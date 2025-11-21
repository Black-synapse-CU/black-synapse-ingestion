"""
Speaker API
-----------
Runs a lightweight FastAPI server that accepts uploaded WAV or MP3 files and
plays them inside the container using any available CLI audio player.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import List, Sequence

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

SUPPORTED_SUFFIXES = {".wav", ".mp3"}
SUPPORTED_MIME_TYPES = {
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
}
RECEIVED_DIR = Path(os.environ.get("RECEIVED_AUDIO_DIR", "received_audio"))
PLAYER_ENV = os.environ.get("SPEAKER_PLAYER", "").strip()

PLAYER_CANDIDATES: List[List[str]] = [
    ["ffplay", "-nodisp", "-autoexit"],
    ["aplay"],
    ["paplay"],
]

FFMPEG_CMD = shutil.which("ffmpeg")

app = FastAPI(title="Kokoro Speaker API", version="1.0.0")

_player_cmd: List[str] | None = None


def _split_env_cmd(cmd_str: str) -> List[str]:
    """Split SPEAKER_PLAYER env command respecting quotes."""
    import shlex

    return shlex.split(cmd_str)


def _resolve_player() -> List[str]:
    """Determine which playback command is available inside the container."""
    candidates: Sequence[List[str]]

    if PLAYER_ENV:
        candidates = [_split_env_cmd(PLAYER_ENV)]
    else:
        candidates = PLAYER_CANDIDATES

    for candidate in candidates:
        if shutil.which(candidate[0]):
            return list(candidate)

    raise RuntimeError(
        "No audio player found. Install ffmpeg (for ffplay) or alsa-utils (aplay) "
        "or provide SPEAKER_PLAYER env var."
    )


def _ensure_tmp_dir() -> None:
    RECEIVED_DIR.mkdir(parents=True, exist_ok=True)


def _needs_wav_conversion(player_cmd: Sequence[str], suffix: str) -> bool:
    return player_cmd and player_cmd[0] in {"aplay"} and suffix == ".mp3"


def _convert_to_wav(source: Path) -> Path:
    if not FFMPEG_CMD:
        raise RuntimeError(
            "MP3 playback requires ffmpeg when using aplay. Install ffmpeg "
            "or set SPEAKER_PLAYER to ffplay."
        )

    wav_path = source.with_suffix(".wav")
    subprocess.run(
        [FFMPEG_CMD, "-y", "-i", str(source), str(wav_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    return wav_path


async def _store_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if not suffix and upload.content_type in SUPPORTED_MIME_TYPES:
        suffix = SUPPORTED_MIME_TYPES[upload.content_type]

    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix or upload.content_type}'. "
            "Only .wav or .mp3 are accepted.",
        )

    tmp_name = f"{uuid.uuid4().hex}{suffix}"
    tmp_path = RECEIVED_DIR / tmp_name

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
            "Use multipart form field 'file' or set Content-Type to "
            "audio/wav or audio/mpeg.",
        )

    tmp_name = f"{uuid.uuid4().hex}{suffix}"
    tmp_path = RECEIVED_DIR / tmp_name

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


def _play_file(file_path: Path, player_cmd: Sequence[str]) -> None:
    target_path = file_path
    converted_path: Path | None = None

    if _needs_wav_conversion(player_cmd, file_path.suffix.lower()):
        converted_path = _convert_to_wav(file_path)
        target_path = converted_path

    try:
        subprocess.run(
            list(player_cmd) + [str(target_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"Playback failed: {exc}") from exc
    finally:
        if converted_path and converted_path.exists():
            converted_path.unlink(missing_ok=True)


@app.on_event("startup")
def startup_event() -> None:
    global _player_cmd
    _ensure_tmp_dir()
    _player_cmd = _resolve_player()


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "player": _player_cmd[0] if _player_cmd else None}


@app.post("/play")
async def play_audio(request: Request, file: UploadFile | None = File(None)):
    if _player_cmd is None:
        raise HTTPException(status_code=503, detail="Audio player not ready")

    if file is not None:
        stored_path = await _store_upload(file)
    else:
        stored_path = await _store_stream(request)

    try:
        _play_file(stored_path, _player_cmd)
    finally:
        stored_path.unlink(missing_ok=True)

    return JSONResponse({"status": "played"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "speaker_api:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8001")),
        reload=False,
    )

