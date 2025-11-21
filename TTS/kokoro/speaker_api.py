from fastapi import FastAPI, Request, HTTPException
from datetime import datetime
from pathlib import Path
import logging
import os
import uvicorn
import wave
import pygame

app = FastAPI()

SAVE_DIR = Path("received_audio")
SAVE_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)


ALLOW_DUMMY_AUDIO = os.getenv("ALLOW_DUMMY_AUDIO", "0").lower() in {
    "1",
    "true",
    "yes",
}

_mixer_ready = False
_using_dummy_driver = False


def _initialize_mixer():
    global _mixer_ready, _using_dummy_driver

    if _mixer_ready:
        return

    try:
        pygame.mixer.init()
        _mixer_ready = True
        _using_dummy_driver = False
        logging.info("pygame.mixer initialized successfully")
        return
    except pygame.error as e1:
        logging.error(
            "Primary mixer initialization failed. Ensure /dev/snd is passed through "
            "and the host audio device is accessible.",
            exc_info=True,
        )
        if not ALLOW_DUMMY_AUDIO:
            raise RuntimeError(
                "Audio device unavailable. Set ALLOW_DUMMY_AUDIO=1 to bypass playback "
                "or expose the host sound device to the container."
            ) from e1

    # Attempt fallback to SDL dummy driver so container can still boot if explicitly allowed
    previous_driver = os.environ.get("SDL_AUDIODRIVER")
    if previous_driver != "dummy":
        os.environ["SDL_AUDIODRIVER"] = "dummy"

    try:
        pygame.mixer.init()
        _mixer_ready = True
        _using_dummy_driver = True
        logging.warning(
            "pygame.mixer initialized with SDL dummy driver (no audio output)"
        )
    except pygame.error as e2:
        raise RuntimeError(
            "Could not initialize pygame.mixer even with SDL dummy driver"
        ) from e2


@app.on_event("startup")
def init_audio():
    """
    Initialize pygame mixer once when the app starts.
    """
    _initialize_mixer()


def play_with_pygame(path: Path):
    """
    Play a WAV file using pygame on any platform.
    """
    if not _mixer_ready:
        _initialize_mixer()

    if not _mixer_ready:
        logging.warning("Skipping playback; mixer still unavailable")
        return

    try:
        # Load sound
        sound = pygame.mixer.Sound(str(path))
        # Play non-blocking; mixer runs in background
        sound.play()
        if _using_dummy_driver:
            logging.info(
                "Playback invoked but SDL dummy driver is active; no audio will play."
            )
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