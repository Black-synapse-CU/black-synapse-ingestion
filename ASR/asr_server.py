from fastapi import FastAPI, Request, HTTPException
from pathlib import Path
from datetime import datetime
import logging
import uvicorn
import wave
import whisper
import numpy as np  # make sure: pip install numpy

app = FastAPI()

SAVE_DIR = Path("received_audio")
SAVE_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)

MODEL = whisper.load_model("base")  # or "tiny", "small", etc.


@app.post("/transcribe")
async def transcribe(request: Request):
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="No audio data received")

    filepath = SAVE_DIR / (datetime.now().strftime("%Y%m%d-%H%M%S") + ".wav")
    with open(filepath, "wb") as f:
        f.write(data)

    try:
        with wave.open(str(filepath), "rb") as wf:
            nchannels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()

            logging.info(
                f"Received WAV: ch={nchannels}, width={sampwidth}, "
                f"sr={framerate}, frames={nframes}"
            )

            if nchannels != 1:
                raise ValueError(f"Expected mono audio, got {nchannels} channels")
            if sampwidth != 2:
                raise ValueError(f"Expected 16-bit PCM (2 bytes), got {sampwidth}")
            if framerate != 16000:
                raise ValueError(f"Expected 16 kHz sample rate, got {framerate}")

            pcm_bytes = wf.readframes(nframes)

    except Exception as e:
        logging.error(f"WAV parse error: {e}", exc_info=True)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or unsupported WAV audio: {e}",
        )

    try:
        audio_np = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        result = MODEL.transcribe(audio_np, language="en")  # or omit language to auto-detect
        text = result.get("text", "").strip()
    except Exception as e:
        logging.error(f"Transcription error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Whisper transcription failed: {e}")

    return {"text": text}


if __name__ == "__main__":
    # For debugging audio, keep it simple (single process, no reload)
    uvicorn.run(app, host="0.0.0.0", port=8002)