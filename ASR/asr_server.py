from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List
import subprocess
from pathlib import Path
import os
import uvicorn

app = FastAPI()

# Configure paths to whisper.cpp and model
WHISPER_BIN = os.getenv("WHISPER_BIN", "/usr/local/bin/whisper")  # e.g. /home/elyas/whisper.cpp/main
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "/models/ggml-base.en.bin")

# Extra default args (tweak as you like)
WHISPER_ARGS = [
    "-l", "en",  # force English; remove/change if you want auto or other language
    "-nt"        # no timestamps; remove if you WANT timestamps in output
]


class FileEvent(BaseModel):
    event: str
    path: str


@app.post("/transcribe")
async def transcribe(events: List[FileEvent]):
    """
    Accepts a list of file change events like:
    [
      {
        "event": "change",
        "path": "/data/asr/utterance.wav"
      }
    ]

    For each event, runs whisper.cpp on the given path and returns transcriptions.
    """
    if not events:
        raise HTTPException(status_code=400, detail="No events provided")

    results = []

    for ev in events:
        audio_path = Path(ev.path)

        if not audio_path.suffix.lower() == ".wav":
            # You can relax this if you want to support other formats
            raise HTTPException(
                status_code=400,
                detail=f"Only .wav files are supported (got: {audio_path})"
            )

        if not audio_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Audio file not found at path: {audio_path}"
            )

        try:
            text = run_whisper_on_wav(audio_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        results.append({
            "event": ev.event,
            "path": ev.path,
            "text": text
        })

    # If you only ever send one event, this will just be a single-item list
    return JSONResponse(results)


def run_whisper_on_wav(wav_path: Path) -> str:
    """
    Call whisper.cpp on a WAV file and return the transcribed text.
    """
    if not Path(WHISPER_BIN).exists():
        raise RuntimeError(f"whisper binary not found at {WHISPER_BIN}")

    if not Path(WHISPER_MODEL).exists():
        raise RuntimeError(f"whisper model not found at {WHISPER_MODEL}")

    cmd = [
        str(WHISPER_BIN),
        "-m", str(WHISPER_MODEL),
        "-f", str(wav_path),
        *WHISPER_ARGS,
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except Exception as e:
        raise RuntimeError(f"Failed to run whisper.cpp: {e}")

    if result.returncode != 0:
        # Log stderr somewhere if you want more debugging
        raise RuntimeError(
            f"whisper.cpp returned {result.returncode}.\nSTDERR:\n{result.stderr}"
        )

    return result.stdout.strip()

if __name__ == "__main__":
    # For debugging audio, keep it simple (single process, no reload)
    uvicorn.run(app, host="0.0.0.0", port=8002)