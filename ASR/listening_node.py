import pvporcupine
import sounddevice as sd
from whisper_cpp import Whisper

# Set up wake word
porcupine = pvporcupine.create(keywords=["jarvis"])
audio_stream = sd.InputStream(samplerate=16000, channels=1, dtype='int16')
audio_stream.start()

# Load Whisper
asr = Whisper(model_path="models/base.en")

print("Listening...")

while True:
    pcm = audio_stream.read(512)[0]
    pcm = pcm.flatten().tolist()

    keyword_index = porcupine.process(pcm)
    if keyword_index >= 0:
        print("Wake word detected! Listening for command...")
        recorded_audio = []  # Start capturing audio here

        # Record for N seconds
        for _ in range(0, 16000 * 3 // 512):
            frame = audio_stream.read(512)[0].flatten().tolist()
            recorded_audio.extend(frame)

        # Transcribe
        text = asr.transcribe(recorded_audio)
        print(f"Transcribed: {text}")