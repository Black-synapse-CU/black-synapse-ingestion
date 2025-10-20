#!/bin/bash

# Black Synapse Data Ingestion Startup Script

echo "🚀 Starting Black Synapse Data Ingestion System..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "⚠️  .env file not found. Creating from template..."
    cp env.example .env
    echo "📝 Please edit .env file with your configuration before continuing."
    echo "   Required: OPENAI_API_KEY"
    exit 1
fi

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker and try again."
    exit 1
fi

# Check if Docker Compose is available
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose and try again."
    exit 1
fi

echo "🔧 Starting services with Docker Compose..."

# Start the services
docker-compose up -d

echo "⏳ Waiting for services to be ready..."

# Wait for services to be healthy
sleep 10

# Check service health
echo "🔍 Checking service health..."

# Check worker health
if curl -f http://localhost:8000/health > /dev/null 2>&1; then
    echo "✅ Worker service is healthy"
else
    echo "⚠️  Worker service is not responding yet. It may take a few more minutes to start."
fi

# Check PostgreSQL
if docker-compose exec -T postgres pg_isready -U postgres > /dev/null 2>&1; then
    echo "✅ PostgreSQL is ready"
else
    echo "⚠️  PostgreSQL is not ready yet"
fi

# Check Qdrant
if curl -f http://localhost:6333/health > /dev/null 2>&1; then
    echo "✅ Qdrant is ready"
else
    echo "⚠️  Qdrant is not ready yet"
fi

echo ""
echo "🎉 Black Synapse Data Ingestion System is starting up!"
echo ""
echo "📊 Service URLs:"
echo "   • Worker API: http://localhost:8000"
echo "   • API Docs: http://localhost:8000/docs"
echo "   • n8n Interface: http://localhost:5678"
echo "   • Qdrant Dashboard: http://localhost:6333/dashboard"
echo ""
echo "📝 Next steps:"
echo "   1. Configure your OpenAI API key in .env"
echo "   2. Import n8n workflows from n8n/workflows/"
echo "   3. Set up data source integrations"
echo "   4. Test the API endpoints"
echo ""
echo "📚 For more information, see README.md"
echo ""
echo "To view logs: docker-compose logs -f"
echo "To stop services: docker-compose down"
