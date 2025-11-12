import struct
import wave
import requests
import pvporcupine
import pyaudio
import io

# CONFIG
N8N_WEBHOOK_URL = "https://your-n8n-url/webhook/voice"
ACCESS_KEY = "YOUR_PICOVOICE_ACCESS_KEY"  # get from picovoice console
KEYWORD = "jarvis"  # or use built-in keywords

SAMPLE_RATE = 16000
CHANNELS = 1
FRAME_LENGTH = 512  # Porcupine expects this many samples per frame
SILENCE_FRAMES_TO_STOP = 20  # tweak (20 * 20ms ≈ 0.4s)

def create_wav_from_pcm16(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Wrap raw PCM16 mono into a WAV in-memory and return bytes."""
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buffer.getvalue()

def send_to_n8n(wav_bytes: bytes):
    files = {
        "file": ("audio.wav", wav_bytes, "audio/wav")
    }
    r = requests.post(N8N_WEBHOOK_URL, files=files, timeout=15)
    r.raise_for_status()

def main():
    # 1) init porcupine
    porcupine = pvporcupine.create(
        access_key=ACCESS_KEY,
        keywords=[KEYWORD],  # you can also supply a custom keyword path
    )

    # 2) init audio
    pa = pyaudio.PyAudio()
    audio_stream = pa.open(
        rate=porcupine.sample_rate,
        channels=1,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=porcupine.frame_length,
    )

    print("Listening for wake word...")

    try:
        recording = False
        recorded_frames = []
        silence_counter = 0

        while True:
            pcm = audio_stream.read(porcupine.frame_length, exception_on_overflow=False)
            # convert to ints for porcupine
            pcm_int16 = struct.unpack_from("h" * porcupine.frame_length, pcm)

            if not recording:
                # check wake word
                keyword_index = porcupine.process(pcm_int16)
                if keyword_index >= 0:
                    print("Wake word detected! Start speaking...")
                    recording = True
                    recorded_frames = []
                    silence_counter = 0
                continue

            # if we reached here, we're recording the utterance
            recorded_frames.append(pcm)

            # simple energy-based silence detection
            # you can replace this with webrtcvad if you want stronger VAD
            energy = max(abs(s) for s in pcm_int16)
            if energy < 500:  # tweak threshold
                silence_counter += 1
            else:
                silence_counter = 0

            if silence_counter > SILENCE_FRAMES_TO_STOP:
                print("Utterance ended, sending to n8n...")
                # join all PCM frames
                pcm_all = b"".join(recorded_frames)
                wav_bytes = create_wav_from_pcm16(pcm_all, sample_rate=SAMPLE_RATE)
                try:
                    send_to_n8n(wav_bytes)
                    print("Sent to n8n")
                except Exception as e:
                    print("Error sending to n8n:", e)
                # go back to listening
                recording = False
                print("Listening for wake word...")

    finally:
        audio_stream.stop_stream()
        audio_stream.close()
        pa.terminate()
        porcupine.delete()


if __name__ == "__main__":
    main()