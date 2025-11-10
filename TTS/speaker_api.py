from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.responses import JSONResponse
import base64
import subprocess
import tempfile
import os

app = FastAPI()
PLAYER_CMD = ["aplay"]  # or ["ffplay", "-nodisp", "-autoexit"]

# Expected request body:
# { "data": "<base64 or raw string of wav bytes>" }
class AudioData(BaseModel):
    data: str

@app.post("/incoming-audio")
async def incoming_audio(payload: AudioData):
    """
    Accepts a JSON body with 'data' containing base64-encoded audio.
    Example body: { "data": "<base64 string>" }
    """
    # Try base64 decode
    try:
        audio_bytes = base64.b64decode(payload.data)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 audio data")

    # Save bytes to a temporary WAV file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    # Play the audio
    try:
        subprocess.run(PLAYER_CMD + [tmp_path], check=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Playback failed: {e}")
    finally:
        os.remove(tmp_path)

    return JSONResponse({"status": "played"})