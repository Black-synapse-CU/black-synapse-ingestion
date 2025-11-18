#!/bin/bash
set -e

OLLAMA_HOST="${OLLAMA_HOST:-ollama:11434}"

echo "Waiting for Ollama to be ready at $OLLAMA_HOST..."
# Wait for Ollama to be ready by checking if we can list models
until OLLAMA_HOST=$OLLAMA_HOST ollama list > /dev/null 2>&1; do
  echo "Ollama is not ready yet. Waiting..."
  sleep 2
done

echo "Ollama is ready! Pulling gemma3 model..."
OLLAMA_HOST=$OLLAMA_HOST ollama pull gemma3

echo "Model gemma3 has been pulled successfully!"

