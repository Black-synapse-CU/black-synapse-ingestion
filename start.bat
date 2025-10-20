@echo off
REM Black Synapse Data Ingestion Startup Script for Windows

echo 🚀 Starting Black Synapse Data Ingestion System...

REM Check if .env file exists
if not exist .env (
    echo ⚠️  .env file not found. Creating from template...
    copy env.example .env
    echo 📝 Please edit .env file with your configuration before continuing.
    echo    Required: OPENAI_API_KEY
    pause
    exit /b 1
)

REM Check if Docker is running
docker info >nul 2>&1
if errorlevel 1 (
    echo ❌ Docker is not running. Please start Docker and try again.
    pause
    exit /b 1
)

REM Check if Docker Compose is available
docker-compose --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Docker Compose is not installed. Please install Docker Compose and try again.
    pause
    exit /b 1
)

echo 🔧 Starting services with Docker Compose...

REM Start the services
docker-compose up -d

echo ⏳ Waiting for services to be ready...

REM Wait for services to be healthy
timeout /t 10 /nobreak >nul

REM Check service health
echo 🔍 Checking service health...

REM Check worker health
curl -f http://localhost:8000/health >nul 2>&1
if errorlevel 1 (
    echo ⚠️  Worker service is not responding yet. It may take a few more minutes to start.
) else (
    echo ✅ Worker service is healthy
)

REM Check PostgreSQL
docker-compose exec -T postgres pg_isready -U postgres >nul 2>&1
if errorlevel 1 (
    echo ⚠️  PostgreSQL is not ready yet
) else (
    echo ✅ PostgreSQL is ready
)

REM Check Qdrant
curl -f http://localhost:6333/health >nul 2>&1
if errorlevel 1 (
    echo ⚠️  Qdrant is not ready yet
) else (
    echo ✅ Qdrant is ready
)

echo.
echo 🎉 Black Synapse Data Ingestion System is starting up!
echo.
echo 📊 Service URLs:
echo    • Worker API: http://localhost:8000
echo    • API Docs: http://localhost:8000/docs
echo    • n8n Interface: http://localhost:5678
echo    • Qdrant Dashboard: http://localhost:6333/dashboard
echo.
echo 📝 Next steps:
echo    1. Configure your OpenAI API key in .env
echo    2. Import n8n workflows from n8n/workflows/
echo    3. Set up data source integrations
echo    4. Test the API endpoints
echo.
echo 📚 For more information, see README.md
echo.
echo To view logs: docker-compose logs -f
echo To stop services: docker-compose down
pause
