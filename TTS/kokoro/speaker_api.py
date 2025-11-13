from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
import subprocess
import tempfile
from pathlib import Path
import os
import uvicorn

app = FastAPI()

# Configure whisper paths
WHISPER_BIN = os.getenv("WHISPER_BIN", r"C:\tools\whisper.cpp\main.exe")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", r"C:\tools\whisper.cpp\models\ggml-base.en.bin")

WHISPER_ARGS = [
    "-l", "en",  # force English
    "-nt",       # no timestamps
]


def run_whisper(wav_path: Path) -> str:
    if not Path(WHISPER_BIN).exists():
        raise RuntimeError(f"Whisper binary not found: {WHISPER_BIN}")

    if not Path(WHISPER_MODEL).exists():
        raise RuntimeError(f"Model not found: {WHISPER_MODEL}")

    cmd = [
        str(WHISPER_BIN),
        "-m", str(WHISPER_MODEL),
        "-f", str(wav_path),
        *WHISPER_ARGS
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(f"whisper.cpp failed:\n{result.stderr}")

    return result.stdout.strip()


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".wav"):
        raise HTTPException(status_code=400, detail="Only .wav files are supported")

    # Save temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        wav_path = Path(tmp.name)
        content = await file.read()
        tmp.write(content)

    try:
        text = run_whisper(wav_path)
    finally:
        try:
            wav_path.unlink()
        except:
            pass

    return JSONResponse({"text": text})


if __name__ == "__main__":
    # For debugging audio, keep it simple (single process, no reload)
    uvicorn.run(app, host="0.0.0.0", port=8001)