### voice_trigger.py
# Handles wake word detection + VAD-based recording
# Continuously listens for wake word and records audio after detection

import webrtcvad
import sounddevice as sd
import soundfile as sf
import numpy as np
import asyncio
import json
import time
import os
import random
import urllib.request
import websockets
from pathlib import Path
from openwakeword import Model
import openwakeword
from dotenv import load_dotenv

load_dotenv()

WAKE_SOUNDS_DIR = Path(__file__).parent / "wake_sounds"

SPEAKER_API_URL = os.getenv("SPEAKER_API_URL", "http://localhost:8001")
FACE_SERVICE_URL = os.getenv("FACE_SERVICE_URL", "http://raspberrypi.local:8003")

def _set_face(state: str, text: str = ""):
    """Fire-and-forget face state update to the Pi display."""
    try:
        import json as _json
        body = _json.dumps({"state": state, "text": text}).encode()
        req = urllib.request.Request(
            f"{FACE_SERVICE_URL}/face/state",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1.0).close()
    except Exception:
        pass

def _interrupt_speaker() -> int:
    """Interrupt the speaker API and return the current epoch value."""
    try:
        req = urllib.request.Request(
            f"{SPEAKER_API_URL}/interrupt",
            data=b"",
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return int(json.loads(resp.read()).get("epoch", 0))
    except Exception:
        return 0  # Non-critical — recording continues regardless


async def _stream_to_asr(frames: list, epoch: int) -> None:
    """Stream recorded PCM frames to asr_server over WebSocket for transcription."""
    try:
        async with websockets.connect(ASR_WS_URL) as ws:
            for frame in frames:
                await ws.send(frame.astype(np.int16).tobytes())
            await ws.send(json.dumps({"done": True, "epoch": epoch}))
            try:
                result = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
                print(f"[Transcript: {result.get('text', '')}]")
            except asyncio.TimeoutError:
                print("[Warning: ASR server did not respond in time]")
    except Exception as e:
        print(f"[Warning: Could not reach ASR server: {e}]")

SAMPLE_RATE = 16000
# OpenWakeWord processes audio in chunks (typically 1280 samples = 80ms at 16kHz)
# Using 1280 samples per frame for optimal performance
OWW_FRAME_LENGTH = 1280
# VAD frame size (30ms for optimal VAD performance)
VAD_FRAME_DURATION_MS = 30
VAD_FRAME_SIZE = int(SAMPLE_RATE * VAD_FRAME_DURATION_MS / 1000)
SILENCE_DURATION_MS = int(os.getenv("SILENCE_DURATION_MS", "1800"))
SILENCE_FRAMES = int(SILENCE_DURATION_MS / VAD_FRAME_DURATION_MS)
# Detection threshold for wake word (adjust between 0.0 and 1.0)
WAKE_WORD_THRESHOLD = 0.5
# Cooldown period after detection (seconds) - prevents immediate re-detection
COOLDOWN_SECONDS = 2.0

def _play_wake_chime():
    """Play a random acknowledgment phrase from ASR/wake_sounds/. Falls back to a simple chime if none found."""
    sound_files = list(WAKE_SOUNDS_DIR.glob("*.wav")) + list(WAKE_SOUNDS_DIR.glob("*.mp3"))
    if sound_files:
        try:
            chosen = random.choice(sound_files)
            audio, sample_rate = sf.read(str(chosen), dtype="float32")
            sd.play(audio, samplerate=sample_rate, blocking=True)
            return
        except Exception:
            pass  # Fall through to chime
    # Fallback: two-tone chime
    try:
        duration = 0.08
        t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
        fade = np.linspace(0.0, 1.0, int(SAMPLE_RATE * 0.01))
        tone1 = (0.35 * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
        tone2 = (0.35 * np.sin(2 * np.pi * 1320 * t)).astype(np.float32)
        tone1[:len(fade)] *= fade
        tone2[:len(fade)] *= fade
        tone1[-len(fade):] *= fade[::-1]
        tone2[-len(fade):] *= fade[::-1]
        sd.play(np.concatenate([tone1, tone2]), samplerate=SAMPLE_RATE, blocking=True)
    except Exception:
        pass


def is_speech(frame, vad):
    return vad.is_speech(frame.tobytes(), SAMPLE_RATE)

def record_after_wake():
    oww_model = None
    stream = None
    
    try:
        # Initialize OpenWakeWord model
        openwakeword.utils.download_models()

        # Available models: "alexa", "hey_jarvis", "hey_mycroft", "hey_porcupine", "hey_rhasspy", "hey_spot", "hey_raven", "timer"
        # Using "hey_jarvis" as it's closest to "Jarvis"
        oww_model = Model(wakeword_models=["hey jarvis"],)
        wake_word_name = "hey jarvis"
    except Exception as e:
        raise RuntimeError(f"OpenWakeWord initialization error: {e}")
    
    vad = webrtcvad.Vad(2)
    # Use OpenWakeWord's recommended frame length for the stream
    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='int16', blocksize=OWW_FRAME_LENGTH)
    
    try:
        stream.start()
        print("[Listening for wake word 'hey jarvis']")
        
        last_detection_time = 0.0  # Track when last wake word was detected
        cooldown_frames_to_flush = int(COOLDOWN_SECONDS * SAMPLE_RATE / OWW_FRAME_LENGTH)  # Frames to flush during cooldown

        while True:
            pcm = stream.read(OWW_FRAME_LENGTH)[0].flatten()
            
            # Process audio through OpenWakeWord
            prediction = oww_model.predict(pcm)
            
            # Check if we're in cooldown period
            current_time = time.time()
            in_cooldown = (current_time - last_detection_time) < COOLDOWN_SECONDS
            
            # Check if wake word detected (prediction is a dict with model names as keys)
            if (not in_cooldown and 
                wake_word_name in prediction and 
                prediction[wake_word_name] > WAKE_WORD_THRESHOLD):
                
                last_detection_time = current_time
                print("[Wake word detected!] Interrupting speaker and recording...")
                _set_face("listening")
                _interrupt_speaker()
                _play_wake_chime()
                frames = []
                silence_counter = 0
                # Buffer for VAD processing (need 30ms frames for VAD)
                vad_buffer = np.array([], dtype=np.int16)

                while True:
                    frame = stream.read(OWW_FRAME_LENGTH)[0].flatten()
                    frames.append(frame)
                    vad_buffer = np.concatenate([vad_buffer, frame])

                    # Process VAD when we have enough samples (30ms = 480 samples)
                    while len(vad_buffer) >= VAD_FRAME_SIZE:
                        vad_frame = vad_buffer[:VAD_FRAME_SIZE]
                        vad_buffer = vad_buffer[VAD_FRAME_SIZE:]

                        if is_speech(vad_frame, vad):
                            silence_counter = 0
                        else:
                            silence_counter += 1
                            if silence_counter > SILENCE_FRAMES:
                                print("[Silence detected. Stopping recording.]")
                                break

                    if silence_counter > SILENCE_FRAMES:
                        break

                print(f"[Streaming {len(frames)} frames to ASR server (epoch={epoch})...]")
                asyncio.run(_stream_to_asr(frames, epoch))
                
                # Flush model state by processing silence frames during cooldown
                print(f"[Cooldown period: {COOLDOWN_SECONDS}s - flushing model state...]")
                silence_frame = np.zeros(OWW_FRAME_LENGTH, dtype=np.int16)
                for _ in range(cooldown_frames_to_flush):
                    oww_model.predict(silence_frame)
                
                print("[Resuming wake word detection...]\n")
                # Continue listening for next wake word (don't break)
    finally:
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        if oww_model is not None:
            try:
                # OpenWakeWord models don't require explicit cleanup, but we can reset if needed
                pass
            except Exception:
                pass

if __name__ == "__main__":
    record_after_wake()