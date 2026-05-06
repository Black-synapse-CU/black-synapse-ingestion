-- AtlasAI Database Schema
-- PostgreSQL (pgvector) — fully replaces Qdrant for vector storage
-- Database: atlasai

\c atlasai;

-- ── Extensions ───────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Document Registry ────────────────────────────────────────────────────────

-- One row per unique document ingested from any source.
-- content_hash drives idempotency — skip re-ingestion if unchanged.
CREATE TABLE IF NOT EXISTS documents (
    doc_id          VARCHAR(255)  PRIMARY KEY,
    source          VARCHAR(100)  NOT NULL,
    title           TEXT,
    uri             TEXT,
    author          VARCHAR(255),
    created_at      TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ,
    content_hash    VARCHAR(64)   UNIQUE,
    processed_at    TIMESTAMPTZ   DEFAULT NOW(),
    is_deleted      BOOLEAN       DEFAULT FALSE,
    chunk_count     INTEGER       DEFAULT 0,
    metadata        JSONB         DEFAULT '{}'::jsonb
);

-- ── Vector Store: Documents ──────────────────────────────────────────────────
-- Replaces Qdrant collection `atlasai_documents`.
-- Each row is one ~500-token chunk with its embedding.
-- Embedding dimension matches EMBEDDING_MODEL env var:
--   text-embedding-3-small → 1536
--   text-embedding-3-large → 3072
-- Change the dimension here and recreate if switching models.

CREATE TABLE IF NOT EXISTS document_chunks (
    id              BIGSERIAL     PRIMARY KEY,
    doc_id          VARCHAR(255)  NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    chunk_index     INTEGER       NOT NULL,
    text            TEXT          NOT NULL,
    embedding       vector(1536),
    source          VARCHAR(100),
    title           TEXT,
    uri             TEXT,
    author          VARCHAR(255),
    created_at      TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ,
    UNIQUE (doc_id, chunk_index)
);

-- ── Vector Store: Chat Memory ────────────────────────────────────────────────
-- Replaces Qdrant collection `atlasai_chat_memory`.
-- Stores consolidated/summarised session embeddings after chat_logs are indexed.

CREATE TABLE IF NOT EXISTS chat_memory (
    id              BIGSERIAL     PRIMARY KEY,
    session_id      VARCHAR(255)  NOT NULL,
    chunk_index     INTEGER       NOT NULL DEFAULT 0,
    text            TEXT          NOT NULL,
    embedding       vector(1536),
    created_at      TIMESTAMPTZ   DEFAULT NOW(),
    UNIQUE (session_id, chunk_index)
);

-- ── Chat Logs ────────────────────────────────────────────────────────────────
-- Raw per-message log. Rows are marked is_indexed=TRUE after consolidation
-- into chat_memory via POST /chat/memory/consolidate.

CREATE TABLE IF NOT EXISTS chat_logs (
    id              BIGSERIAL     PRIMARY KEY,
    session_id      VARCHAR(255)  NOT NULL,
    role            VARCHAR(50)   NOT NULL,   -- 'user' | 'assistant'
    message         TEXT          NOT NULL,
    timestamp       TIMESTAMPTZ   DEFAULT NOW(),
    is_indexed      BOOLEAN       DEFAULT FALSE,
    metadata        JSONB         DEFAULT '{}'::jsonb
);

-- ── Ingestion Audit Log ──────────────────────────────────────────────────────
-- Append-only event log; one row per processing event per document.

CREATE TABLE IF NOT EXISTS ingestion_log (
    id              BIGSERIAL     PRIMARY KEY,
    doc_id          VARCHAR(255)  NOT NULL,
    event_type      VARCHAR(50)   NOT NULL,   -- 'ingested' | 'skipped' | 'error' | 'deleted'
    message         TEXT,
    timestamp       TIMESTAMPTZ   DEFAULT NOW(),
    metadata        JSONB         DEFAULT '{}'::jsonb
);

-- ── Source Sync Tracker ──────────────────────────────────────────────────────
-- One row per sync run triggered by n8n or the worker.

CREATE TABLE IF NOT EXISTS source_sync (
    id                    BIGSERIAL    PRIMARY KEY,
    source                VARCHAR(100) NOT NULL,
    sync_type             VARCHAR(50)  NOT NULL,  -- 'full' | 'incremental' | 'deletion_check'
    started_at            TIMESTAMPTZ  DEFAULT NOW(),
    completed_at          TIMESTAMPTZ,
    status                VARCHAR(20)  DEFAULT 'running',  -- 'running' | 'completed' | 'failed'
    documents_processed   INTEGER      DEFAULT 0,
    documents_deleted     INTEGER      DEFAULT 0,
    error_message         TEXT,
    metadata              JSONB        DEFAULT '{}'::jsonb
);

-- ── Indexes ──────────────────────────────────────────────────────────────────

-- documents
CREATE INDEX IF NOT EXISTS idx_documents_source        ON documents(source);
CREATE INDEX IF NOT EXISTS idx_documents_content_hash  ON documents(content_hash);
CREATE INDEX IF NOT EXISTS idx_documents_processed_at  ON documents(processed_at);
CREATE INDEX IF NOT EXISTS idx_documents_is_deleted    ON documents(is_deleted);
CREATE INDEX IF NOT EXISTS idx_documents_updated_at    ON documents(updated_at);

-- document_chunks — HNSW index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS idx_chunks_doc_id           ON document_chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_source           ON document_chunks(source);
CREATE INDEX IF NOT EXISTS hnsw_chunks_embedding       ON document_chunks USING hnsw (embedding vector_cosine_ops);

-- chat_memory — HNSW index for semantic recall
CREATE INDEX IF NOT EXISTS idx_memory_session_id       ON chat_memory(session_id);
CREATE INDEX IF NOT EXISTS hnsw_memory_embedding       ON chat_memory USING hnsw (embedding vector_cosine_ops);

-- chat_logs
CREATE INDEX IF NOT EXISTS idx_chat_logs_session_id    ON chat_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_logs_timestamp     ON chat_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_chat_logs_is_indexed    ON chat_logs(is_indexed);

-- ingestion_log
CREATE INDEX IF NOT EXISTS idx_ingestion_log_doc_id    ON ingestion_log(doc_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_log_timestamp ON ingestion_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_ingestion_log_type      ON ingestion_log(event_type);

-- source_sync
CREATE INDEX IF NOT EXISTS idx_source_sync_source      ON source_sync(source);
CREATE INDEX IF NOT EXISTS idx_source_sync_status      ON source_sync(status);
CREATE INDEX IF NOT EXISTS idx_source_sync_started_at  ON source_sync(started_at);

-- ── Views ────────────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW active_documents AS
SELECT doc_id, source, title, uri, author, created_at, updated_at,
       content_hash, processed_at, chunk_count, metadata
FROM documents
WHERE is_deleted = FALSE;

-- ── Helper Functions ─────────────────────────────────────────────────────────

-- Vector similarity search: returns top-k document chunks by cosine similarity.
-- Usage: SELECT * FROM search_documents('[0.1,0.2,...]'::vector, 5);
CREATE OR REPLACE FUNCTION search_documents(
    query_embedding vector(1536),
    k               INTEGER DEFAULT 10
)
RETURNS TABLE (
    id          BIGINT,
    doc_id      VARCHAR(255),
    chunk_index INTEGER,
    text        TEXT,
    source      VARCHAR(100),
    title       TEXT,
    uri         TEXT,
    author      VARCHAR(255),
    score       FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        dc.id, dc.doc_id, dc.chunk_index, dc.text,
        dc.source, dc.title, dc.uri, dc.author,
        1 - (dc.embedding <=> query_embedding) AS score
    FROM document_chunks dc
    JOIN documents d ON dc.doc_id = d.doc_id
    WHERE d.is_deleted = FALSE
    ORDER BY dc.embedding <=> query_embedding
    LIMIT k;
END;
$$ LANGUAGE plpgsql;

-- Vector similarity search over chat memory.
-- Usage: SELECT * FROM search_chat_memory('[0.1,0.2,...]'::vector, 5);
CREATE OR REPLACE FUNCTION search_chat_memory(
    query_embedding vector(1536),
    k               INTEGER DEFAULT 5
)
RETURNS TABLE (
    id          BIGINT,
    session_id  VARCHAR(255),
    chunk_index INTEGER,
    text        TEXT,
    score       FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        cm.id, cm.session_id, cm.chunk_index, cm.text,
        1 - (cm.embedding <=> query_embedding) AS score
    FROM chat_memory cm
    ORDER BY cm.embedding <=> query_embedding
    LIMIT k;
END;
$$ LANGUAGE plpgsql;

-- Document statistics summary.
CREATE OR REPLACE FUNCTION get_document_stats()
RETURNS TABLE (
    total_documents     BIGINT,
    documents_by_source JSONB,
    total_chunks        BIGINT,
    avg_chunks_per_doc  NUMERIC,
    last_processed      TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)::BIGINT,
        jsonb_object_agg(source, cnt),
        SUM(chunk_count)::BIGINT,
        ROUND(AVG(chunk_count), 2),
        MAX(processed_at)
    FROM (
        SELECT source, COUNT(*) AS cnt, SUM(chunk_count) AS chunk_count, MAX(processed_at) AS processed_at
        FROM documents
        WHERE is_deleted = FALSE
        GROUP BY source
    ) s;
END;
$$ LANGUAGE plpgsql;

-- Soft-delete helper: marks documents deleted and cascades to document_chunks.
CREATE OR REPLACE FUNCTION mark_documents_deleted(p_doc_ids TEXT[])
RETURNS INTEGER AS $$
DECLARE updated INTEGER;
BEGIN
    UPDATE documents SET is_deleted = TRUE, updated_at = NOW()
    WHERE doc_id = ANY(p_doc_ids);
    GET DIAGNOSTICS updated = ROW_COUNT;
    RETURN updated;
END;
$$ LANGUAGE plpgsql;

-- Prune old log entries (call periodically via pg_cron or a cron job).
CREATE OR REPLACE FUNCTION cleanup_old_logs()
RETURNS void AS $$
BEGIN
    DELETE FROM ingestion_log  WHERE timestamp    < NOW() - INTERVAL '30 days';
    DELETE FROM source_sync    WHERE status = 'completed' AND completed_at < NOW() - INTERVAL '7 days';
END;
$$ LANGUAGE plpgsql;

-- ── Seed ─────────────────────────────────────────────────────────────────────

INSERT INTO source_sync (source, sync_type, status, completed_at)
VALUES ('system', 'initialization', 'completed', NOW())
ON CONFLICT DO NOTHING;

COMMIT;
