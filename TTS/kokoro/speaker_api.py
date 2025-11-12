from fastapi import FastAPI, Request, HTTPException
import subprocess, sys, shutil, os, uvicorn

app = FastAPI()

def play_bytes(audio_bytes: bytes):
    """Try ffplay → mpg123 → fallback (playsound/os)."""
    # 1) ffplay can read from stdin
    if shutil.which("ffplay"):
        subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).stdin.write(audio_bytes)
        return

    # 2) mpg123 can too
    if shutil.which("mpg123"):
        subprocess.Popen(
            ["mpg123", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).stdin.write(audio_bytes)
        return

    # 3) Fallback to playsound/os (requires a file)
    try:
        from playsound import playsound
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        playsound(tmp_path)
        os.remove(tmp_path)
    except Exception:
        # 4) OS default app
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        if os.name == "nt":
            os.startfile(tmp_path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", tmp_path])
        else:
            subprocess.Popen(["xdg-open", tmp_path])


@app.post("/play")
async def play(request: Request):
    audio_bytes = await request.body()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="No audio data received")

    play_bytes(audio_bytes)
    return {"status": "playing", "bytes": len(audio_bytes)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)