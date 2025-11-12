import pvporcupine
import webrtcvad
import sounddevice as sd
import numpy as np
import collections
import time
import wave

# --- Config ---
SAMPLE_RATE = 16000
FRAME_DURATION_MS = 30  # 30 ms
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)
SILENCE_TIMEOUT_MS = 800  # Stop if this long silence
SILENCE_FRAMES = int(SILENCE_TIMEOUT_MS / FRAME_DURATION_MS)

# --- Wake Word ---
porcupine = pvporcupine.create(keywords=["jarvis"])
vad = webrtcvad.Vad(2)  # Aggressiveness: 0–3

# --- Audio ---
stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='int16', blocksize=FRAME_SIZE)
stream.start()

print("Listening for 'Jarvis'...")

def is_speech(frame):
    return vad.is_speech(frame.tobytes(), SAMPLE_RATE)

while True:
    # Wake word loop
    pcm = stream.read(FRAME_SIZE)[0].flatten()
    keyword_index = porcupine.process(pcm.tolist())

    if keyword_index >= 0:
        print("Wake word detected! Start speaking...")

        frames = []
        silence_counter = 0

        while True:
            frame = stream.read(FRAME_SIZE)[0].flatten()
            frames.append(frame)

            if is_speech(frame):
                silence_counter = 0
            else:
                silence_counter += 1
                if silence_counter > SILENCE_FRAMES:
                    print("End of speech.")
                    break

        # Combine frames and pass to ASR
        audio_np = np.concatenate(frames).astype(np.int16)

        # Optional: save to WAV for testing
        with wave.open("utterance.wav", 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_np.tobytes())

        print("Saved utterance.wav")