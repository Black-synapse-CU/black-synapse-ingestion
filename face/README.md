# Atlas Face Display

Pygame-based robot face running on the Raspberry Pi's 3.5" touchscreen (480x320).  
Receives state updates from the Jetson over the local network via HTTP.

## States

| State       | Eyes         | Bottom                        |
|-------------|--------------|-------------------------------|
| `idle`      | blink        | subtle smile                  |
| `listening` | wide open    | pulse rings + waveform bars   |
| `echo`      | blink        | "I heard:" + transcript text  |
| `thinking`  | squinted     | bouncing dots                 |

## Setup (on the Pi)

```bash
cd face
pip install -r requirements.txt
python face_service.py
```

Runs on port `8003`. Set `FACE_FB=/dev/fb0` if on HDMI, `/dev/fb1` for SPI screen (default).

## Setup (on the Jetson)

1. Find the Pi's IP:
   ```bash
   # run on the Pi
   hostname -I
   ```

2. Add to `.env` on the Jetson:
   ```
   FACE_SERVICE_URL=http://192.168.1.XX:8003
   ```

3. Restart the stack:
   ```bash
   docker compose up -d
   ```

4. Re-import `n8n/workflows/MainWorkflow.json` into n8n (open workflow → `...` → Import from file → Save).

## How it connects

```
Jetson                                    Pi (same LAN)
─────────────────────────────────────     ──────────────────────
wake_word.py   → POST /face/state         face_service.py :8003
n8n MainWorkflow (4 HTTP Request nodes)        └── pygame screen
```

n8n Docker containers reach the Pi directly via LAN IP — no tunnels needed.

## Signal flow

```
wake word fires    → wake_word.py     → listening  (immediate, before n8n)
WAV transcribed    → n8n after ASR    → echo + transcript
LLM processing     → n8n after Enrich → thinking   (parallel branch)
Speaker done       → n8n after Speaker → idle
```

All 4 n8n face nodes have `continueOnFail: true` — if the Pi is offline, the conversation keeps working.

## API

```
POST /face/state   {"state": "listening"}
POST /face/state   {"state": "echo", "text": "turn on the lights"}
POST /face/state   {"state": "thinking"}
POST /face/state   {"state": "idle"}
GET  /face/state   → current state
```

## Environment variables

| Variable         | Default                        | Purpose                        |
|------------------|--------------------------------|--------------------------------|
| `FACE_PORT`      | `8003`                         | Port the service listens on    |
| `FACE_FB`        | `/dev/fb1`                     | Framebuffer device (SPI screen)|
