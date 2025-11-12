from fastapi import FastAPI, Request, HTTPException
import uvicorn
import tempfile
import os
import subprocess
import traceback

app = FastAPI()

def play_mp3(path: str):
    """Play an mp3 file from disk, then return."""
    if os.name == "nt":  # Windows
        os.startfile(path)
    else:  # Linux / Jetson
        # needs: sudo apt install mpg123  (or use ffplay below)
        try:
            subprocess.run(["mpg123", path], check=True)
        except FileNotFoundError:
            # fallback to ffplay if mpg123 not installed
            subprocess.run(["ffplay", "-nodisp", "-autoexit", path], check=True)


@app.post("/play")
async def play(request: Request):
    # read raw mp3 bytes from body
    audio_bytes = await request.body()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="No audio data received")

    # make temp mp3
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
        tmp_path = tmp.name
        tmp.write(audio_bytes)

    # play
    try:
        play_mp3(tmp_path)
    except Exception as e:
        # clean up
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        print("Playback failed:", e)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Playback failed: {e}")

    # delete after successful play
    os.remove(tmp_path)
    return {"status": "played", "bytes": len(audio_bytes)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)