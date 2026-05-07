from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from pathlib import Path
from datetime import datetime
import asyncio
import json
import logging
import os
import wave

import httpx
import numpy as np
import uvicorn
from dotenv import load_dotenv
from faster_whisper import WhisperModel

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WHISPER_MODEL     = os.getenv("WHISPER_MODEL",     "small")
WHISPER_DEVICE    = os.getenv("WHISPER_DEVICE",    "cuda")
WHISPER_COMPUTE   = os.getenv("WHISPER_COMPUTE_TYPE", "int8_float16")
WHISPER_LANGUAGE  = os.getenv("WHISPER_LANGUAGE",  "en")
N8N_WEBHOOK_URL   = os.getenv("N8N_WEBHOOK_URL",   "http://localhost:5678/webhook/asr-transcript")
ASR_HOST          = os.getenv("ASR_HOST",          "0.0.0.0")
ASR_PORT          = int(os.getenv("ASR_PORT",      "8002"))

SAVE_DIR = Path("received_audio")
SAVE_DIR.mkdir(exist_ok=True)

app = FastAPI()
MODEL: WhisperModel = None


@app.on_event("startup")
def startup():
    global MODEL
    logger.info(f"Loading Faster-Whisper '{WHISPER_MODEL}' on {WHISPER_DEVICE} ({WHISPER_COMPUTE})...")
    try:
        MODEL = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
        logger.info("Faster-Whisper model loaded.")
    except Exception as e:
        logger.warning(f"CUDA load failed ({e}), falling back to CPU int8.")
        MODEL = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")


def _transcribe_sync(audio_np: np.ndarray) -> str:
    segments, _ = MODEL.transcribe(audio_np, language=WHISPER_LANGUAGE, beam_size=5)
    return " ".join(s.text for s in segments).strip()


async def _post_to_n8n(text: str, epoch: int) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(N8N_WEBHOOK_URL, json={"text": text, "epoch": epoch})
            logger.info(f"Transcript posted to n8n: {text[:80]}")
    except Exception as e:
        logger.warning(f"Failed to post transcript to n8n: {e}")


@app.websocket("/stream")
async def stream_audio(ws: WebSocket):
    """
    Streaming transcription endpoint.

    Protocol:
      Client sends binary frames (int16 PCM, 16 kHz) one chunk at a time,
      then sends a JSON text frame: {"done": true, "epoch": N}
      Server responds with JSON: {"text": "...", "epoch": N}
      Server also fires transcript to n8n webhook asynchronously.
    """
    await ws.accept()
    chunks: list[bytes] = []
    epoch = 0

    try:
        while True:
            message = await ws.receive()
            if "bytes" in message and message["bytes"]:
                chunks.append(message["bytes"])
            elif "text" in message and message["text"]:
                payload = json.loads(message["text"])
                epoch = int(payload.get("epoch", 0))
                break
    except WebSocketDisconnect:
        logger.warning("WebSocket disconnected before done sentinel.")
        return

    if not chunks:
        await ws.send_json({"text": "", "epoch": epoch})
        return

    audio_np = np.frombuffer(b"".join(chunks), dtype=np.int16).astype(np.float32) / 32768.0
    logger.info(f"Transcribing {len(audio_np) / 16000:.1f}s of audio...")

    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(None, _transcribe_sync, audio_np)
    logger.info(f"Transcript: {text}")

    await ws.send_json({"text": text, "epoch": epoch})
    asyncio.create_task(_post_to_n8n(text, epoch))


@app.post("/transcribe")
async def transcribe_http(request: Request):
    """
    Backward-compatible HTTP endpoint. Accepts raw WAV bytes, returns {"text": "..."}.
    Does not post to n8n — caller is responsible for routing the result.
    """
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="No audio data received")

    filepath = SAVE_DIR / (datetime.now().strftime("%Y%m%d-%H%M%S") + ".wav")
    with open(filepath, "wb") as f:
        f.write(data)

    try:
        with wave.open(str(filepath), "rb") as wf:
            nchannels  = wf.getnchannels()
            sampwidth  = wf.getsampwidth()
            framerate  = wf.getframerate()
            nframes    = wf.getnframes()
            logger.info(f"WAV: ch={nchannels} width={sampwidth} sr={framerate} frames={nframes}")
            if nchannels != 1:
                raise ValueError(f"Expected mono, got {nchannels} channels")
            if sampwidth != 2:
                raise ValueError(f"Expected 16-bit PCM, got {sampwidth} bytes/sample")
            if framerate != 16000:
                raise ValueError(f"Expected 16 kHz, got {framerate} Hz")
            pcm_bytes = wf.readframes(nframes)
    except Exception as e:
        logger.error(f"WAV parse error: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid WAV: {e}")

    try:
        audio_np = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, _transcribe_sync, audio_np)
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")

    return {"text": text}


if __name__ == "__main__":
    uvicorn.run(app, host=ASR_HOST, port=ASR_PORT)
