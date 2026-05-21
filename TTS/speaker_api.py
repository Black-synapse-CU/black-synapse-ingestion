"""
Speaker API (Barge-in / Override capable)
----------------------------------------
FastAPI server that accepts WAV/MP3 and plays them via sounddevice + soundfile.
- POST /stop: immediately stop playback
- POST /interrupt: stop playback + increment epoch, returns epoch
- POST /play?epoch=N: only plays if epoch matches current epoch (prevents stale audio)
- Non-blocking playback (play runs in background thread)
"""

from __future__ import annotations

import logging
import os
import platform
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import numpy as np
import openai
import sounddevice as sd
import soundfile as sf
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

OPENAI_TTS_MODEL = os.environ.get("OPENAI_TTS_MODEL", "tts-1")
OPENAI_TTS_VOICE = os.environ.get("OPENAI_TTS_VOICE", "alloy")
# OpenAI PCM stream: 24 kHz, 16-bit mono
_TTS_SAMPLE_RATE = 24000
_TTS_CHUNK_BYTES = _TTS_SAMPLE_RATE * 2 // 20  # 50 ms chunks

SUPPORTED_SUFFIXES = {".wav", ".mp3"}
SUPPORTED_MIME_TYPES = {
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
}
RECEIVED_DIR = Path(os.environ.get("RECEIVED_AUDIO_DIR", "received_audio"))
AUDIO_DEVICE: Optional[str] = os.environ.get("AUDIO_DEVICE") or None

app = FastAPI(title="Kokoro Speaker API", version="3.0.0")

# ---- Barge-in state ----
_state_lock = threading.Lock()
_current_epoch = 0
_current_play_id: Optional[str] = None
_stop_event = threading.Event()


def _ensure_tmp_dir() -> None:
    RECEIVED_DIR.mkdir(parents=True, exist_ok=True)


async def _store_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if not suffix and upload.content_type in SUPPORTED_MIME_TYPES:
        suffix = SUPPORTED_MIME_TYPES[upload.content_type]

    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix or upload.content_type}'. Only .wav or .mp3 are accepted.",
        )

    tmp_path = RECEIVED_DIR / f"{uuid.uuid4().hex}{suffix}"
    with tmp_path.open("wb") as buffer:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            buffer.write(chunk)

    if tmp_path.stat().st_size == 0:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    return tmp_path


async def _store_stream(request: Request) -> Path:
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
    suffix = SUPPORTED_MIME_TYPES.get(content_type)

    if not suffix:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported or missing Content-Type '{content_type}'. "
                   "Use multipart form field 'file' or set Content-Type to audio/wav or audio/mpeg.",
        )

    tmp_path = RECEIVED_DIR / f"{uuid.uuid4().hex}{suffix}"
    bytes_written = 0
    with tmp_path.open("wb") as buffer:
        async for chunk in request.stream():
            if not chunk:
                continue
            buffer.write(chunk)
            bytes_written += len(chunk)

    if bytes_written == 0:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded body is empty.")

    return tmp_path


def _stop_now() -> None:
    """Immediately stop current playback."""
    _stop_event.set()
    try:
        sd.stop()
    except Exception:
        pass


def _background_play(file_path: Path, play_id: str) -> None:
    """
    Play audio in background via sounddevice OutputStream.
    Stops early if /stop called or a new play starts.
    """
    global _current_play_id

    try:
        data, samplerate = sf.read(str(file_path), dtype="float32", always_2d=True)
        _stop_event.clear()

        chunk_frames = int(samplerate * 0.05)  # 50 ms chunks
        pos = 0

        with sd.OutputStream(
            samplerate=samplerate,
            channels=data.shape[1],
            device=AUDIO_DEVICE,
            dtype="float32",
        ) as stream:
            while pos < len(data):
                with _state_lock:
                    still_current = (_current_play_id == play_id)
                if not still_current or _stop_event.is_set():
                    return

                chunk = data[pos : pos + chunk_frames]
                stream.write(chunk)
                pos += chunk_frames

    except Exception as e:
        import traceback
        logging.error(f"[background_play] ERROR: {e}\n{traceback.format_exc()}")
        try:
            sd.stop()
        except Exception:
            pass
    finally:
        try:
            file_path.unlink(missing_ok=True)
        except Exception:
            pass


@app.on_event("startup")
def startup_event() -> None:
    _ensure_tmp_dir()
    logging.info(f"Audio device: {AUDIO_DEVICE!r}")
    logging.info(f"Available devices: {sd.query_devices()}")


@app.get("/healthz")
async def healthz():
    with _state_lock:
        epoch = _current_epoch
        play_id = _current_play_id
    return {
        "status": "ok",
        "player": "sounddevice",
        "platform": platform.system(),
        "audio_device": AUDIO_DEVICE,
        "epoch": epoch,
        "playing": play_id is not None,
    }


@app.post("/stop")
async def stop_audio():
    """Stop current playback immediately."""
    _stop_now()
    global _current_play_id
    with _state_lock:
        _current_play_id = None
    return JSONResponse({"status": "stopped"})


@app.post("/interrupt")
async def interrupt():
    """Barge-in: stops playback and increments epoch."""
    _stop_now()

    global _current_epoch, _current_play_id
    with _state_lock:
        _current_epoch += 1
        _current_play_id = None
        epoch = _current_epoch

    return JSONResponse({"status": "interrupted", "epoch": epoch})


@app.post("/play")
async def play_audio(
    background_tasks: BackgroundTasks,
    request: Request,
    file: Optional[UploadFile] = File(None),
    epoch: int = Query(..., description="Epoch token returned by /interrupt. Must match current epoch."),
):
    """
    Plays audio only if epoch matches current epoch.
    Non-blocking: returns immediately while audio plays in background.
    """
    if file is not None:
        stored_path = await _store_upload(file)
    else:
        stored_path = await _store_stream(request)

    with _state_lock:
        if epoch != _current_epoch:
            stored_path.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail=f"Stale audio (epoch={epoch}, current={_current_epoch}).")

        global _current_play_id
        play_id = uuid.uuid4().hex
        _current_play_id = play_id

    background_tasks.add_task(_background_play, stored_path, play_id)
    return JSONResponse({"status": "playing", "epoch": epoch, "play_id": play_id})


class SpeakRequest(BaseModel):
    text: str
    epoch: int
    voice: str = OPENAI_TTS_VOICE
    model: str = OPENAI_TTS_MODEL


def _stream_and_play(text: str, voice: str, model: str, play_id: str) -> None:
    """Stream OpenAI TTS PCM directly to the audio device, playing as chunks arrive."""
    _stop_event.clear()
    client = openai.OpenAI(api_key=os.environ["OPEN_AI_KEY"])

    t_start = time.perf_counter()
    t_first_chunk: Optional[float] = None

    try:
        with sd.OutputStream(
            samplerate=_TTS_SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=AUDIO_DEVICE,
        ) as stream:
            with client.audio.speech.with_streaming_response.create(
                model=model,
                voice=voice,
                input=text,
                response_format="pcm",
            ) as response:
                buf = b""
                for raw in response.iter_bytes(_TTS_CHUNK_BYTES):
                    if t_first_chunk is None:
                        t_first_chunk = time.perf_counter()
                        logging.info(f"[TTS] first chunk in {t_first_chunk - t_start:.3f}s")
                    with _state_lock:
                        still_current = (_current_play_id == play_id)
                    if not still_current or _stop_event.is_set():
                        return
                    buf += raw
                    while len(buf) >= _TTS_CHUNK_BYTES:
                        pcm = buf[:_TTS_CHUNK_BYTES]
                        buf = buf[_TTS_CHUNK_BYTES:]
                        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                        stream.write(audio.reshape(-1, 1))
                if buf and len(buf) >= 2:
                    audio = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
                    stream.write(audio.reshape(-1, 1))
        logging.info(f"[TTS] done in {time.perf_counter() - t_start:.3f}s total")
    except Exception as e:
        logging.error(f"[stream_and_play] {e}")


@app.post("/speak")
async def speak(body: SpeakRequest, background_tasks: BackgroundTasks):
    """
    Accepts text + epoch, streams OpenAI TTS PCM to the audio device in real-time.
    First audio chunk plays in ~400-600 ms. Respects epoch for barge-in protection.
    """
    global _current_play_id
    with _state_lock:
        if body.epoch != _current_epoch:
            raise HTTPException(
                status_code=409,
                detail=f"Stale request (epoch={body.epoch}, current={_current_epoch}).",
            )
        play_id = uuid.uuid4().hex
        _current_play_id = play_id

    background_tasks.add_task(_stream_and_play, body.text, body.voice, body.model, play_id)
    return JSONResponse({"status": "streaming", "play_id": play_id})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "speaker_api:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8001")),
        reload=False,
    )
