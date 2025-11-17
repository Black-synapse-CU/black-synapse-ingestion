FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for audio processing
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    openai-whisper \
    numpy

# Copy the ASR server code
COPY asr_server.py .

# Create directory for received audio
RUN mkdir -p received_audio

# Expose the port
EXPOSE 8002

# Run the ASR server
CMD ["python", "asr_server.py"]

