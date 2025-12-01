### voice_trigger.py
# Handles wake word detection + VAD-based recording
# Continuously listens for wake word and records audio after detection

import webrtcvad
import sounddevice as sd
import numpy as np
import wave
from openwakeword import Model
import openwakeword

SAMPLE_RATE = 16000
# OpenWakeWord processes audio in chunks (typically 1280 samples = 80ms at 16kHz)
# Using 1280 samples per frame for optimal performance
OWW_FRAME_LENGTH = 1280
# VAD frame size (30ms for optimal VAD performance)
VAD_FRAME_DURATION_MS = 30
VAD_FRAME_SIZE = int(SAMPLE_RATE * VAD_FRAME_DURATION_MS / 1000)
SILENCE_FRAMES = int(800 / VAD_FRAME_DURATION_MS)
OUTPUT_WAV = "utterance.wav"
# Detection threshold for wake word (adjust between 0.0 and 1.0)
WAKE_WORD_THRESHOLD = 0.5

def is_speech(frame, vad):
    return vad.is_speech(frame.tobytes(), SAMPLE_RATE)

def record_after_wake():
    oww_model = None
    stream = None

    openwakeword.utils.download_models()
    
    try:
        # Initialize OpenWakeWord model
        # Available models: "alexa", "hey_jarvis", "hey_mycroft", "hey_porcupine", "hey_rhasspy", "hey_spot", "hey_raven", "timer"
        # Using "hey_jarvis" as it's closest to "Jarvis"
        oww_model = Model(wakeword_models=["hey_jarvis"])
        wake_word_name = "hey_jarvis"
    except Exception as e:
        raise RuntimeError(f"OpenWakeWord initialization error: {e}")
    
    vad = webrtcvad.Vad(2)
    # Use OpenWakeWord's recommended frame length for the stream
    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='int16', blocksize=OWW_FRAME_LENGTH)
    
    try:
        stream.start()
        print("[Listening for wake word 'hey_jarvis']")

        while True:
            pcm = stream.read(OWW_FRAME_LENGTH)[0].flatten()
            
            # Process audio through OpenWakeWord
            prediction = oww_model.predict(pcm)
            
            # Check if wake word detected (prediction is a dict with model names as keys)
            if wake_word_name in prediction and prediction[wake_word_name] > WAKE_WORD_THRESHOLD:
                print("[Wake word detected!] Recording...")
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