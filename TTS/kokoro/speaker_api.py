from fastapi import FastAPI, Request, HTTPException
import os
from datetime import datetime
import uvicorn

app = FastAPI()

SAVE_DIR = "received_audio"
os.makedirs(SAVE_DIR, exist_ok=True)

@app.post("/play")
async def play(request: Request):
    # really this is "upload", but we'll keep your route name
    audio_bytes = await request.body()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="No audio data received")

    # make a filename with timestamp
    filename = datetime.now().strftime("%Y%m%d-%H%M%S") + ".wav"
    filepath = os.path.join(SAVE_DIR, filename)

    # save it
    with open(filepath, "wb") as f:
        f.write(audio_bytes)

    return {"status": "saved", "file": filepath, "size": len(audio_bytes)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)