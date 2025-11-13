from fastapi import FastAPI, Request, HTTPException
from datetime import datetime
from pathlib import Path
import logging
import uvicorn
import wave
import pygame

app = FastAPI()

SAVE_DIR = Path("received_audio")
SAVE_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)


@app.on_event("startup")
def init_audio():
    """
    Initialize pygame mixer once when the app starts.
    """
    try:
        pygame.mixer.init()  # you can pass frequency, size, channels, buffer if needed
        logging.info("pygame.mixer initialized successfully")
    except Exception as e:
        logging.error(f"Failed to initialize pygame.mixer: {e}", exc_info=True)


def play_with_pygame(path: Path):
    """
    Play a WAV file using pygame on any platform.
    """
    try:
        # Load sound
        sound = pygame.mixer.Sound(str(path))
        # Play non-blocking; mixer runs in background
        sound.play()
        # If you REALLY want to block until done:
        # while pygame.mixer.get_busy():
        #     pygame.time.wait(50)
    except Exception as e:
        logging.error(f"Playback error with pygame: {e}", exc_info=True)
        raise


@app.post("/play")
async def play(request: Request):
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="No audio data received")

    # save file
    filepath = SAVE_DIR / (datetime.now().strftime("%Y%m%d-%H%M%S") + ".wav")
    with open(filepath, "wb") as f:
        f.write(data)

    # validate header + log params
    try:
        with wave.open(str(filepath), "rb") as wf:
            nch, sampwidth, fr, nframes, comptype, compname = wf.getparams()
    except Exception as e:
        return {
            "status": "saved_invalid_wav",
            "file": str(filepath),
            "error": f"{e}",
        }

    info = {
        "status": "saved",
        "file": str(filepath.resolve()),
        "wav_params": {
            "channels": nch,
            "sample_width_bytes": sampwidth,
            "sample_rate_hz": fr,
            "frames": nframes,
            "comptype": comptype,
            "compname": compname,
        },
    }

    # playback via pygame
    try:
        play_with_pygame(filepath)
        info["playback"] = "ok"
    except Exception as e:
        info["playback"] = f"failed: {e}"

    return info


if __name__ == "__main__":
    # For debugging audio, keep it simple (single process, no reload)
    uvicorn.run(app, host="0.0.0.0", port=8001)