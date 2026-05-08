#!/usr/bin/env python3
"""
Ingest local documents from a specified directory (default: /home/ubuntu/Documents)
into PostgreSQL (pgvector) using Azure OpenAI for embeddings.

Requirements:
- psycopg2
- openai
- python-dotenv
- PyMuPDF (optional, for PDF support)
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import random
import uuid
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv

# Load .env from project root (two levels up)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import psycopg2
from psycopg2.extras import Json
from openai import AzureOpenAI

# Try to import fitz for PDF parsing
try:
    import fitz  # PyMuPDF
    HAVE_FITZ = True
except ImportError:
    HAVE_FITZ = False
    print("Warning: PyMuPDF (fitz) not installed. PDF ingestion will be skipped.")


# ── Config ────────────────────────────────────────────────────────────
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://postgres:postgres@localhost:5432/atlasai")
DEFAULT_DOCS_DIR = "/home/blacksynapse/Documents"

AZURE_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "text-embedding-3-small")
AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")

ID_NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")

CHUNK_SIZE_CHARS = 2200
CHUNK_OVERLAP_CHARS = 250
EMBED_BATCH_SIZE = 64

# ── Utilities ─────────────────────────────────────────────────────────

def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()

def stable_id(*parts: str) -> str:
    """Deterministic UUIDv5 from arbitrary string parts."""
    raw = "|".join(parts)
    return str(uuid.uuid5(ID_NAMESPACE, raw))

# ── Document Parsing ──────────────────────────────────────────────────

def extract_text(file_path: Path) -> str:
    ext = file_path.suffix.lower()
    if ext == ".pdf":
        if not HAVE_FITZ:
            print(f"  [skip] Cannot read PDF without PyMuPDF: {file_path.name}")
            return ""
        try:
            doc = fitz.open(file_path)
            text = "\n".join([page.get_text() for page in doc])
            return text
        except Exception as e:
            print(f"  [error] Failed to parse PDF {file_path.name}: {e}")
            return ""
    elif ext in [".txt", ".md", ".csv", ".json"]:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            try:
                with open(file_path, "r", encoding="latin-1") as f:
                    return f.read()
            except Exception as e:
                print(f"  [error] Failed to read text file {file_path.name}: {e}")
                return ""
    else:
        print(f"  [skip] Unsupported file type: {ext} for file {file_path.name}")
        return ""

# ── Chunking (adapted from qdrant/ingest_azure.py) ────────────────────

def chunk_paragraphs(text: str, max_chars: int, overlap_chars: int) -> list:
    text = text.strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks = []
    cur = []
    cur_len = 0

    def flush():
        nonlocal cur, cur_len
        if cur:
            chunks.append("\n\n".join(cur).strip())
            cur, cur_len = [], 0

    for p in paras:
        p_len = len(p) + (2 if cur else 0)
        if cur_len + p_len <= max_chars:
            cur.append(p)
            cur_len += p_len
        else:
            flush()
            if len(p) > max_chars:
                start = 0
                while start < len(p):
                    chunks.append(p[start:start + max_chars])
                    start += max_chars - overlap_chars
            else:
                cur.append(p)
                cur_len = len(p)
    flush()

    if overlap_chars <= 0 or len(chunks) <= 1:
        return chunks
    overlapped = [chunks[0]]
    for ch in chunks[1:]:
        tail = overlapped[-1][-overlap_chars:]
        overlapped.append((tail + "\n\n" + ch).strip())
    return overlapped

# ── Azure embedding ───────────────────────────────────────────────────

def make_azure_client() -> AzureOpenAI:
    if not AZURE_API_KEY:
        sys.exit("AZURE_OPENAI_API_KEY is not set.")
    if not AZURE_ENDPOINT:
        sys.exit("AZURE_OPENAI_ENDPOINT is not set.")
    return AzureOpenAI(
        api_key=AZURE_API_KEY,
        azure_endpoint=AZURE_ENDPOINT,
        api_version=AZURE_API_VERSION,
    )

def embed_texts(texts: list, client: AzureOpenAI, max_retries: int = 5) -> list:
    last_err = None
    for attempt in range(max_retries):
        try:
            response = client.embeddings.create(
                model=AZURE_DEPLOYMENT,
                input=texts,
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                sleep_s = (2 ** attempt) + random.random()
                print(f"  [embed retry {attempt + 1}/{max_retries}] {e} → sleep {sleep_s:.1f}s")
                time.sleep(sleep_s)
    raise RuntimeError(f"Embedding failed after {max_retries} retries: {last_err}")

# ── Database ──────────────────────────────────────────────────────────

def get_db_connection():
    if not POSTGRES_URL:
        sys.exit("POSTGRES_URL is not set.")
    return psycopg2.connect(POSTGRES_URL)

def process_file(
    file_path: Path, 
    conn, 
    azure: AzureOpenAI, 
    source_name: str = "local_docs"
):
    print(f"\nProcessing: {file_path}")
    text = extract_text(file_path)
    if not text:
        return

    content_hash = sha256_text(text)
    uri = f"file://{file_path.absolute()}"
    doc_id = stable_id(uri)
    
    with conn.cursor() as cur:
        # Check if document exists and content hash matches
        cur.execute("SELECT content_hash, is_deleted FROM documents WHERE doc_id = %s", (doc_id,))
        row = cur.fetchone()
        
        if row:
            db_hash, is_deleted = row
            if db_hash == content_hash and not is_deleted:
                print(f"  [skip] Unchanged: {file_path.name}")
                return
            
            print(f"  [update] Content changed (or undeleted). Re-indexing...")
        else:
            print(f"  [new] Indexing new file...")

        chunks = chunk_paragraphs(text, CHUNK_SIZE_CHARS, CHUNK_OVERLAP_CHARS)
        if not chunks:
            print("  [skip] No chunks extracted.")
            return
            
        print(f"  Generated {len(chunks)} chunks. Computing embeddings...")
        
        embeddings = []
        for i in range(0, len(chunks), EMBED_BATCH_SIZE):
            batch = chunks[i:i + EMBED_BATCH_SIZE]
            batch_emb = embed_texts(batch, azure)
            embeddings.extend(batch_emb)
            
        # Write to Database
        # 1. Delete old chunks if exist
        cur.execute("DELETE FROM document_chunks WHERE doc_id = %s", (doc_id,))
        
        # 2. Upsert document record
        now = datetime.now()
        metadata = {"file_name": file_path.name, "size": file_path.stat().st_size}
        
        cur.execute("""
            INSERT INTO documents (
                doc_id, source, title, uri, created_at, updated_at, 
                content_hash, processed_at, is_deleted, chunk_count, metadata
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, FALSE, %s, %s
            ) ON CONFLICT (doc_id) DO UPDATE SET
                title = EXCLUDED.title,
                updated_at = EXCLUDED.updated_at,
                content_hash = EXCLUDED.content_hash,
                processed_at = EXCLUDED.processed_at,
                is_deleted = FALSE,
                chunk_count = EXCLUDED.chunk_count,
                metadata = EXCLUDED.metadata
        """, (
            doc_id, source_name, file_path.name, uri, now, now,
            content_hash, now, len(chunks), Json(metadata)
        ))
        
        # 3. Insert chunks
        for i, (chunk_text, emb) in enumerate(zip(chunks, embeddings)):
            cur.execute("""
                INSERT INTO document_chunks (
                    doc_id, chunk_index, text, embedding, 
                    source, title, uri, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """, (
                doc_id, i, chunk_text, f"[{','.join(map(str, emb))}]",
                source_name, file_path.name, uri, now, now
            ))
            
        # 4. Log ingestion
        cur.execute("""
            INSERT INTO ingestion_log (doc_id, event_type, message)
            VALUES (%s, 'ingested', %s)
        """, (doc_id, f"Ingested {len(chunks)} chunks"))
        
        conn.commit()
        print(f"  [done] Upserted {len(chunks)} chunks into PostgreSQL.")

# ── Main ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest local documents into PostgreSQL via pgvector and Azure OpenAI."
    )
    parser.add_argument(
        "--dir", default=DEFAULT_DOCS_DIR,
        help=f"Directory containing documents to ingest (default: {DEFAULT_DOCS_DIR})"
    )
    args = parser.parse_args()

    docs_dir = Path(args.dir)
    if not docs_dir.exists() or not docs_dir.is_dir():
        sys.exit(f"Error: Directory '{docs_dir}' does not exist.")

    print(f"Starting ingestion from: {docs_dir}")
    print(f"Using Postgres: {POSTGRES_URL.split('@')[-1]}")
    print(f"Azure Deployment: {AZURE_DEPLOYMENT}\n")

    azure = make_azure_client()
    try:
        conn = get_db_connection()
    except Exception as e:
        sys.exit(f"Failed to connect to Postgres: {e}")

    processed_count = 0
    # Recursively find files
    for file_path in docs_dir.rglob("*"):
        if file_path.is_file() and not file_path.name.startswith("."):
            try:
                process_file(file_path, conn, azure)
                processed_count += 1
            except Exception as e:
                print(f"  [error] Unhandled error processing {file_path.name}: {e}")
                conn.rollback()

    conn.close()
    print(f"\nIngestion complete. Processed {processed_count} files.")

if __name__ == "__main__":
    main()
