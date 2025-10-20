# Black Synapse Data Ingestion

A comprehensive data ingestion system for **Black Synapse (Embodied Intelligence Engine)** that processes, normalizes, and embeds data from various sources into a unified vector database for RAG (Retrieval-Augmented Generation) applications.

## 🚀 Features

- **Multi-Source Ingestion**: Connect to Notion, Gmail, Drive, Slack, and other data sources
- **Unified Schema**: Normalize all data into a consistent format
- **Intelligent Chunking**: Smart text segmentation with configurable overlap
- **Vector Embeddings**: Generate embeddings using OpenAI's text-embedding-3-small
- **Deduplication**: SHA-256 based content hashing to prevent duplicate processing
- **Workflow Orchestration**: n8n-powered ETL workflows with webhook and scheduled triggers
- **Scalable Architecture**: Docker Compose stack with PostgreSQL and Qdrant
- **Comprehensive Testing**: Unit tests for all core functionality

## 🏗️ Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Data Sources  │    │   n8n Workflows │    │  FastAPI Worker │
│                 │    │                 │    │                 │
│ • Notion        │───▶│ • Webhooks      │───▶│ • Deduplication │
│ • Gmail         │    │ • Scheduling    │    │ • Chunking      │
│ • Drive         │    │ • Normalization │    │ • Embeddings    │
│ • Slack         │    │                 │    │ • Vector Store  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                                                       │
                       ┌─────────────────┐            │
                       │   PostgreSQL    │◀───────────┘
                       │                 │
                       │ • Document Meta │
                       │ • Ingestion Log │
                       │ • Sync Tracking │
                       └─────────────────┘
                                │
                       ┌─────────────────┐
                       │     Qdrant      │
                       │                 │
                       │ • Vector Store  │
                       │ • Similarity    │
                       │ • Search        │
                       └─────────────────┘
```

## 📋 Prerequisites

- Docker and Docker Compose
- OpenAI API Key
- Python 3.10+ (for local development)

## 🚀 Quick Start

### 1. Clone and Setup

```bash
git clone <repository-url>
cd black-synapse-ingestion
```

### 2. Environment Configuration

```bash
# Copy environment template
cp env.example .env

# Edit .env with your configuration
nano .env
```

Required environment variables:
```env
OPENAI_API_KEY=your_openai_api_key_here
POSTGRES_URL=postgresql://postgres:password@localhost:5432/black_synapse
QDRANT_URL=http://localhost:6333
```

### 3. Start the Stack

```bash
# Start all services
docker-compose up -d

# Check service status
docker-compose ps
```

### 4. Verify Installation

```bash
# Check worker health
curl http://localhost:8000/health

# Check n8n interface
open http://localhost:5678
```

## 📚 API Documentation

### Endpoints

#### `POST /ingest`
Ingest a single document.

**Request Body:**
```json
{
  "doc_id": "unique_document_id",
  "source": "notion",
  "title": "Document Title",
  "uri": "https://example.com/doc",
  "text": "Document content text...",
  "author": "Author Name",
  "created_at": "2023-01-01T00:00:00Z",
  "updated_at": "2023-01-01T00:00:00Z"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Document processed successfully",
  "doc_id": "unique_document_id",
  "chunks_processed": 5
}
```

#### `POST /reindex`
Re-index an existing document.

**Query Parameters:**
- `doc_id`: Document ID to re-index

#### `POST /sync`
Perform full synchronization for a data source.

**Query Parameters:**
- `source`: Data source to sync (e.g., "notion", "gmail")

### Interactive API Documentation

Visit `http://localhost:8000/docs` for interactive Swagger documentation.

## 🔧 Development

### Local Development Setup

```bash
# Install Python dependencies
cd worker
pip install -r requirements.txt

# Set environment variables
export OPENAI_API_KEY=your_key_here
export POSTGRES_URL=postgresql://postgres:password@localhost:5432/black_synapse
export QDRANT_URL=http://localhost:6333

# Run the worker
python -m uvicorn app.main:app --reload
```

### Running Tests

```bash
cd worker
pytest tests/ -v
```

### Code Quality

```bash
# Format code
black app/

# Lint code
flake8 app/

# Type checking
mypy app/
```

## 🔄 n8n Workflows

### Notion Integration

1. Import the Notion workflow from `n8n/workflows/notion-sync.json`
2. Configure Notion API credentials in n8n
3. Set up webhook URL in Notion integration settings
4. Activate the workflow

### Gmail Integration

1. Import the Gmail workflow from `n8n/workflows/gmail-sync.json`
2. Configure Gmail OAuth2 credentials in n8n
3. Adjust the schedule trigger as needed
4. Activate the workflow

### Custom Workflows

Create custom workflows for additional data sources by following the unified schema:

```json
{
  "doc_id": "source_unique_id",
  "source": "your_source_name",
  "title": "Document Title",
  "uri": "Document URL",
  "text": "Full text content",
  "author": "Author Name",
  "created_at": "ISO 8601 timestamp",
  "updated_at": "ISO 8601 timestamp"
}
```

## 📊 Monitoring and Logging

### Health Checks

- **Worker**: `GET http://localhost:8000/health`
- **PostgreSQL**: `docker-compose exec postgres pg_isready`
- **Qdrant**: `curl http://localhost:6333/health`

### Logs

```bash
# View all logs
docker-compose logs -f

# View specific service logs
docker-compose logs -f worker
docker-compose logs -f postgres
docker-compose logs -f qdrant
```

### Database Queries

```sql
-- Check document statistics
SELECT * FROM get_document_stats();

-- View recent ingestion events
SELECT * FROM ingestion_log 
ORDER BY timestamp DESC 
LIMIT 10;

-- Check documents by source
SELECT source, COUNT(*) as count 
FROM documents 
WHERE is_deleted = FALSE 
GROUP BY source;
```

## 🛠️ Configuration

### Chunking Strategy

Configure text chunking in `worker/app/utils.py`:

```python
# Default settings
max_tokens = 500        # Maximum tokens per chunk
overlap_tokens = 50     # Overlap between chunks
```

### Embedding Model

Change the embedding model in `worker/app/pipeline.py`:

```python
# Available models
model = "text-embedding-3-small"  # Default (1536 dimensions)
model = "text-embedding-3-large"  # Alternative (3072 dimensions)
```

### Database Configuration

Modify `docker-compose.yml` for production settings:

```yaml
postgres:
  environment:
    POSTGRES_PASSWORD: your_secure_password
    POSTGRES_DB: your_database_name
```

## 🚀 Production Deployment

### Environment Variables

Set production environment variables:

```env
OPENAI_API_KEY=your_production_key
POSTGRES_URL=postgresql://user:password@db-host:5432/black_synapse
QDRANT_URL=http://qdrant-host:6333
LOG_LEVEL=WARNING
```

### Security Considerations

1. Use strong database passwords
2. Configure n8n authentication
3. Set up proper network security
4. Enable SSL/TLS for production
5. Regular security updates

### Scaling

- **Horizontal Scaling**: Run multiple worker instances behind a load balancer
- **Database Scaling**: Use read replicas for PostgreSQL
- **Vector Database**: Configure Qdrant clustering for high availability

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Run the test suite
6. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

For support and questions:

1. Check the [Issues](https://github.com/your-repo/issues) page
2. Review the API documentation at `/docs`
3. Check the logs for error details
4. Verify environment configuration

## 🔄 Changelog

### v1.0.0
- Initial release
- Multi-source data ingestion
- Unified document schema
- Vector embedding pipeline
- n8n workflow integration
- Comprehensive testing suite
