### asr_handler.py
# ASR module using whisper.cpp

import subprocess

def transcribe(audio_path="utterance.wav", model="base.en"):
    try:
        cmd = [
            "./main",  # Path to whisper.cpp binary
            "-m", f"models/ggml-{model}.bin",
            "-f", audio_path,
            "-otxt",
            "-of", "result"
        ]
        subprocess.run(cmd, check=True)
        with open("result.txt", "r") as f:
            return f.read().strip()
    except Exception as e:
        print("[ASR Error]", e)
        return "(transcription failed)"


### voice_trigger.py
# Handles wake word detection + VAD-based recording

import pvporcupine
import webrtcvad
import sounddevice as sd
import numpy as np
import wave
import os
from asr_handler import transcribe

SAMPLE_RATE = 16000
FRAME_DURATION_MS = 30
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)
SILENCE_FRAMES = int(800 / FRAME_DURATION_MS)
OUTPUT_WAV = "utterance.wav"
WHISPER_MODEL = "base.en"

def is_speech(frame, vad):
    return vad.is_speech(frame.tobytes(), SAMPLE_RATE)

def record_after_wake():
    porcupine = pvporcupine.create(keywords=["jarvis"])
    vad = webrtcvad.Vad(2)
    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='int16', blocksize=FRAME_SIZE)
    stream.start()

    print("[Listening for wake word 'Jarvis']")

    while True:
        pcm = stream.read(FRAME_SIZE)[0].flatten()
        if porcupine.process(pcm.tolist()) >= 0:
            print("[Wake word detected!] Recording...")
            frames = []
            silence_counter = 0

            while True:
                frame = stream.read(FRAME_SIZE)[0].flatten()
                frames.append(frame)

                if is_speech(frame, vad):
                    silence_counter = 0
                else:
                    silence_counter += 1
                    if silence_counter > SILENCE_FRAMES:
                        print("[Silence detected. Stopping recording.]")
                        break

            audio = np.concatenate(frames).astype(np.int16)

            with wave.open(OUTPUT_WAV, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio.tobytes())

            print(f"[Audio saved to {OUTPUT_WAV}]")

            transcript = transcribe(audio_path=OUTPUT_WAV, model=WHISPER_MODEL)
            print(f"[Transcript]: {transcript}\n")

            break

if __name__ == "__main__":
    record_after_wake()