"""
AtlasAI Ingestion Pipeline

Core pipeline for processing documents through the ingestion workflow:
- Deduplication via content hashing
- Text chunking
- Embedding generation
- Vector storage in Postgres (pgvector)
- Metadata tracking in Postgres
"""

import hashlib
import logging
import asyncio
import os
from typing import Dict, Any, List, Optional
from datetime import datetime
import json

import psycopg2
from psycopg2.extras import RealDictCursor
import tiktoken

from .utils import chunk_text, get_embedding, setup_logging

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """Main pipeline class for document processing and ingestion."""

    def __init__(self):
        """Initialize the ingestion pipeline with database connections."""
        # openai_client is optional — only used for GPT-4o Vision in PDF processing
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            import openai, httpx as _httpx
            self.openai_client = openai.OpenAI(api_key=api_key, http_client=_httpx.Client())
        else:
            self.openai_client = None

        self.postgres_url = os.getenv("POSTGRES_URL")
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")

        self.tokenizer = tiktoken.get_encoding("cl100k_base")

        self.embedding_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
        model_dim_override = os.getenv("EMBEDDING_DIM")
        if model_dim_override:
            try:
                self.embedding_dim = int(model_dim_override)
            except ValueError:
                logger.warning("Invalid EMBEDDING_DIM value '%s', falling back to model default", model_dim_override)
                self.embedding_dim = 1536
        else:
            self.embedding_dim = 3072 if self.embedding_model == "text-embedding-3-large" else 1536

        logger.info("Using embedding model: %s (dim=%d)", self.embedding_model, self.embedding_dim)

        asyncio.create_task(self._initialize())

    async def _initialize(self):
        """Initialize database connections and create tables if needed."""
        try:
            await self._ensure_postgres_tables()
            logger.info("Pipeline initialization completed successfully")
        except Exception as e:
            logger.error("Pipeline initialization failed: %s", e)
            raise

    async def _ensure_postgres_tables(self):
        """Ensure all Postgres tables exist."""
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                    cur.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")

                    cur.execute("""
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
                        )
                    """)

                    cur.execute(f"""
                        CREATE TABLE IF NOT EXISTS document_chunks (
                            id          BIGSERIAL     PRIMARY KEY,
                            doc_id      VARCHAR(255)  NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
                            chunk_index INTEGER       NOT NULL,
                            text        TEXT          NOT NULL,
                            embedding   vector({self.embedding_dim}),
                            source      VARCHAR(100),
                            title       TEXT,
                            uri         TEXT,
                            author      VARCHAR(255),
                            created_at  TIMESTAMPTZ,
                            updated_at  TIMESTAMPTZ,
                            UNIQUE (doc_id, chunk_index)
                        )
                    """)

                    cur.execute(f"""
                        CREATE TABLE IF NOT EXISTS chat_memory (
                            id          BIGSERIAL     PRIMARY KEY,
                            session_id  VARCHAR(255)  NOT NULL,
                            chunk_index INTEGER       NOT NULL DEFAULT 0,
                            text        TEXT          NOT NULL,
                            embedding   vector({self.embedding_dim}),
                            created_at  TIMESTAMPTZ   DEFAULT NOW(),
                            UNIQUE (session_id, chunk_index)
                        )
                    """)

                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS chat_logs (
                            id          BIGSERIAL     PRIMARY KEY,
                            session_id  VARCHAR(255)  NOT NULL,
                            role        VARCHAR(50)   NOT NULL,
                            message     TEXT          NOT NULL,
                            timestamp   TIMESTAMPTZ   DEFAULT NOW(),
                            is_indexed  BOOLEAN       DEFAULT FALSE,
                            metadata    JSONB         DEFAULT '{}'::jsonb
                        )
                    """)

                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS ingestion_log (
                            id          BIGSERIAL     PRIMARY KEY,
                            doc_id      VARCHAR(255)  NOT NULL,
                            event_type  VARCHAR(50)   NOT NULL,
                            message     TEXT,
                            timestamp   TIMESTAMPTZ   DEFAULT NOW(),
                            metadata    JSONB         DEFAULT '{}'::jsonb
                        )
                    """)

                    cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_source        ON documents(source)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_content_hash  ON documents(content_hash)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_processed_at  ON documents(processed_at)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_is_deleted    ON documents(is_deleted)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc_id           ON document_chunks(doc_id)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source           ON document_chunks(source)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_logs_session_id    ON chat_logs(session_id)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_logs_timestamp     ON chat_logs(timestamp)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_logs_is_indexed    ON chat_logs(is_indexed)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_ingestion_log_doc_id    ON ingestion_log(doc_id)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_ingestion_log_timestamp ON ingestion_log(timestamp)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_memory_session_id       ON chat_memory(session_id)")

                    conn.commit()
                    logger.info("Postgres tables ensured successfully")
        except Exception as e:
            logger.error("Failed to ensure Postgres tables: %s", e)
            raise

    async def check_postgres_connection(self) -> bool:
        """Check if Postgres connection is healthy."""
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    return True
        except Exception as e:
            logger.error("Postgres connection check failed: %s", e)
            return False

    def _compute_content_hash(self, text: str) -> str:
        """Compute SHA-256 hash of document content for deduplication."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def _is_document_unchanged(self, doc_id: str, content_hash: str) -> bool:
        """Check if document content has changed since last processing."""
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT content_hash FROM documents WHERE doc_id = %s", (doc_id,))
                    result = cur.fetchone()
                    return bool(result and result[0] == content_hash)
        except Exception as e:
            logger.error("Failed to check document unchanged status: %s", e)
            return False

    async def _log_ingestion_event(self, doc_id: str, event_type: str, message: str, metadata: Dict = None):
        """Log an ingestion event to the database."""
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO ingestion_log (doc_id, event_type, message, metadata) VALUES (%s, %s, %s, %s)",
                        (doc_id, event_type, message, json.dumps(metadata or {})),
                    )
                    conn.commit()
        except Exception as e:
            logger.error("Failed to log ingestion event: %s", e)

    @staticmethod
    def _vec_str(embedding: List[float]) -> str:
        """Format a float list as a pgvector literal: '[0.1,0.2,...]'."""
        return "[" + ",".join(map(str, embedding)) + "]"

    async def process_document(self, document: Any, force_reindex: bool = False) -> Dict[str, Any]:
        """
        Process a single document through the ingestion pipeline.

        Args:
            document: DocumentPayload object
            force_reindex: If True, process even if content hasn't changed

        Returns:
            Dict with success status, chunks processed, and any errors
        """
        try:
            content_hash = self._compute_content_hash(document.text)

            if not force_reindex and await self._is_document_unchanged(document.doc_id, content_hash):
                await self._log_ingestion_event(
                    document.doc_id, "skipped", "Document content unchanged, skipping processing"
                )
                return {"success": True, "chunks_processed": 0, "message": "Document unchanged, skipped processing"}

            chunks = chunk_text(document.text, self.tokenizer)
            logger.info("Chunked document %s into %d chunks", document.doc_id, len(chunks))

            chunk_texts = [chunk["text"] for chunk in chunks]
            embeddings = await get_embedding(chunk_texts, ollama_url=self.ollama_url, model=self.embedding_model)

            # Upsert document metadata first (chunks reference it via FK)
            await self._update_document_metadata(document, content_hash, len(chunks))

            # Replace all chunks for this document
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM document_chunks WHERE doc_id = %s", (document.doc_id,))

                    for i, chunk in enumerate(chunks):
                        embedding_val = self._vec_str(embeddings[i])
                        cur.execute(
                            """
                            INSERT INTO document_chunks
                                (doc_id, chunk_index, text, embedding, source, title, uri, author, created_at, updated_at)
                            VALUES (%s, %s, %s, %s::vector, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (doc_id, chunk_index) DO UPDATE SET
                                text      = EXCLUDED.text,
                                embedding = EXCLUDED.embedding
                            """,
                            (
                                document.doc_id,
                                i,
                                chunk["text"],
                                embedding_val,
                                document.source,
                                document.title,
                                document.uri,
                                document.author,
                                document.created_at,
                                document.updated_at,
                            ),
                        )
                    conn.commit()

            await self._log_ingestion_event(
                document.doc_id,
                "processed",
                f"Successfully processed {len(chunks)} chunks",
                {"chunks_processed": len(chunks), "content_hash": content_hash},
            )

            return {"success": True, "chunks_processed": len(chunks), "message": f"Successfully processed {len(chunks)} chunks"}

        except Exception as e:
            error_msg = f"Failed to process document {document.doc_id}: {str(e)}"
            logger.error(error_msg)
            await self._log_ingestion_event(document.doc_id, "error", error_msg, {"error": str(e)})
            return {"success": False, "chunks_processed": 0, "error": error_msg}

    async def _update_document_metadata(self, document: Any, content_hash: str, chunk_count: int):
        """Upsert document metadata in Postgres."""
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO documents
                            (doc_id, source, title, uri, author, created_at, updated_at,
                             content_hash, chunk_count, is_deleted)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE)
                        ON CONFLICT (doc_id) DO UPDATE SET
                            source       = EXCLUDED.source,
                            title        = EXCLUDED.title,
                            uri          = EXCLUDED.uri,
                            author       = EXCLUDED.author,
                            created_at   = EXCLUDED.created_at,
                            updated_at   = EXCLUDED.updated_at,
                            content_hash = EXCLUDED.content_hash,
                            chunk_count  = EXCLUDED.chunk_count,
                            processed_at = NOW(),
                            is_deleted   = FALSE
                        """,
                        (
                            document.doc_id,
                            document.source,
                            document.title,
                            document.uri,
                            document.author,
                            document.created_at,
                            document.updated_at,
                            content_hash,
                            chunk_count,
                        ),
                    )
                    conn.commit()
        except Exception as e:
            logger.error("Failed to update document metadata: %s", e)
            raise

    async def get_document_by_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve document metadata by ID."""
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT doc_id, source, title, uri, author, created_at, updated_at FROM documents WHERE doc_id = %s AND is_deleted = FALSE",
                        (doc_id,),
                    )
                    result = cur.fetchone()
                    return dict(result) if result else None
        except Exception as e:
            logger.error("Failed to get document by ID: %s", e)
            return None

    async def sync_source(self, source: str) -> Dict[str, Any]:
        """Perform full synchronization for a data source (placeholder)."""
        try:
            await self._log_ingestion_event(source, "sync_started", f"Full sync started for source: {source}")
            documents_processed = 0
            documents_deleted = 0
            await self._log_ingestion_event(
                source,
                "sync_completed",
                f"Sync completed: {documents_processed} processed, {documents_deleted} deleted",
                {"documents_processed": documents_processed, "documents_deleted": documents_deleted},
            )
            return {"documents_processed": documents_processed, "documents_deleted": documents_deleted, "errors": []}
        except Exception as e:
            logger.error("Sync failed for source %s: %s", source, e)
            return {"documents_processed": 0, "documents_deleted": 0, "errors": [str(e)]}

    async def get_ingest_status(self, limit: int = 20) -> Dict[str, Any]:
        """Return recent ingestion log entries and per-document chunk counts."""
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT il.doc_id, il.event_type, il.message, il.timestamp, il.metadata,
                               d.source, d.title, d.chunk_count
                        FROM ingestion_log il
                        LEFT JOIN documents d ON il.doc_id = d.doc_id
                        ORDER BY il.timestamp DESC
                        LIMIT %s
                    """, (limit,))
                    events = [dict(r) for r in cur.fetchall()]

                    cur.execute("SELECT COUNT(*) FROM documents WHERE is_deleted = FALSE")
                    doc_count = cur.fetchone()["count"]

                    cur.execute("SELECT COUNT(*) FROM document_chunks")
                    chunk_count = cur.fetchone()["count"]

            return {"total_documents": doc_count, "total_chunks": chunk_count, "recent_events": events}
        except Exception as e:
            logger.error("Failed to get ingest status: %s", e)
            return {"error": str(e)}

    async def get_document_chunks(self, doc_id: str) -> Dict[str, Any]:
        """Return all stored chunks for a document, with a text preview per chunk."""
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT doc_id, source, title, chunk_count, processed_at FROM documents WHERE doc_id = %s",
                        (doc_id,),
                    )
                    doc = cur.fetchone()
                    if not doc:
                        return {"error": f"Document '{doc_id}' not found"}

                    cur.execute(
                        """
                        SELECT chunk_index,
                               LEFT(text, 200) AS preview,
                               LENGTH(text)    AS char_count,
                               embedding IS NOT NULL AS has_embedding
                        FROM document_chunks
                        WHERE doc_id = %s
                        ORDER BY chunk_index
                        """,
                        (doc_id,),
                    )
                    chunks = [dict(r) for r in cur.fetchall()]

            return {"document": dict(doc), "chunks": chunks}
        except Exception as e:
            logger.error("Failed to get document chunks: %s", e)
            return {"error": str(e)}

    async def log_chat_message(self, session_id: str, role: str, message: str, meta: Dict = None) -> bool:
        """Log a chat message to Postgres."""
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO chat_logs (session_id, role, message, metadata) VALUES (%s, %s, %s, %s)",
                        (session_id, role, message, json.dumps(meta or {})),
                    )
                    conn.commit()
            return True
        except Exception as e:
            logger.error("Failed to log chat message: %s", e)
            return False

    async def consolidate_chat_memory(self) -> Dict[str, Any]:
        """
        Consolidate unindexed chat logs into long-term vector memory in Postgres.
        Groups logs by session, chunks them, embeds, and upserts to chat_memory.
        """
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT id, session_id, role, message, timestamp FROM chat_logs WHERE is_indexed = FALSE ORDER BY session_id, timestamp ASC"
                    )
                    logs = cur.fetchall()

            if not logs:
                return {"processed_count": 0, "sessions_processed": 0, "message": "No new logs to consolidate"}

            sessions: Dict[str, list] = {}
            for log in logs:
                sid = log["session_id"]
                sessions.setdefault(sid, []).append(log)

            processed_count = 0
            sessions_processed = 0
            ids_to_mark_indexed = []

            for sid, session_logs in sessions.items():
                full_text = "\n".join([f"{l['role'].title()}: {l['message']}" for l in session_logs])
                if not full_text.strip():
                    continue

                chunks = chunk_text(full_text, self.tokenizer)

                chunk_texts = [chunk["text"] for chunk in chunks]
                embeddings = await get_embedding(chunk_texts, ollama_url=self.ollama_url, model=self.embedding_model)

                with psycopg2.connect(self.postgres_url) as conn:
                    with conn.cursor() as cur:
                        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                            cur.execute(
                                """
                                INSERT INTO chat_memory (session_id, chunk_index, text, embedding)
                                VALUES (%s, %s, %s, %s::vector)
                                ON CONFLICT (session_id, chunk_index) DO UPDATE SET
                                    text      = EXCLUDED.text,
                                    embedding = EXCLUDED.embedding
                                """,
                                (sid, i, chunk["text"], self._vec_str(embedding)),
                            )
                        conn.commit()

                processed_count += len(session_logs)
                sessions_processed += 1
                ids_to_mark_indexed.extend([l["id"] for l in session_logs])

            if ids_to_mark_indexed:
                with psycopg2.connect(self.postgres_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE chat_logs SET is_indexed = TRUE WHERE id = ANY(%s)",
                            (ids_to_mark_indexed,),
                        )
                        conn.commit()

            return {
                "processed_count": processed_count,
                "sessions_processed": sessions_processed,
                "message": f"Consolidated {processed_count} messages from {sessions_processed} sessions",
            }

        except Exception as e:
            logger.error("Chat memory consolidation failed: %s", e)
            return {"error": str(e)}
