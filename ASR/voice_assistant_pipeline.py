### voice_trigger.py
# Handles wake word detection + VAD-based recording
# Continuously listens for "Jarvis" wake word and records audio after detection

import os
from dotenv import load_dotenv
import pvporcupine
import webrtcvad
import sounddevice as sd
import numpy as np
import wave

# Load environment variables from .env file
load_dotenv()

SAMPLE_RATE = 16000
# Porcupine requires 512 samples per frame at 16kHz
PORCUPINE_FRAME_LENGTH = 512
# VAD frame size (30ms for optimal VAD performance)
VAD_FRAME_DURATION_MS = 30
VAD_FRAME_SIZE = int(SAMPLE_RATE * VAD_FRAME_DURATION_MS / 1000)
SILENCE_FRAMES = int(800 / VAD_FRAME_DURATION_MS)
OUTPUT_WAV = "utterance.wav"

def is_speech(frame, vad):
    return vad.is_speech(frame.tobytes(), SAMPLE_RATE)

def record_after_wake():
    access_key = os.getenv("PICOVOICE_ACCESS_KEY")
    if not access_key:
        raise ValueError(
            "PICOVOICE_ACCESS_KEY environment variable is required. "
            "Get your access key from https://console.picovoice.ai/"
        )
    porcupine = pvporcupine.create(access_key=access_key, keywords=["jarvis"])
    vad = webrtcvad.Vad(2)
    # Use Porcupine's frame length for the stream
    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='int16', blocksize=PORCUPINE_FRAME_LENGTH)
    
    try:
        stream.start()
        print("[Listening for wake word 'Jarvis']")

        while True:
            pcm = stream.read(PORCUPINE_FRAME_LENGTH)[0].flatten()
            if porcupine.process(pcm.tolist()) >= 0:
                print("[Wake word detected!] Recording...")
                frames = []
                silence_counter = 0
                # Buffer for VAD processing (need 30ms frames for VAD)
                vad_buffer = np.array([], dtype=np.int16)

                while True:
                    frame = stream.read(PORCUPINE_FRAME_LENGTH)[0].flatten()
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

                audio = np.concatenate(frames).astype(np.int16)

                with wave.open(OUTPUT_WAV, 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(SAMPLE_RATE)
                    wf.writeframes(audio.tobytes())

                print(f"[Audio saved to {OUTPUT_WAV}]")
                print("[Resuming wake word detection...]\n")
                # Continue listening for next wake word (don't break)
    finally:
        stream.stop()
        stream.close()
        porcupine.delete()

if __name__ == "__main__":
    record_after_wake()