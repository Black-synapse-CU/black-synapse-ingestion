"""
AtlasAI Worker

FastAPI application for processing and embedding data from various sources for the SPOT robot.
Handles ingestion, deduplication, chunking, and vector storage to power the robot's AI capabilities.
"""

import os
import logging
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import httpx

from .pipeline import IngestionPipeline
from .utils import setup_logging
from .scraper import scrape_url
from .qr_analyzer import decode_qr_from_bytes, decode_qr_from_base64, classify_qr_content
from .pdf_processor import extract_pdf_content
import asyncio
import uuid
from datetime import datetime

try:
    from action_servos import ServoOrchestrator as _ServoOrchestrator
    from action_servos.config import DEFAULT_I2C_BUS, DEFAULT_PCA9685_ADDRESS, DEFAULT_PWM_FREQUENCY_HZ
    from .arm_actions import execute_action as _execute_arm_action
    _SERVOS_AVAILABLE = True
except ModuleNotFoundError:
    _ServoOrchestrator = None  # type: ignore[assignment,misc]
    _execute_arm_action = None  # type: ignore[assignment]
    _SERVOS_AVAILABLE = False

# Load environment variables
load_dotenv()

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="AtlasAI Worker",
    description="ETL pipeline for processing and embedding data from various sources for the SPOT robot's AI system",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize ingestion pipeline
pipeline = IngestionPipeline()

# Arm servo orchestrator — None if hardware unavailable (worker still starts cleanly)
if _SERVOS_AVAILABLE:
    _servo_bus = int(os.getenv("SERVO_I2C_BUS", str(DEFAULT_I2C_BUS)))
    try:
        _arm_orch = _ServoOrchestrator()
        _arm_orch.open(_servo_bus, DEFAULT_PCA9685_ADDRESS, DEFAULT_PWM_FREQUENCY_HZ)
        logger.info("Servo orchestrator opened on I2C bus %d", _servo_bus)
    except Exception as _e:
        _arm_orch = None
        logger.warning("Servo hardware unavailable — /arm/action will return 503: %s", _e)
else:
    _arm_orch = None
    logger.warning("action_servos module not found — /arm/action will return 503")

# Service URLs for acknowledge endpoint
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qcwind/qwen2.5-7B-instruct-Q4_K_M:latest")
KOKORO_URL = os.getenv("KOKORO_URL", "http://localhost:8880")
KOKORO_VOICE = os.getenv("KOKORO_VOICE", "af_heart")
SPEAKER_URL = os.getenv("SPEAKER_URL", "http://localhost:8001")

# Pydantic models for API requests/responses
class DocumentPayload(BaseModel):
    """Unified document schema for ingestion."""
    doc_id: str = Field(..., description="Unique document identifier")
    source: str = Field(..., description="Data source (e.g., 'notion', 'gmail', 'slack')")
    title: str = Field(..., description="Document title")
    uri: str = Field(..., description="Document URI or URL")
    text: str = Field(..., description="Document content text")
    author: str = Field(..., description="Document author")
    created_at: str = Field(..., description="Creation timestamp (ISO format)")
    updated_at: str = Field(..., description="Last update timestamp (ISO format)")

class UserIngestRequest(BaseModel):
    """Schema for user profile ingestion request."""
    name: str = Field(..., description="Name of the user")
    url: str = Field(None, description="URL to scrape for user info")
    bio: str = Field(None, description="Directly provided biography or context")
    scraping_consent: bool = Field(False, description="Explicit user consent to scrape the provided URL")

class IngestionResponse(BaseModel):
    """Response model for ingestion operations."""
    success: bool
    message: str
    doc_id: str
    chunks_processed: int = 0
    error: str = None

class SyncResponse(BaseModel):
    """Response model for sync operations."""
    success: bool
    message: str
    documents_processed: int = 0
    documents_deleted: int = 0
    errors: List[str] = []

class QRCodeResult(BaseModel):
    """Result for a single decoded QR code value."""
    value: str
    content_type: str  # "url" or "text"
    ingested: bool
    doc_id: Optional[str] = None
    chunks_processed: int = 0
    error: Optional[str] = None

class QRAnalyzeResponse(BaseModel):
    """Response model for QR code analysis."""
    success: bool
    qr_codes_found: int
    results: List[QRCodeResult]
    message: str


class PDFIngestResponse(BaseModel):
    """Response model for PDF ingestion."""
    success: bool
    message: str
    doc_id: str
    pages: int = 0
    images_described: int = 0
    chunks_processed: int = 0
    error: str = None
class ChatLogPayload(BaseModel):
    """Schema for chat log ingestion."""
    session_id: str = Field(..., description="Unique session identifier")
    role: str = Field(..., description="Role of the message sender (user/assistant)")
    message: str = Field(..., description="Content of the message")
    timestamp: Optional[datetime] = Field(None, description="Timestamp of the message")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

class ChatConsolidationResponse(BaseModel):
    """Response model for chat consolidation."""
    success: bool
    processed_count: int
    sessions_processed: int
    message: str
    error: str = None

@app.get("/")
async def root():
    """Health check endpoint."""
    return {"message": "AtlasAI Worker - SPOT Robot AI System", "status": "healthy"}

@app.get("/health")
async def health_check():
    """Detailed health check including database connections."""
    try:
        postgres_healthy = await pipeline.check_postgres_connection()

        return {
            "status": "healthy" if postgres_healthy else "unhealthy",
            "postgres": "connected" if postgres_healthy else "disconnected",
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Health check failed: {str(e)}")

@app.get("/debug/ingest")
async def debug_ingest_status(limit: int = 20):
    """
    Recent ingestion activity and overall chunk/document counts.

    Query params:
      limit  Number of recent log entries to return (default 20)
    """
    return await pipeline.get_ingest_status(limit=limit)


@app.get("/debug/chunks/{doc_id}")
async def debug_document_chunks(doc_id: str):
    """
    Show every stored chunk for a document: index, character count, text preview,
    and whether an embedding was saved.
    """
    result = await pipeline.get_document_chunks(doc_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.post("/ingest", response_model=IngestionResponse)
async def ingest_document(
    document: DocumentPayload,
    background_tasks: BackgroundTasks
):
    """
    Ingest a single document.
    
    Processes the document through the full pipeline:
    1. Validates payload
    2. Deduplicates via content hash
    3. Chunks text
    4. Generates embeddings
    5. Upserts to Qdrant
    6. Logs to Postgres
    """
    try:
        logger.info(f"Processing document: {document.doc_id} from {document.source}")
        
        # Process document through pipeline
        result = await pipeline.process_document(document)
        
        if result["success"]:
            logger.info(f"Successfully processed document {document.doc_id}: {result['chunks_processed']} chunks")
            return IngestionResponse(
                success=True,
                message="Document processed successfully",
                doc_id=document.doc_id,
                chunks_processed=result["chunks_processed"]
            )
        else:
            logger.error(f"Failed to process document {document.doc_id}: {result['error']}")
            return IngestionResponse(
                success=False,
                message="Document processing failed",
                doc_id=document.doc_id,
                error=result["error"]
            )
            
    except Exception as e:
        logger.error(f"Unexpected error processing document {document.doc_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

@app.post("/reindex", response_model=IngestionResponse)
async def reindex_document(
    doc_id: str,
    background_tasks: BackgroundTasks
):
    """
    Re-index an existing document.
    
    Re-processes a document that already exists in the system,
    useful for updating embeddings or fixing processing errors.
    """
    try:
        logger.info(f"Re-indexing document: {doc_id}")
        
        # Retrieve document from database
        document = await pipeline.get_document_by_id(doc_id)
        if not document:
            raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
        
        # Process document through pipeline
        result = await pipeline.process_document(document, force_reindex=True)
        
        if result["success"]:
            logger.info(f"Successfully re-indexed document {doc_id}: {result['chunks_processed']} chunks")
            return IngestionResponse(
                success=True,
                message="Document re-indexed successfully",
                doc_id=doc_id,
                chunks_processed=result["chunks_processed"]
            )
        else:
            logger.error(f"Failed to re-index document {doc_id}: {result['error']}")
            return IngestionResponse(
                success=False,
                message="Document re-indexing failed",
                doc_id=doc_id,
                error=result["error"]
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error re-indexing document {doc_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Re-indexing failed: {str(e)}")

@app.post("/sync", response_model=SyncResponse)
async def sync_data_source(
    source: str,
    background_tasks: BackgroundTasks
):
    """
    Full synchronization for a data source.
    
    Performs a complete sync including:
    1. Processing all documents from the source
    2. Identifying and handling deletions
    3. Updating metadata
    """
    try:
        logger.info(f"Starting full sync for source: {source}")
        
        # Perform full synchronization
        result = await pipeline.sync_source(source)
        
        logger.info(f"Sync completed for {source}: {result['documents_processed']} processed, {result['documents_deleted']} deleted")
        return SyncResponse(
            success=True,
            message=f"Sync completed for {source}",
            documents_processed=result["documents_processed"],
            documents_deleted=result["documents_deleted"],
            errors=result.get("errors", [])
        )
        
    except Exception as e:
        logger.error(f"Sync failed for source {source}: {e}")
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")

@app.post("/ingest/user", response_model=IngestionResponse)
async def ingest_user_profile(
    request: UserIngestRequest,
    background_tasks: BackgroundTasks
):
    """
    Ingest a user profile.
    
    Combines provided bio and scraped content (if URL provided and consent given).
    """
    try:
        # Validate consent if URL is provided
        if request.url and not request.scraping_consent:
            raise HTTPException(
                status_code=400, 
                detail="scraping_consent must be True when providing a URL"
            )
            
        content_parts = []
        if request.bio:
            content_parts.append(f"Bio: {request.bio}")
            
        if request.url:
            logger.info(f"Scraping user profile from {request.url}")
            scraped_content = scrape_url(request.url)
            if scraped_content:
                content_parts.append(f"Scraped Content from {request.url}:\n{scraped_content}")
            else:
                logger.warning(f"Failed to scrape content from {request.url}")
                # We continue even if scraping fails, as long as we have valid request
        
        full_text = "\n\n".join(content_parts)
        
        if not full_text:
            raise HTTPException(status_code=400, detail="No content provided (bio or valid URL required)")
            
        doc_id = f"user_{request.name.lower().replace(' ', '_')}"
        now = datetime.utcnow().isoformat()
        
        document = DocumentPayload(
            doc_id=doc_id,
            source="user_profile",
            title=f"User Profile: {request.name}",
            uri=request.url or f"user://{doc_id}",
            text=full_text,
            author="system",
            created_at=now,
            updated_at=now
        )
        
        logger.info(f"Processing user profile: {doc_id}")
        result = await pipeline.process_document(document)
        
        if result["success"]:
            return IngestionResponse(
                success=True,
                message="User profile processed successfully",
                doc_id=doc_id,
                chunks_processed=result["chunks_processed"]
            )
        else:
            return IngestionResponse(
                success=False,
                message="User profile processing failed",
                doc_id=doc_id,
                error=result["error"]
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing user profile {request.name}: {e}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

async def _ingest_qr_value(value: str, content_type: str) -> Dict[str, Any]:
    """Ingest a single decoded QR value through the pipeline."""
    now = datetime.utcnow().isoformat()
    doc_id = f"qr_{uuid.uuid4().hex[:12]}"

    if content_type == "url":
        text = scrape_url(value)
        if not text:
            return {"ingested": False, "doc_id": doc_id, "chunks_processed": 0,
                    "error": f"Failed to scrape URL: {value}"}
        title = f"QR Code URL: {value}"
        uri = value
        source = "qr_url"
    else:
        text = value
        title = f"QR Code Text: {value[:80]}"
        uri = f"qr://text/{doc_id}"
        source = "qr_text"

    document = DocumentPayload(
        doc_id=doc_id,
        source=source,
        title=title,
        uri=uri,
        text=text,
        author="qr_analyzer",
        created_at=now,
        updated_at=now,
    )

    result = await pipeline.process_document(document)
    return {
        "ingested": result["success"],
        "doc_id": doc_id,
        "chunks_processed": result.get("chunks_processed", 0),
        "error": result.get("error"),
    }


@app.post("/analyze/qr", response_model=QRAnalyzeResponse)
async def analyze_qr_code(
    file: Optional[UploadFile] = File(default=None),
    image_base64: Optional[str] = Form(default=None),
    ingest: bool = Form(default=True),
):
    """
    Analyze a QR code image and optionally ingest its content.

    Accepts either:
    - A binary image upload via `file` (multipart/form-data)
    - A base64-encoded image string via `image_base64`

    For each QR code found:
    - If the decoded value is a URL, it is scraped and embedded.
    - If it is plain text, it is embedded directly.

    Set `ingest=false` to decode without ingesting.
    """
    if file is None and not image_base64:
        raise HTTPException(status_code=400, detail="Provide either 'file' or 'image_base64'")

    # Decode QR codes
    try:
        if file is not None:
            image_bytes = await file.read()
            decoded_values = decode_qr_from_bytes(image_bytes)
        else:
            decoded_values = decode_qr_from_base64(image_base64)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.error(f"QR decode error: {exc}")
        raise HTTPException(status_code=500, detail=f"QR decoding failed: {str(exc)}")

    if not decoded_values:
        return QRAnalyzeResponse(
            success=True,
            qr_codes_found=0,
            results=[],
            message="No QR codes detected in the image",
        )

    results: List[QRCodeResult] = []
    for value in decoded_values:
        content_type = classify_qr_content(value)

        if ingest:
            ingest_result = await _ingest_qr_value(value, content_type)
            results.append(QRCodeResult(
                value=value,
                content_type=content_type,
                ingested=ingest_result["ingested"],
                doc_id=ingest_result["doc_id"],
                chunks_processed=ingest_result["chunks_processed"],
                error=ingest_result.get("error"),
            ))
        else:
            results.append(QRCodeResult(
                value=value,
                content_type=content_type,
                ingested=False,
            ))

    success = all(r.ingested for r in results) if ingest else True
    logger.info(f"QR analysis: {len(results)} code(s) found, ingest={ingest}")
    return QRAnalyzeResponse(
        success=success,
        qr_codes_found=len(results),
        results=results,
        message=f"Processed {len(results)} QR code(s)",
    )


@app.post("/chat/log", response_model=IngestionResponse)
async def log_chat(
    payload: ChatLogPayload,
    background_tasks: BackgroundTasks
):
    """
    Log a chat message to the database for future memory consolidation.
    """
    try:
        success = await pipeline.log_chat_message(
            session_id=payload.session_id,
            role=payload.role,
            message=payload.message,
            meta=payload.metadata
        )
        
        if success:
            return IngestionResponse(
                success=True,
                message="Chat message logged successfully",
                doc_id=payload.session_id # Using session_id as doc_id reference
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to log chat message")
            
    except Exception as e:
        logger.error(f"Error logging chat message: {e}")
        raise HTTPException(status_code=500, detail=f"Logging failed: {str(e)}")

@app.post("/chat/memory/consolidate", response_model=ChatConsolidationResponse)
async def consolidate_memory(
    background_tasks: BackgroundTasks
):
    """
    Trigger consolidation of chat logs into long-term vector memory.
    """
    try:
        result = await pipeline.consolidate_chat_memory()
        
        if "error" in result:
            return ChatConsolidationResponse(
                success=False,
                processed_count=0,
                sessions_processed=0,
                message="Consolidation failed",
                error=result["error"]
            )
            
        return ChatConsolidationResponse(
            success=True,
            processed_count=result["processed_count"],
            sessions_processed=result["sessions_processed"],
            message=result["message"]
        )
            
    except Exception as e:
        logger.error(f"Error eliminating chat memory: {e}")
        raise HTTPException(status_code=500, detail=f"Consolidation failed: {str(e)}")
class ArmActionRequest(BaseModel):
    """Request schema for LLM-driven arm control."""
    action: str = Field(
        ...,
        description="Action name: extend | retract | wave | point | rest | release",
    )
    amount: Optional[int] = Field(
        None,
        ge=0,
        le=100,
        description="For extend/retract: percentage 0–100. Omit for named gesture actions.",
    )


class ArmActionResponse(BaseModel):
    """Response schema for arm action execution."""
    success: bool
    action: str
    result: str


@app.post("/arm/action", response_model=ArmActionResponse)
async def arm_action(request: ArmActionRequest):
    """
    Execute a pre-configured arm action.

    Called by the LLM via n8n toolHttpRequest. The LLM selects from named
    actions (extend, retract, wave, point, rest, release) and optionally
    provides an amount (0–100%) for parameterized actions.
    """
    if _arm_orch is None:
        raise HTTPException(status_code=503, detail="Servo hardware unavailable.")
    try:
        loop = asyncio.get_event_loop()
        description = await loop.run_in_executor(
            None, _execute_arm_action, _arm_orch, request.action, request.amount
        )
        return ArmActionResponse(success=True, action=request.action, result=description)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Arm action '%s' failed: %s", request.action, e)
        raise HTTPException(status_code=500, detail=f"Arm action failed: {e}")


class AcknowledgeRequest(BaseModel):
    """Request schema for intermediate voice acknowledgment."""
    message: str = Field(..., description="The user's message to acknowledge")


@app.post("/acknowledge")
async def acknowledge(request: AcknowledgeRequest, background_tasks: BackgroundTasks):
    """
    Generate and speak a brief contextual acknowledgment while the main agent runs.

    Flow:
      1. Ask Ollama to produce a single natural sentence acknowledging the user's message
      2. Interrupt the speaker (get current epoch)
      3. Synthesize audio via Kokoro TTS
      4. Play it non-blocking via the speaker API
      5. Return immediately so n8n can proceed to the main agent

    The audio plays in the background while the main agent uses its tools.
    """
    _SYSTEM_PROMPT = (
        "You are generating a brief spoken acknowledgment for Lynx, an AI robot assistant. "
        "Given the user's message, output ONE short conversational sentence (under 15 words) "
        "that naturally acknowledges what you are about to help with. "
        "Reference the subject of their request. "
        "Be warm and natural. Output only the sentence — no quotes, no explanation."
    )

    async def _speak_acknowledgment(text: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Get current epoch by interrupting (stops any ongoing speech)
                interrupt_resp = await client.post(f"{SPEAKER_URL}/interrupt")
                epoch = interrupt_resp.json().get("epoch", 0)

                # Synthesize audio
                tts_resp = await client.post(
                    f"{KOKORO_URL}/v1/audio/speech",
                    json={"model": "kokoro", "input": text, "voice": KOKORO_VOICE,
                          "response_format": "wav", "speed": 1.2, "stream": False},
                )
                tts_resp.raise_for_status()
                audio_bytes = tts_resp.content

                # Play (non-blocking on the speaker side)
                await client.post(
                    f"{SPEAKER_URL}/play",
                    params={"epoch": epoch},
                    content=audio_bytes,
                    headers={"Content-Type": "audio/wav"},
                )
        except Exception as e:
            logger.warning("Acknowledge speak failed: %s", e)

    # Generate acknowledgment text from Ollama
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": request.message},
                    ],
                    "stream": False,
                    "options": {"num_predict": 40, "temperature": 0.7},
                },
            )
            resp.raise_for_status()
            acknowledgment = resp.json()["message"]["content"].strip().strip('"')
    except Exception as e:
        logger.warning("Ollama acknowledgment generation failed: %s", e)
        acknowledgment = "Got it, let me look into that for you."

    # Speak in background — returns immediately so n8n proceeds to the main agent
    background_tasks.add_task(_speak_acknowledgment, acknowledgment)

    return {"acknowledgment": acknowledgment, "status": "speaking"}


@app.post("/ingest/pdf", response_model=PDFIngestResponse)
async def ingest_pdf(
    file: UploadFile = File(...),
    title: str = Form(default=None),
    source: str = Form(default="pdf_upload"),
    author: str = Form(default="unknown"),
    use_openai_vision: bool = Form(default=False),
    background_tasks: BackgroundTasks = None,
):
    """
    Ingest a PDF file, extracting both text and image content.

    For each page the pipeline will:
      1. Extract raw text via PyMuPDF
      2. Extract embedded images and describe them using a vision model
         - Default: moondream via Ollama (local, free)
         - Optional: GPT-4o Vision (set use_openai_vision=true in the form)
      3. Combine text + image descriptions into a single document
      4. Run through the normal chunk → embed → Qdrant pipeline

    Form fields:
      file              (required) The PDF file
      title             Document title (defaults to filename)
      source            Source label (default: 'pdf_upload')
      author            Author name  (default: 'unknown')
      use_openai_vision If true, use GPT-4o Vision instead of moondream
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    doc_title = title or file.filename
    doc_id = f"pdf_{uuid.uuid4().hex[:16]}"
    now = datetime.utcnow().isoformat()

    logger.info("Processing PDF upload: %s (%d bytes)", file.filename, len(pdf_bytes))

    try:
        openai_client = pipeline.openai_client if use_openai_vision else None
        combined_text = await extract_pdf_content(
            pdf_bytes,
            openai_client=openai_client,
            use_openai_vision=use_openai_vision,
        )
    except RuntimeError as exc:
        # PyMuPDF not installed
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.error("PDF extraction failed for %s: %s", file.filename, exc)
        raise HTTPException(status_code=500, detail=f"PDF extraction failed: {exc}")

    if not combined_text.strip():
        raise HTTPException(
            status_code=422,
            detail="No extractable content found in the PDF (it may be a scanned image without OCR)",
        )

    # Count how many image description blocks were produced
    images_described = combined_text.count("[Page ") - combined_text.count("[Page ") + \
                       combined_text.count("Image") if "Image" in combined_text else 0
    images_described = combined_text.count("Image Description]")

    document = DocumentPayload(
        doc_id=doc_id,
        source=source,
        title=doc_title,
        uri=f"pdf://{file.filename}",
        text=combined_text,
        author=author,
        created_at=now,
        updated_at=now,
    )

    result = await pipeline.process_document(document)

    if result["success"]:
        logger.info(
            "PDF ingested successfully: %s → %d chunks",
            file.filename, result["chunks_processed"],
        )
        return PDFIngestResponse(
            success=True,
            message="PDF ingested successfully",
            doc_id=doc_id,
            images_described=images_described,
            chunks_processed=result["chunks_processed"],
        )
    else:
        return PDFIngestResponse(
            success=False,
            message="PDF ingestion failed",
            doc_id=doc_id,
            error=result.get("error"),
        )


if __name__ == "__main__":
    import uvicorn
    
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", 8000))
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=True,
        log_level=os.getenv("LOG_LEVEL", "info").lower()
    )
