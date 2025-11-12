from fastapi import FastAPI, Request, HTTPException
import os, platform, subprocess
from datetime import datetime
import uvicorn, wave
from pathlib import Path
import winsound

app = FastAPI()
SAVE_DIR = Path("received_audio"); SAVE_DIR.mkdir(exist_ok=True)

def play_wav_windows(path: Path, async_mode: bool = False):
    flags = winsound.SND_FILENAME | winsound.SND_NODEFAULT
    if async_mode:
        flags |= winsound.SND_ASYNC
    winsound.PlaySound(str(path), flags)

def play_wav_linux(path: Path):
    subprocess.run(["aplay", str(path)], check=True)

@app.post("/play")
async def play(request: Request):
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="No audio data received")

    # save
    filepath = SAVE_DIR / (datetime.now().strftime("%Y%m%d-%H%M%S") + ".wav")
    with open(filepath, "wb") as f:
        f.write(data)

    # validate header + log params
    try:
        with wave.open(str(filepath), "rb") as wf:
            nch, sampwidth, fr, nframes, comptype, compname = wf.getparams()
    except Exception as e:
        return {"status": "saved_invalid_wav", "file": str(filepath), "error": f"{e}"}

    info = {
        "status": "saved",
        "file": str(filepath.resolve()),
        "wav_params": {
            "channels": nch, "sample_width_bytes": sampwidth,
            "sample_rate_hz": fr, "frames": nframes,
            "comptype": comptype, "compname": compname
        }
    }

    # play (blocking once, to reveal errors clearly)
    try:
        if platform.system().lower().startswith("win"):
            # First try blocking (easier to debug)
            play_wav_windows(filepath, async_mode=False)
        else:
            play_wav_linux(filepath)
        info["playback"] = "ok"
    except Exception as e:
        info["playback"] = f"failed: {e}"

    return info

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)