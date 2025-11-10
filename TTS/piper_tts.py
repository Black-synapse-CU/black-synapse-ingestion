import tempfile
import os
import wave
import platform
import subprocess
import traceback

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from piper import PiperVoice
import uvicorn

app = FastAPI()

MODEL_PATH =  r"C:\Users\Elyas\OneDrive - The University of Colorado Denver\Desktop\projects\black-synapse-ingestion\TTS\en_US-lessac-medium.onnx"

VOICE: PiperVoice | None = None


class TTSRequest(BaseModel):
    text: str


def play_wav(path: str):
    system = platform.system().lower()
    if "windows" in system:
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME)
    else:
        subprocess.run(["aplay", path], check=True)


@app.on_event("startup")
def load_voice():
    global VOICE
    try:
        use_cuda = True
        VOICE = PiperVoice.load(MODEL_PATH, use_cuda=use_cuda)
        print(f"Piper model loaded on {'GPU' if use_cuda else 'CPU'}")
    except Exception as e:
        print("Failed to load Piper model:", e)
        traceback.print_exc()
        # let it start anyway, but endpoints will 500
        VOICE = None


@app.post("/tts")
def tts(req: TTSRequest):
    if VOICE is None:
        raise HTTPException(status_code=500, detail="Voice model not loaded")

    # make temp wav
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        tmp_path = tmp.name

    # synthesize
    try:
        with wave.open(tmp_path, "wb") as wav_file:
            VOICE.synthesize_wav(req.text, wav_file)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        print("TTS failed:", e)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"TTS failed: {e}")

    # play
    try:
        play_wav(tmp_path)
    except Exception as e:
        os.remove(tmp_path)
        print("Playback failed:", e)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Playback failed: {e}")

    os.remove(tmp_path)
    return {"status": "played", "text": req.text}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)