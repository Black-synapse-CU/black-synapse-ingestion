# AtlasAI PostgreSQL Database

PostgreSQL (with **pgvector**) is the single persistence layer for AtlasAI. It fully replaces Qdrant — all vector embeddings, document metadata, chat memory, and audit logs live here.

## Connection

| Setting  | Value                                      |
|----------|--------------------------------------------|
| Host     | `localhost:5433` (Docker maps 5433 → 5432) |
| Database | `atlasai`                                  |
| User     | `n8n`                                      |
| Password | `n8npassword`                              |
| DSN      | `postgresql://n8n:n8npassword@localhost:5433/atlasai` |

The container runs `pgvector/pgvector:pg16`. Both the `vector` and `uuid-ossp` extensions are enabled.

---

## Tables

### `documents`
Registry of every document ingested from any source. One row per unique document.

| Column         | Type        | Notes |
|----------------|-------------|-------|
| `doc_id`       | VARCHAR PK  | Stable ID from the source system |
| `source`       | VARCHAR     | `notion`, `gmail`, `drive`, `slack`, etc. |
| `title`        | TEXT        | |
| `uri`          | TEXT        | Link back to the original resource |
| `author`       | VARCHAR     | |
| `created_at`   | TIMESTAMPTZ | Source timestamp |
| `updated_at`   | TIMESTAMPTZ | Source timestamp |
| `content_hash` | VARCHAR(64) | SHA-256 of raw content — drives deduplication |
| `processed_at` | TIMESTAMPTZ | When the worker last processed this doc |
| `is_deleted`   | BOOLEAN     | Soft-delete; cascades to `document_chunks` |
| `chunk_count`  | INTEGER     | How many chunks were stored |
| `metadata`     | JSONB       | Source-specific extras |

---

### `document_chunks` *(replaces Qdrant `atlasai_documents`)*
One row per text chunk with its embedding. This is the primary vector search table.

| Column        | Type          | Notes |
|---------------|---------------|-------|
| `id`          | BIGSERIAL PK  | |
| `doc_id`      | VARCHAR FK    | References `documents(doc_id)`, cascades on delete |
| `chunk_index` | INTEGER       | 0-based position within the parent document |
| `text`        | TEXT          | The raw chunk text (~500 tokens, 50-token overlap) |
| `embedding`   | vector(1536)  | OpenAI embedding; 3072 if using `text-embedding-3-large` |
| `source`      | VARCHAR       | Denormalized for fast filtered search |
| `title`       | TEXT          | Denormalized |
| `uri`         | TEXT          | Denormalized |
| `author`      | VARCHAR       | Denormalized |
| `created_at`  | TIMESTAMPTZ   | Denormalized from parent doc |
| `updated_at`  | TIMESTAMPTZ   | Denormalized from parent doc |

HNSW index (`hnsw_chunks_embedding`) on `embedding` using `vector_cosine_ops` for fast ANN search.

> If you switch embedding models, drop and recreate this table — dimensions must match exactly.

---

### `chat_memory` *(replaces Qdrant `atlasai_chat_memory`)*
Consolidated session embeddings. Populated by `POST /chat/memory/consolidate` after raw `chat_logs` are grouped, chunked, and embedded.

| Column        | Type          | Notes |
|---------------|---------------|-------|
| `id`          | BIGSERIAL PK  | |
| `session_id`  | VARCHAR       | Groups a conversation |
| `chunk_index` | INTEGER       | Position within the session's consolidated text |
| `text`        | TEXT          | Consolidated/summarised text |
| `embedding`   | vector(1536)  | |
| `created_at`  | TIMESTAMPTZ   | |

HNSW index (`hnsw_memory_embedding`) for semantic recall queries.

---

### `chat_logs`
Raw, per-message conversation log. The pipeline reads unindexed rows here, consolidates them into `chat_memory`, then sets `is_indexed = TRUE`.

| Column       | Type        | Notes |
|--------------|-------------|-------|
| `id`         | BIGSERIAL PK | |
| `session_id` | VARCHAR     | |
| `role`       | VARCHAR     | `user` or `assistant` |
| `message`    | TEXT        | |
| `timestamp`  | TIMESTAMPTZ | |
| `is_indexed` | BOOLEAN     | Set TRUE after consolidation into `chat_memory` |
| `metadata`   | JSONB       | |

---

### `ingestion_log`
Append-only audit trail. Multiple rows per document — one per processing event.

| Column       | Type        | Notes |
|--------------|-------------|-------|
| `id`         | BIGSERIAL PK | |
| `doc_id`     | VARCHAR     | |
| `event_type` | VARCHAR     | `ingested`, `skipped`, `error`, `deleted` |
| `message`    | TEXT        | Human-readable detail |
| `timestamp`  | TIMESTAMPTZ | |
| `metadata`   | JSONB       | Structured event data |

Auto-pruned after 30 days by `cleanup_old_logs()`.

---

### `source_sync`
Tracks each sync run triggered by n8n or the worker.

| Column                | Type        | Notes |
|-----------------------|-------------|-------|
| `id`                  | BIGSERIAL PK | |
| `source`              | VARCHAR     | e.g. `notion`, `gmail` |
| `sync_type`           | VARCHAR     | `full`, `incremental`, `deletion_check` |
| `started_at`          | TIMESTAMPTZ | |
| `completed_at`        | TIMESTAMPTZ | NULL while running |
| `status`              | VARCHAR     | `running`, `completed`, `failed` |
| `documents_processed` | INTEGER     | |
| `documents_deleted`   | INTEGER     | |
| `error_message`       | TEXT        | Set on failure |
| `metadata`            | JSONB       | |

Completed rows older than 7 days are pruned by `cleanup_old_logs()`.

---

## Views

### `active_documents`
Filters `documents` to `is_deleted = FALSE`. Use this for application queries instead of hitting `documents` directly.

```sql
SELECT * FROM active_documents WHERE source = 'notion';
```

---

## Helper Functions

### `search_documents(query_embedding, k)`
Top-k cosine similarity search over `document_chunks`. Returns `score` as `1 - cosine_distance` (higher = more similar).

```sql
SELECT * FROM search_documents('[0.1, 0.2, ...]'::vector, 10);
```

### `search_chat_memory(query_embedding, k)`
Same but over `chat_memory`.

```sql
SELECT * FROM search_chat_memory('[0.1, 0.2, ...]'::vector, 5);
```

### `get_document_stats()`
Returns total document count, breakdown by source, total chunks, average chunks per doc, and last processed time.

```sql
SELECT * FROM get_document_stats();
```

### `mark_documents_deleted(doc_ids[])`
Soft-deletes documents by ID. `document_chunks` rows are cascade-deleted automatically.

```sql
SELECT mark_documents_deleted(ARRAY['notion-abc123', 'gmail-xyz789']);
```

### `cleanup_old_logs()`
Prunes `ingestion_log` entries older than 30 days and completed `source_sync` rows older than 7 days. Run periodically via cron or a scheduled n8n workflow.

```sql
SELECT cleanup_old_logs();
```

---

## Schema Notes

### Embedding dimensions
The schema defaults to `vector(1536)` (OpenAI `text-embedding-3-small`). If you switch to `text-embedding-3-large` (3072 dimensions), you must:
1. Drop `document_chunks` and `chat_memory`
2. Recreate them with `vector(3072)`
3. Re-ingest all documents

### Re-initialising
The init script is idempotent (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`). To fully reset:

```bash
docker exec black-synapse-postgres-1 psql -U n8n -c "DROP DATABASE atlasai;"
docker exec black-synapse-postgres-1 psql -U n8n -c "CREATE DATABASE atlasai OWNER n8n;"
docker exec -i black-synapse-postgres-1 psql -U n8n -d atlasai < postgres/init.sql
```
