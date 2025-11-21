from fastapi import FastAPI, Request, HTTPException
from datetime import datetime
from pathlib import Path
import logging
import os
import threading
import uvicorn
import wave
import wx

app = FastAPI()

SAVE_DIR = Path("received_audio")
SAVE_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)


ALLOW_DUMMY_AUDIO = os.getenv("ALLOW_DUMMY_AUDIO", "0").lower() in {"1", "true", "yes"}

_wx_app = None
_wx_ready = False
_audio_silent = False
_playing_sounds = set()
_playing_lock = threading.Lock()


def _initialize_mixer():
    global _wx_app, _wx_ready, _audio_silent

    if _wx_ready or _audio_silent:
        return

    try:
        _wx_app = wx.App(False)
        _wx_ready = True
        logging.info("wxPython audio backend initialized successfully")
    except Exception as exc:  # pylint: disable=broad-except
        logging.error(
            "Could not initialize wxPython audio backend. Ensure the container has "
            "access to an audio device and (on Linux) a DISPLAY.",
            exc_info=True,
        )
        if not ALLOW_DUMMY_AUDIO:
            raise RuntimeError(
                "Audio device unavailable. Set ALLOW_DUMMY_AUDIO=1 to bypass playback "
                "or expose the host sound device to the container."
            ) from exc
        _audio_silent = True
        logging.warning("Continuing without audio output (ALLOW_DUMMY_AUDIO=1)")


@app.on_event("startup")
def init_audio():
    """
    Initialize pygame mixer once when the app starts.
    """
    _initialize_mixer()


def play_with_wx(path: Path):
    """
    Play a WAV file using wxPython on any platform.
    """
    if _audio_silent:
        logging.info("Audio playback is disabled; skipping wx.Sound playback.")
        return

    if not _wx_ready:
        _initialize_mixer()

    if not _wx_ready:
        logging.warning("Skipping playback; wx backend is unavailable.")
        return

    try:
        sound = wx.Sound(str(path))
        if not sound.IsOk():
            raise RuntimeError(f"wx.Sound could not load file {path}")

        played = sound.Play(wx.SOUND_ASYNC)
        if not played:
            raise RuntimeError("wx.Sound.Play returned False")

        duration_ms = sound.GetLength()
        with _playing_lock:
            _playing_sounds.add(sound)

        cleanup_delay = (duration_ms / 1000.0) + 1 if duration_ms > 0 else 5
        timer = threading.Timer(
            cleanup_delay,
            lambda: _remove_sound(sound),
        )
        timer.daemon = True
        timer.start()
    except Exception as e:  # pylint: disable=broad-except
        logging.error(f"Playback error with wx.Sound: {e}", exc_info=True)
        raise


def _remove_sound(sound: wx.Sound):
    with _playing_lock:
        _playing_sounds.discard(sound)


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

    # playback via wxPython
    try:
        play_with_wx(filepath)
        info["playback"] = "ok"
    except Exception as e:
        info["playback"] = f"failed: {e}"

    return info


if __name__ == "__main__":
    # For debugging audio, keep it simple (single process, no reload)
    uvicorn.run(app, host="0.0.0.0", port=8001)