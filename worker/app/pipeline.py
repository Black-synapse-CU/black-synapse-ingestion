"""
AtlasAI Ingestion Pipeline (pgvector backend)

Core pipeline for processing documents through the ingestion workflow:
- Deduplication via content hashing
- Text chunking
- Embedding generation
- Vector storage in pgvector (Postgres with vector extension)
- Metadata tracking in Postgres
"""

import hashlib
import logging
import asyncio
import os
import json
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor
import tiktoken

from .utils import chunk_text, get_embedding_azure, setup_logging

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """Main pipeline class for document processing and ingestion."""

    def __init__(self):
        self.postgres_url = os.getenv("POSTGRES_URL")
        self.tokenizer = tiktoken.get_encoding("cl100k_base")

        self.azure_api_key = os.getenv("AZURE_OPENAI_API_KEY")
        self.azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        self.azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "text-embedding-3-small")
        self.azure_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
        self.embedding_dim = int(os.getenv("EMBEDDING_DIM", "1536"))

        # Optional OpenAI client — only used for PDF image vision fallback
        # when use_openai_vision=True is passed to /ingest/pdf
        try:
            import openai, httpx as _httpx
            api_key = os.getenv("OPENAI_API_KEY")
            self.openai_client = openai.OpenAI(api_key=api_key, http_client=_httpx.Client()) if api_key else None
        except ImportError:
            self.openai_client = None

        asyncio.create_task(self._initialize())

    async def _initialize(self):
        try:
            await self._ensure_schema()
            logger.info("Pipeline initialization completed successfully")
        except Exception as e:
            logger.error("Pipeline initialization failed: %s", e)
            raise

    async def _ensure_schema(self):
        """Enable pgvector and create all tables + indexes."""
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT pg_catalog.format_type(a.atttypid, a.atttypmod)
                        FROM pg_attribute a JOIN pg_class c ON a.attrelid = c.oid
                        WHERE c.relname = 'document_chunks'
                          AND a.attname = 'embedding' AND a.attnum > 0
                    """)
                    row = cur.fetchone()
                    if row and row[0].startswith("vector("):
                        existing_dim = int(row[0][7:-1])
                        if existing_dim != self.embedding_dim:
                            cur.execute("DROP TABLE IF EXISTS document_chunks")
                            cur.execute("DROP TABLE IF EXISTS chat_memory_chunks")
                            conn.commit()
        except Exception:
            pass
        max_attempts = 10
        delay = 1
        for attempt in range(1, max_attempts + 1):
            try:
                with psycopg2.connect(self.postgres_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")

                        cur.execute("""
                            CREATE TABLE IF NOT EXISTS documents (
                                doc_id VARCHAR(255) PRIMARY KEY,
                                source VARCHAR(100) NOT NULL,
                                title TEXT,
                                uri TEXT,
                                author VARCHAR(255),
                                created_at TIMESTAMP WITH TIME ZONE,
                                updated_at TIMESTAMP WITH TIME ZONE,
                                content_hash VARCHAR(64),
                                processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                                is_deleted BOOLEAN DEFAULT FALSE,
                                chunk_count INTEGER DEFAULT 0
                            )
                        """)

                        cur.execute(f"""
                            CREATE TABLE IF NOT EXISTS document_chunks (
                                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                                doc_id VARCHAR(255) NOT NULL,
                                chunk_index INTEGER NOT NULL,
                                text TEXT NOT NULL,
                                embedding vector({self.embedding_dim}),
                                source VARCHAR(100),
                                title TEXT,
                                uri TEXT,
                                author VARCHAR(255),
                                created_at TEXT,
                                updated_at TEXT
                            )
                        """)

                        cur.execute(f"""
                            CREATE TABLE IF NOT EXISTS chat_memory_chunks (
                                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                                session_id VARCHAR(255) NOT NULL,
                                text TEXT NOT NULL,
                                embedding vector({self.embedding_dim}),
                                timestamp_start TIMESTAMP WITH TIME ZONE,
                                timestamp_end TIMESTAMP WITH TIME ZONE,
                                log_ids JSONB,
                                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                            )
                        """)

                        cur.execute("""
                            CREATE TABLE IF NOT EXISTS ingestion_log (
                                id SERIAL PRIMARY KEY,
                                doc_id VARCHAR(255) NOT NULL,
                                event_type VARCHAR(50) NOT NULL,
                                message TEXT,
                                timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                                metadata JSONB
                            )
                        """)

                        cur.execute("""
                            CREATE TABLE IF NOT EXISTS chat_logs (
                                id SERIAL PRIMARY KEY,
                                session_id VARCHAR(255) NOT NULL,
                                role VARCHAR(50) NOT NULL,
                                message TEXT NOT NULL,
                                timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                                is_indexed BOOLEAN DEFAULT FALSE,
                                metadata JSONB
                            )
                        """)

                        cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source)")
                        cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash)")
                        cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_processed_at ON documents(processed_at)")
                        cur.execute("CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc_id ON document_chunks(doc_id)")
                        cur.execute("CREATE INDEX IF NOT EXISTS idx_ingestion_log_doc_id ON ingestion_log(doc_id)")
                        cur.execute("CREATE INDEX IF NOT EXISTS idx_ingestion_log_timestamp ON ingestion_log(timestamp)")
                        cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_logs_session_id ON chat_logs(session_id)")
                        cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_logs_timestamp ON chat_logs(timestamp)")
                        cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_logs_is_indexed ON chat_logs(is_indexed)")
                        cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_memory_session_id ON chat_memory_chunks(session_id)")

                        conn.commit()
                        logger.info("pgvector schema ensured successfully")
                return
            except Exception as e:
                logger.warning("Attempt %d/%d — failed to set up schema: %s", attempt, max_attempts, e)
                if attempt == max_attempts:
                    raise
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10)

    async def check_postgres_connection(self) -> bool:
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    return True
        except Exception as e:
            logger.error("Postgres connection check failed: %s", e)
            return False

    def _compute_content_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def _is_document_unchanged(self, doc_id: str, content_hash: str) -> bool:
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT content_hash FROM documents WHERE doc_id = %s",
                        (doc_id,)
                    )
                    result = cur.fetchone()
                    return bool(result and result[0] == content_hash)
        except Exception as e:
            logger.error("Failed to check document unchanged status: %s", e)
            return False

    async def _log_ingestion_event(self, doc_id: str, event_type: str, message: str, metadata: Dict = None):
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO ingestion_log (doc_id, event_type, message, metadata)
                        VALUES (%s, %s, %s, %s)
                    """, (doc_id, event_type, message, json.dumps(metadata or {})))
                    conn.commit()
        except Exception as e:
            logger.error("Failed to log ingestion event: %s", e)

    async def process_document(self, document: Any, force_reindex: bool = False) -> Dict[str, Any]:
        """
        Process a document through the full pipeline.

        Deduplicates via content hash, chunks, embeds, and upserts
        vectors + metadata into pgvector / Postgres.
        """
        try:
            content_hash = self._compute_content_hash(document.text)

            if not force_reindex and await self._is_document_unchanged(document.doc_id, content_hash):
                await self._log_ingestion_event(
                    document.doc_id, "skipped", "Document content unchanged, skipping processing"
                )
                return {
                    "success": True,
                    "chunks_processed": 0,
                    "message": "Document unchanged, skipped processing",
                }

            chunks = chunk_text(document.text, self.tokenizer)
            logger.info("Chunked document %s into %d chunks", document.doc_id, len(chunks))

            chunk_texts = [c["text"] for c in chunks]
            embeddings = await get_embedding_azure(
                chunk_texts,
                api_key=self.azure_api_key,
                endpoint=self.azure_endpoint,
                deployment=self.azure_deployment,
                api_version=self.azure_api_version,
            )

            await self._update_document_metadata(document, content_hash, len(chunks))

            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM document_chunks WHERE doc_id = %s", (document.doc_id,))
                    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                        cur.execute("""
                            INSERT INTO document_chunks
                                (doc_id, chunk_index, text, embedding,
                                 source, title, uri, author, created_at, updated_at)
                            VALUES (%s, %s, %s, %s::vector, %s, %s, %s, %s, %s, %s)
                        """, (
                            document.doc_id,
                            i,
                            chunk["text"],
                            json.dumps(embedding),
                            document.source,
                            document.title,
                            document.uri,
                            document.author,
                            document.created_at,
                            document.updated_at,
                        ))
                    conn.commit()
            await self._log_ingestion_event(
                document.doc_id, "processed",
                f"Successfully processed {len(chunks)} chunks",
                {"chunks_processed": len(chunks), "content_hash": content_hash},
            )

            return {
                "success": True,
                "chunks_processed": len(chunks),
                "message": f"Successfully processed {len(chunks)} chunks",
            }

        except Exception as e:
            error_msg = f"Failed to process document {document.doc_id}: {e}"
            logger.error(error_msg)
            await self._log_ingestion_event(document.doc_id, "error", error_msg, {"error": str(e)})
            return {"success": False, "chunks_processed": 0, "error": error_msg}

    async def _update_document_metadata(self, document: Any, content_hash: str, chunk_count: int):
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO documents
                            (doc_id, source, title, uri, author, created_at, updated_at,
                             content_hash, chunk_count, is_deleted)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (doc_id) DO UPDATE SET
                            source        = EXCLUDED.source,
                            title         = EXCLUDED.title,
                            uri           = EXCLUDED.uri,
                            author        = EXCLUDED.author,
                            created_at    = EXCLUDED.created_at,
                            updated_at    = EXCLUDED.updated_at,
                            content_hash  = EXCLUDED.content_hash,
                            chunk_count   = EXCLUDED.chunk_count,
                            processed_at  = NOW(),
                            is_deleted    = FALSE
                    """, (
                        document.doc_id,
                        document.source,
                        document.title,
                        document.uri,
                        document.author,
                        document.created_at,
                        document.updated_at,
                        content_hash,
                        chunk_count,
                        False,
                    ))
                    conn.commit()
        except Exception as e:
            logger.error("Failed to update document metadata: %s", e)
            raise

    async def get_document_by_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT doc_id, source, title, uri, author, created_at, updated_at
                        FROM documents WHERE doc_id = %s AND is_deleted = FALSE
                    """, (doc_id,))
                    result = cur.fetchone()
                    return dict(result) if result else None
        except Exception as e:
            logger.error("Failed to get document by ID: %s", e)
            return None

    async def sync_source(self, source: str) -> Dict[str, Any]:
        try:
            await self._log_ingestion_event(source, "sync_started", f"Full sync started for source: {source}")
            await self._log_ingestion_event(
                source, "sync_completed", "Sync completed: 0 processed, 0 deleted",
                {"documents_processed": 0, "documents_deleted": 0},
            )
            return {"documents_processed": 0, "documents_deleted": 0, "errors": []}
        except Exception as e:
            logger.error("Sync failed for source %s: %s", source, e)
            return {"documents_processed": 0, "documents_deleted": 0, "errors": [str(e)]}

    async def log_chat_message(self, session_id: str, role: str, message: str, meta: Dict = None) -> bool:
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO chat_logs (session_id, role, message, metadata)
                        VALUES (%s, %s, %s, %s)
                    """, (session_id, role, message, json.dumps(meta or {})))
                    conn.commit()
            return True
        except Exception as e:
            logger.error("Failed to log chat message: %s", e)
            return False

    async def consolidate_chat_memory(self) -> Dict[str, Any]:
        """Consolidate unindexed chat logs into long-term vector memory."""
        try:
            with psycopg2.connect(self.postgres_url) as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT id, session_id, role, message, timestamp
                        FROM chat_logs
                        WHERE is_indexed = FALSE
                        ORDER BY session_id, timestamp ASC
                    """)
                    logs = cur.fetchall()

            if not logs:
                return {"processed_count": 0, "sessions_processed": 0, "message": "No new logs to consolidate"}

            sessions: Dict[str, list] = {}
            for log in logs:
                sessions.setdefault(log["session_id"], []).append(log)

            processed_count = 0
            sessions_processed = 0
            ids_to_mark_indexed = []

            for sid, session_logs in sessions.items():
                full_text = "\n".join([f"{l['role'].title()}: {l['message']}" for l in session_logs])
                if not full_text.strip():
                    continue

                chunks = chunk_text(full_text, self.tokenizer)

                chunk_texts = [c["text"] for c in chunks]
                embeddings = await get_embedding_azure(
                    chunk_texts,
                    api_key=self.azure_api_key,
                    endpoint=self.azure_endpoint,
                    deployment=self.azure_deployment,
                    api_version=self.azure_api_version,
                )

                with psycopg2.connect(self.postgres_url) as conn:
                    with conn.cursor() as cur:
                        for chunk, embedding in zip(chunks, embeddings):
                            cur.execute("""
                                INSERT INTO chat_memory_chunks
                                    (session_id, text, embedding, timestamp_start, timestamp_end, log_ids)
                                VALUES (%s, %s, %s::vector, %s, %s, %s)
                            """, (
                                sid,
                                chunk["text"],
                                json.dumps(embedding),
                                session_logs[0]["timestamp"],
                                session_logs[-1]["timestamp"],
                                json.dumps([l["id"] for l in session_logs]),
                            ))
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
