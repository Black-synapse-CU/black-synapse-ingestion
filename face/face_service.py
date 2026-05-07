import math
import os
import random
import sys
import threading
import time

import pygame
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

# ── Display ────────────────────────────────────────────────────────────
WIDTH, HEIGHT = 480, 320
FPS = 30

# ── Colors ─────────────────────────────────────────────────────────────
BG    = (10,  10,  10)
CYAN  = (0,  220, 255)
WHITE = (220, 220, 220)
GRAY  = (80,  80,  80)
BLUE  = (60,  80, 220)

# ── Eye geometry ───────────────────────────────────────────────────────
EYE_L  = (155, 148)
EYE_R  = (325, 148)
EYE_W  = 38
EYE_H  = 38

# ── Shared state ────────────────────────────────────────────────────────
_lock       = threading.Lock()
_face_state = {"state": "idle", "text": ""}

# ── API ────────────────────────────────────────────────────────────────
api = FastAPI(title="Atlas Face")


class StateUpdate(BaseModel):
    state: str  # idle | listening | echo | thinking
    text: str = ""


@api.post("/face/state")
def set_state(u: StateUpdate):
    with _lock:
        _face_state["state"] = u.state
        _face_state["text"]  = u.text
    return {"ok": True}


@api.get("/face/state")
def get_state():
    with _lock:
        return dict(_face_state)


def _run_api():
    port = int(os.getenv("FACE_PORT", "8003"))
    uvicorn.run(api, host="0.0.0.0", port=port, log_level="warning")


# ── Helpers ────────────────────────────────────────────────────────────
def _draw_eye(surf, center, rx, ry, color=CYAN):
    if ry < 1:
        return
    rect = pygame.Rect(center[0] - rx, center[1] - ry, rx * 2, ry * 2)
    pygame.draw.ellipse(surf, color, rect)


def _wrap_text(text, font, max_width):
    words = text.split()
    lines, current = [], []
    for word in words:
        test = " ".join(current + [word])
        if font.size(test)[0] <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


# ── Main loop ──────────────────────────────────────────────────────────
def main():
    # SDL framebuffer target — set FACE_FB=/dev/fb0 if on HDMI, /dev/fb1 for SPI
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        os.environ.setdefault("SDL_VIDEODRIVER", "fbcon")
        os.environ.setdefault("SDL_FBDEV", os.getenv("FACE_FB", "/dev/fb1"))

    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.FULLSCREEN)
    pygame.display.set_caption("Atlas Face")
    pygame.mouse.set_visible(False)
    clock = pygame.time.Clock()

    font_sm = pygame.font.SysFont("monospace", 16)
    font_md = pygame.font.SysFont("monospace", 22)

    # Blink
    BLINK_DUR  = 0.14
    blink_t    = 0.0
    blink_next = time.time() + random.uniform(2.5, 5.0)
    blink_ry   = float(EYE_H)

    # Listening pulse ring
    pulse_r = 0.0

    # Listening waveform bars
    wave_bars = [random.uniform(0.2, 0.6) for _ in range(10)]
    wave_t    = 0.0

    # Thinking dots
    dot_phase = 0.0

    threading.Thread(target=_run_api, daemon=True).start()

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False

        with _lock:
            state = _face_state["state"]
            text  = _face_state["text"]

        screen.fill(BG)
        now = time.time()

        # ── Blink (idle + echo only) ───────────────────────────────────
        if state in ("idle", "echo"):
            if now >= blink_next and blink_t == 0.0:
                blink_t = BLINK_DUR
            if blink_t > 0.0:
                blink_t = max(0.0, blink_t - dt)
                p = 1.0 - (blink_t / BLINK_DUR)   # 0 → 1
                half = 0.5
                if p < half:
                    blink_ry = EYE_H * (1.0 - p / half)
                else:
                    blink_ry = EYE_H * ((p - half) / half)
                if blink_t == 0.0:
                    blink_ry = float(EYE_H)
                    blink_next = now + random.uniform(2.5, 5.0)
            else:
                blink_ry = float(EYE_H)
        else:
            blink_t  = 0.0
            blink_ry = float(EYE_H)

        # ── Idle ───────────────────────────────────────────────────────
        if state == "idle":
            _draw_eye(screen, EYE_L, EYE_W, int(blink_ry))
            _draw_eye(screen, EYE_R, EYE_W, int(blink_ry))
            pygame.draw.arc(
                screen, CYAN,
                pygame.Rect(195, 212, 90, 32),
                math.pi, 2 * math.pi, 3,
            )

        # ── Listening ──────────────────────────────────────────────────
        elif state == "listening":
            _draw_eye(screen, EYE_L, EYE_W + 6, EYE_H + 6)
            _draw_eye(screen, EYE_R, EYE_W + 6, EYE_H + 6)

            # Single slow ring rising from below
            pulse_r = (pulse_r + dt * 60) % 110
            alpha = max(0.0, 1.0 - pulse_r / 110)
            pc = tuple(int(v * alpha) for v in CYAN)
            if pulse_r > 2 and any(pc):
                pygame.draw.circle(screen, pc, (WIDTH // 2, 270), int(pulse_r + 10), 2)

            # Smooth waveform — 10 bars, gentle movement
            wave_t += dt
            if wave_t > 0.1:
                wave_bars = [
                    max(0.1, min(1.0, b + random.uniform(-0.15, 0.15)))
                    for b in wave_bars[:10]
                ] + wave_bars[10:]
                wave_t = 0.0
            bar_w   = 18
            gap     = 6
            n_bars  = 10
            total_w = n_bars * (bar_w + gap)
            sx      = (WIDTH - total_w) // 2
            for i, h in enumerate(wave_bars[:n_bars]):
                bh = int(h * 28)
                pygame.draw.rect(
                    screen, CYAN,
                    (sx + i * (bar_w + gap), 300 - bh, bar_w, bh),
                )

        # ── Echo ───────────────────────────────────────────────────────
        elif state == "echo":
            _draw_eye(screen, EYE_L, EYE_W, int(blink_ry))
            _draw_eye(screen, EYE_R, EYE_W, int(blink_ry))

            heard = font_sm.render("I heard:", True, GRAY)
            screen.blit(heard, (20, 208))

            lines = _wrap_text(text or "...", font_md, WIDTH - 40)[:3]
            for i, line in enumerate(lines):
                surf = font_md.render(line, True, WHITE)
                screen.blit(surf, (20, 228 + i * 27))

        # ── Thinking ───────────────────────────────────────────────────
        elif state == "thinking":
            # Squinted eyes
            _draw_eye(screen, EYE_L, EYE_W, max(5, EYE_H // 4))
            _draw_eye(screen, EYE_R, EYE_W, max(5, EYE_H // 4))

            lbl = font_sm.render("THINKING", True, BLUE)
            screen.blit(lbl, (WIDTH // 2 - lbl.get_width() // 2, 212))

            dot_phase = (dot_phase + dt * 5.0) % (2 * math.pi)
            for i in range(3):
                offset = math.sin(dot_phase + i * (2 * math.pi / 3))
                y = int(262 + offset * 14)
                pygame.draw.circle(screen, BLUE, (WIDTH // 2 - 32 + i * 32, y), 9)

        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()
