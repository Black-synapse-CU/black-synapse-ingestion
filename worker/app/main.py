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
from pydantic import BaseModel, Field, field_validator, model_validator
from dotenv import load_dotenv
import httpx

from .pipeline import IngestionPipeline
from .utils import setup_logging
from .scraper import scrape_url
from .qr_analyzer import decode_qr_from_bytes, decode_qr_from_base64, classify_qr_content
from .pdf_processor import extract_pdf_content
import asyncio
import threading
import time
import uuid
from datetime import datetime

from action_servos import ServoOrchestrator as _ServoOrchestrator
from action_servos.config import DEFAULT_I2C_BUS, DEFAULT_PCA9685_ADDRESS, DEFAULT_PWM_FREQUENCY_HZ
from .arm_actions import execute_action as _execute_arm_action
from robotic_arm.tools import turn_head as _turn_head

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


@app.on_event("startup")
def _start_vision_loop() -> None:
    if ARM_VISION_LOOP_ENABLED:
        t = threading.Thread(target=_vision_loop, daemon=True, name="arm-vision-loop")
        t.start()
        logger.info("Arm vision loop enabled (interval=%.1fs)", ARM_VISION_INTERVAL)
    else:
        logger.info("Arm vision loop disabled (set ARM_VISION_LOOP_ENABLED=true to enable)")

# Arm servo orchestrator — None if hardware unavailable (worker still starts cleanly)
_servo_bus = int(os.getenv("SERVO_I2C_BUS", str(DEFAULT_I2C_BUS)))
try:
    _arm_orch = _ServoOrchestrator()
    _arm_orch.open(_servo_bus, DEFAULT_PCA9685_ADDRESS, DEFAULT_PWM_FREQUENCY_HZ)
    logger.info("Servo orchestrator opened on I2C bus %d", _servo_bus)
except Exception as _e:
    _arm_orch = None
    logger.warning("Servo hardware unavailable — /arm/action will return 503: %s", _e)

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
    """Health check."""
    return {
        "status": "healthy",
        "servos": "connected" if _arm_orch is not None else "unavailable",
    }

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
        description="For extend/retract: percentage 0–100. Omit to use the default (30% — a small, safe movement). Only go higher if the user explicitly asks for a large or full movement.",
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


# ── Vision-react: camera → GPT-4o mini → arm action ──────────────────

_arm_cam_raw = os.getenv("ARM_CAM_INDEX", "0")
ARM_CAM_INDEX: "int | str" = int(_arm_cam_raw) if _arm_cam_raw.lstrip("-").isdigit() else _arm_cam_raw
# Flip code passed to cv2.flip: -1=both axes (180°), 0=vertical, 1=horizontal, ""=no flip
ARM_CAM_FLIP = os.getenv("ARM_CAM_FLIP", "")

# Tracking / scan state (only mutated inside _arm_lock)
_scan_base: float = 0.0
_scan_base_dir: float = 1.0
_scan_shoulder: float = 0.35   # matches ARM_TRACK_Y_BIAS default — scan starts at standing height
_scan_shoulder_dir: float = 1.0
ARM_TRACK_SCAN_STEP_BASE     = float(os.getenv("ARM_TRACK_SCAN_STEP_BASE",     "0.08"))
ARM_TRACK_SCAN_STEP_SHOULDER = float(os.getenv("ARM_TRACK_SCAN_STEP_SHOULDER", "0.05"))
ARM_TRACK_SCAN_LIMIT_BASE    = float(os.getenv("ARM_TRACK_SCAN_LIMIT_BASE",    "0.85"))
ARM_TRACK_SCAN_LIMIT_SHOULDER= float(os.getenv("ARM_TRACK_SCAN_LIMIT_SHOULDER","0.75"))
# Set ARM_SHOULDER_INVERT=true if shoulder moves down when it should move up
_SHOULDER_SIGN = -1.0 if os.getenv("ARM_SHOULDER_INVERT", "false").lower() == "true" else 1.0

# Smoothing: EMA alpha (lower = smoother but slower to respond)
# Deadband: minimum smoothed change before the arm actually moves
ARM_TRACK_SMOOTH_ALPHA = float(os.getenv("ARM_TRACK_SMOOTH_ALPHA", "0.5"))
ARM_TRACK_DEADBAND     = float(os.getenv("ARM_TRACK_DEADBAND",     "0.08"))
# Bias added to every shoulder target — shifts the default aim upward (positive = up)
ARM_TRACK_Y_BIAS       = float(os.getenv("ARM_TRACK_Y_BIAS",       "0.35"))
_smooth_x:        float = 0.0
_smooth_y:        float = ARM_TRACK_Y_BIAS   # start EMA at the bias so first move is already up
_last_cmd_base:   float = 0.0
_last_cmd_shoulder: float = ARM_TRACK_Y_BIAS * ARM_TRACK_SCAN_LIMIT_SHOULDER

_VISION_ACTIONS = frozenset(("rest", "neutral", "extend", "retract", "point", "wave", "release"))

_VISION_SYSTEM_PROMPT = (
    "You are the decision engine for a robotic arm. "
    "Analyse the image from the arm's camera and choose the single best action to perform. "
    "Valid actions: rest, neutral, extend, retract, point, wave, release. "
    "For extend and retract you may also specify amount (0–100 percent); set null for all other actions. "
    "Reply with JSON only — no markdown, no extra keys:\n"
    '{"action": "<action>", "amount": <integer or null>, "reason": "<one sentence>"}'
)

ARM_VISION_LOOP_ENABLED = os.getenv("ARM_VISION_LOOP_ENABLED", "false").lower() == "true"
ARM_VISION_INTERVAL = float(os.getenv("ARM_VISION_INTERVAL", "1.0"))

AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
# Separate deployment for chat/vision — avoids using the embedding deployment for completions
AZURE_CHAT_DEPLOYMENT = os.getenv("AZURE_CHAT_DEPLOYMENT", AZURE_OPENAI_DEPLOYMENT)
ARM_VISION_PROMPT = os.getenv(
    "ARM_VISION_PROMPT",
    "Wave if you see a person. If nobody is visible, choose rest or neutral.",
)

# Prevents the background loop and manual HTTP calls from moving the arm simultaneously.
_arm_lock = threading.Lock()


_TRACK_SYSTEM_PROMPT = (
    "You are a person-tracking controller for a robotic arm camera. "
    "Detect whether any person is visible, then estimate where to point the camera to best face them. "
    "x = -1.0 (far left) to +1.0 (far right), y = -1.0 (aim down) to +1.0 (aim up). Both 0.0 = center. "
    "Important: infer the full body position, not just what is visible. "
    "If only legs/feet are visible at the bottom, the torso is above frame — return y close to +1.0 to look up. "
    "If only a head/shoulders are visible at the top, the body is centered — return y near 0.0 or slightly positive. "
    "If the full body is visible, return y based on the torso center. "
    "Reply with JSON only — no markdown:\n"
    '{"person": true, "x": <float>, "y": <float>}  or  {"person": false}'
)


def _move_arm_to(base_norm: float, shoulder_norm: float, duration_s: float = 0.6) -> None:
    """Move base and shoulder to normalized positions; hold elbow at its current position."""
    from action_servos.groups import normalized_to_us
    arm = _arm_orch.arm
    L = _arm_orch.layout
    s = arm.last_state
    base_us     = normalized_to_us(L.base,       base_norm)
    shoulder_us = normalized_to_us(L.shoulder_a, _SHOULDER_SIGN * shoulder_norm)
    elbow_us    = s.elbow or L.elbow.center_us
    arm.move_ramp(base_us, shoulder_us, elbow_us, duration_s=duration_s, steps=20)


def _track_blocking(cam_index: "int | str") -> dict:
    """Capture → Azure person detection → track person in 2D, or scan both axes."""
    import base64, json, openai, httpx as _httpx
    global _scan_base, _scan_base_dir, _scan_shoulder, _scan_shoulder_dir

    if not AZURE_OPENAI_API_KEY:
        raise RuntimeError("AZURE_OPENAI_API_KEY not configured")
    if not AZURE_OPENAI_ENDPOINT:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT not configured")

    jpg = _capture_arm_frame(cam_index)
    if jpg is None:
        raise RuntimeError(f"Could not capture frame from camera {cam_index}")

    b64 = base64.b64encode(jpg).decode()
    client = openai.AzureOpenAI(
        api_key=AZURE_OPENAI_API_KEY,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_version=AZURE_OPENAI_API_VERSION,
        http_client=_httpx.Client(),
    )
    resp = client.chat.completions.create(
        model=AZURE_CHAT_DEPLOYMENT,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _TRACK_SYSTEM_PROMPT},
            {"role": "user", "content": [{"type": "image_url", "image_url": {
                "url": f"data:image/jpeg;base64,{b64}", "detail": "low",
            }}]},
        ],
        max_tokens=80,
    )
    raw = resp.choices[0].message.content or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"Model returned non-JSON: {raw!r}")

    if data.get("person"):
        global _smooth_x, _smooth_y, _last_cmd_base, _last_cmd_shoulder
        x = max(-1.0, min(1.0, float(data.get("x", 0.0))))
        y = max(-1.0, min(1.0, float(data.get("y", 0.0))))

        # EMA smoothing to suppress noisy model estimates
        _smooth_x = ARM_TRACK_SMOOTH_ALPHA * x + (1.0 - ARM_TRACK_SMOOTH_ALPHA) * _smooth_x
        _smooth_y = ARM_TRACK_SMOOTH_ALPHA * y + (1.0 - ARM_TRACK_SMOOTH_ALPHA) * _smooth_y

        target_base     = _smooth_x * ARM_TRACK_SCAN_LIMIT_BASE
        target_shoulder = (_smooth_y + ARM_TRACK_Y_BIAS) * ARM_TRACK_SCAN_LIMIT_SHOULDER

        # Only move if the smoothed position has shifted past the deadband
        if (abs(target_base - _last_cmd_base) > ARM_TRACK_DEADBAND or
                abs(target_shoulder - _last_cmd_shoulder) > ARM_TRACK_DEADBAND):
            _move_arm_to(target_base, target_shoulder)
            _last_cmd_base     = target_base
            _last_cmd_shoulder = target_shoulder

        _scan_base     = target_base
        _scan_shoulder = target_shoulder
        return {"tracked": True, "x": round(_smooth_x, 2), "y": round(_smooth_y, 2)}

    # No person — advance both axes independently and bounce at their limits
    def _step(pos: float, direction: float, step: float, limit: float):
        pos += direction * step
        if pos >= limit:
            return limit, -1.0
        if pos <= -limit:
            return -limit, 1.0
        return pos, direction

    _scan_base,     _scan_base_dir     = _step(_scan_base,     _scan_base_dir,     ARM_TRACK_SCAN_STEP_BASE,     ARM_TRACK_SCAN_LIMIT_BASE)
    _scan_shoulder, _scan_shoulder_dir = _step(_scan_shoulder, _scan_shoulder_dir, ARM_TRACK_SCAN_STEP_SHOULDER, ARM_TRACK_SCAN_LIMIT_SHOULDER)
    _move_arm_to(_scan_base, _scan_shoulder)
    return {"tracked": False, "base": _scan_base, "shoulder": _scan_shoulder}


def _vision_loop() -> None:
    """Background daemon: periodically capture → detect person → track or scan."""
    logger.info(
        "Vision loop started (interval=%.1fs, cam=%s)", ARM_VISION_INTERVAL, ARM_CAM_INDEX
    )
    while True:
        time.sleep(ARM_VISION_INTERVAL)
        if _arm_orch is None:
            continue
        if not _arm_lock.acquire(blocking=False):
            logger.debug("Vision loop skipped — arm busy")
            continue
        try:
            out = _track_blocking(ARM_CAM_INDEX)
            if out["tracked"]:
                logger.info("Track: person x=%.2f y=%.2f", out["x"], out["y"])
            else:
                logger.info("Scan: base=%.2f shoulder=%.2f", out["base"], out["shoulder"])
        except Exception as exc:
            logger.warning("Vision loop error: %s", exc)
        finally:
            _arm_lock.release()


class VisionReactRequest(BaseModel):
    """Optional overrides for the vision-react endpoint."""
    prompt: Optional[str] = Field(
        None,
        description="Extra instruction appended to the system prompt, e.g. 'wave if you see a person waving'.",
    )
    cam_index: Optional[int] = Field(
        None,
        description="Camera device index. Defaults to ARM_CAM_INDEX env var (default 0).",
    )


class VisionReactResponse(BaseModel):
    success: bool
    action: str
    amount: Optional[int]
    reason: str
    result: str


def _capture_arm_frame(cam_index: int) -> Optional[bytes]:
    """Open the arm camera, grab one JPEG frame, release immediately."""
    import cv2, sys
    backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
    cap = cv2.VideoCapture(cam_index, backend)
    if not cap.isOpened():
        return None
    try:
        for _ in range(5):  # flush stale buffered frames
            cap.read()
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        if ARM_CAM_FLIP != "":
            frame = cv2.flip(frame, int(ARM_CAM_FLIP))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes() if ok else None
    finally:
        cap.release()


def _vision_react_blocking(cam_index: int, extra_prompt: Optional[str]) -> dict:
    """Blocking: capture → Azure GPT-4o mini → execute arm action. Run in executor."""
    import base64, json, openai, httpx as _httpx

    if not AZURE_OPENAI_API_KEY:
        raise RuntimeError("AZURE_OPENAI_API_KEY not configured")
    if not AZURE_OPENAI_ENDPOINT:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT not configured")

    jpg = _capture_arm_frame(cam_index)
    if jpg is None:
        raise RuntimeError(f"Could not capture frame from camera index {cam_index}")

    b64 = base64.b64encode(jpg).decode()
    system = _VISION_SYSTEM_PROMPT
    if extra_prompt:
        system += f"\n\nAdditional instruction: {extra_prompt}"

    client = openai.AzureOpenAI(
        api_key=AZURE_OPENAI_API_KEY,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_version=AZURE_OPENAI_API_VERSION,
        http_client=_httpx.Client(),
    )
    resp = client.chat.completions.create(
        model=AZURE_CHAT_DEPLOYMENT,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}",
                            "detail": "low",
                        },
                    }
                ],
            },
        ],
        max_tokens=150,
    )
    raw = resp.choices[0].message.content or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"GPT-4o mini returned non-JSON: {raw!r}")

    action = str(data.get("action", "neutral")).strip().lower()
    if action not in _VISION_ACTIONS:
        raise RuntimeError(f"GPT-4o mini chose unknown action '{action}'")

    amount = data.get("amount")
    if isinstance(amount, float):
        amount = int(amount)
    reason = str(data.get("reason", ""))

    arm_result = _execute_arm_action(_arm_orch, action, amount)
    return {"action": action, "amount": amount, "reason": reason, "result": arm_result}


@app.post("/arm/vision-react", response_model=VisionReactResponse)
async def arm_vision_react(request: VisionReactRequest):
    """
    Capture a frame from the arm camera, ask GPT-4o mini which action to
    perform, then execute that action on the physical arm.

    Set ARM_CAM_INDEX env var (default 0) to select the arm camera device.
    Optionally pass a custom prompt to give GPT-4o mini extra context.
    """
    if _arm_orch is None:
        raise HTTPException(status_code=503, detail="Servo hardware unavailable.")
    if not _arm_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Arm is busy — try again shortly.")
    cam = request.cam_index if request.cam_index is not None else ARM_CAM_INDEX
    try:
        loop = asyncio.get_event_loop()
        out = await loop.run_in_executor(
            None, _vision_react_blocking, cam, request.prompt
        )
        return VisionReactResponse(success=True, **out)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("arm/vision-react failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Vision-react failed: {e}")
    finally:
        _arm_lock.release()


# ── Object identification: capture arm camera → describe what is shown ─

class IdentifyRequest(BaseModel):
    question: Optional[str] = Field(
        None,
        description="What to ask about the object, e.g. 'What is this?' or 'Can I eat this?'. Defaults to a general description.",
    )


class IdentifyResponse(BaseModel):
    success: bool
    description: str


@app.post("/arm/identify", response_model=IdentifyResponse)
async def arm_identify(request: IdentifyRequest):
    """
    Capture a frame from the arm camera and ask the vision model to identify
    or describe what is being shown to it.

    Useful for holding up an object in front of the arm and asking what it is.
    The response can be passed to TTS to speak the answer aloud.
    """
    import base64, openai, httpx as _httpx

    if not AZURE_OPENAI_API_KEY or not AZURE_OPENAI_ENDPOINT:
        raise HTTPException(status_code=503, detail="Azure OpenAI not configured.")

    jpg = _capture_arm_frame(ARM_CAM_INDEX)
    if jpg is None:
        raise HTTPException(status_code=503, detail=f"Could not capture frame from camera {ARM_CAM_INDEX}.")

    question = request.question or "What object is being shown to you? Describe it clearly and concisely in one or two sentences."

    b64 = base64.b64encode(jpg).decode()
    try:
        client = openai.AzureOpenAI(
            api_key=AZURE_OPENAI_API_KEY,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_version=AZURE_OPENAI_API_VERSION,
            http_client=_httpx.Client(),
        )
        resp = client.chat.completions.create(
            model=AZURE_CHAT_DEPLOYMENT,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the vision system of a robotic arm. "
                        "Answer questions about what the arm camera sees. "
                        "Be concise and direct — your answer will be spoken aloud."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
                        {"type": "text", "text": question},
                    ],
                },
            ],
            max_tokens=200,
        )
        description = (resp.choices[0].message.content or "").strip()
        logger.info("arm/identify: %s", description[:120])
        return IdentifyResponse(success=True, description=description)
    except Exception as e:
        logger.error("arm/identify failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Identify failed: {e}")


class HeadTurnRequest(BaseModel):
    """Request schema for LLM-driven head movement."""
    pan: Optional[float] = Field(None, ge=-1.0, le=1.0, description="Left/right turn: -1=full right, 0=center, 1=full left. Omit to keep current position.")
    tilt: Optional[float] = Field(None, ge=-1.0, le=1.0, description="Up/down tilt: -1=full up, 0=center, 1=full down. Omit to keep current position.")
    duration_s: float = Field(0.6, gt=0.0, description="Seconds to reach the target pose")

    @model_validator(mode="before")
    @classmethod
    def _coerce_empty_strings(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for k in ("pan", "tilt"):
                if data.get(k) == "":
                    data[k] = None
            if data.get("duration_s") == "":
                data["duration_s"] = 0.6
        return data


class HeadTurnResponse(BaseModel):
    success: bool
    result: str


@app.post("/head/turn", response_model=HeadTurnResponse)
async def head_turn(request: HeadTurnRequest):
    """
    Smoothly move the head to a pan/tilt position.

    Called by the LLM via n8n toolHttpRequest. pan and tilt are normalised
    -1..1 values. Hardware limits: pan 1000–2500 µs, tilt 1200–2500 µs,
    center 1700 µs for both.
    """
    if _arm_orch is None:
        raise HTTPException(status_code=503, detail="Servo hardware unavailable.")
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, _turn_head, _arm_orch, request.pan, request.tilt, request.duration_s
        )
        return HeadTurnResponse(success=True, result=result)
    except Exception as e:
        logger.error("Head turn failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Head turn failed: {e}")


class ArmMoveRequest(BaseModel):
    """Request schema for direct LLM-driven arm joint control."""
    base: Optional[float] = Field(None, ge=-1.0, le=1.0, description="Arm base rotation (NOT head): -1=full left, 0=center, 1=full right. Use small increments (0.1–0.2) unless explicitly asked for a large movement.")
    shoulder: Optional[float] = Field(None, ge=-1.0, le=1.0, description="Arm shoulder lift: -1=up, 0=center, 1=down. Use small increments (0.1–0.2) unless explicitly asked for a large movement.")
    elbow: Optional[float] = Field(None, ge=-1.0, le=1.0, description="Arm elbow flex: -1=up, 0=center, 1=down. Use small increments (0.1–0.2) unless explicitly asked for a large movement.")
    wrist_tilt: Optional[float] = Field(None, ge=-1.0, le=1.0, description="Arm wrist tilt: -1=up, 0=center, 1=down. Use small increments (0.1–0.2) unless explicitly asked for a large movement.")
    duration_s: float = Field(0.8, gt=0.0, description="Seconds to reach the target pose")

    @model_validator(mode="before")
    @classmethod
    def _coerce_empty_strings(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for k in ("base", "shoulder", "elbow", "wrist_tilt"):
                if data.get(k) == "":
                    data[k] = None
            if data.get("duration_s") == "":
                data["duration_s"] = 0.8
        return data


class ArmMoveResponse(BaseModel):
    success: bool
    result: str


@app.post("/arm/move", response_model=ArmMoveResponse)
async def arm_move(request: ArmMoveRequest):
    """
    Move one or more arm joints to normalized positions.

    Called by the LLM via n8n toolHttpRequest. Any joint omitted stays at its
    current position. All values are normalised -1..1.
    """
    if _arm_orch is None:
        raise HTTPException(status_code=503, detail="Servo hardware unavailable.")
    if all(v is None for v in (request.base, request.shoulder, request.elbow, request.wrist_tilt)):
        raise HTTPException(status_code=400, detail="At least one joint must be specified: base, shoulder, elbow, or wrist_tilt.")

    def _move() -> str:
        from action_servos.groups import normalized_to_us
        arm = _arm_orch.arm
        L = _arm_orch.layout
        s = arm.last_state

        base_us     = normalized_to_us(L.base,       request.base)      if request.base     is not None else (s.base     or L.base.center_us)
        shoulder_us = normalized_to_us(L.shoulder_a, -request.shoulder) if request.shoulder is not None else (s.shoulder or L.shoulder_a.center_us)
        elbow_us    = normalized_to_us(L.elbow,      -request.elbow)    if request.elbow    is not None else (s.elbow    or L.elbow.center_us)

        arm.move_ramp(base_us, shoulder_us, elbow_us, duration_s=request.duration_s, steps=30)

        if request.wrist_tilt is not None and L.wrist_tilt is not None:
            wt_us = normalized_to_us(L.wrist_tilt, request.wrist_tilt)
            arm.set_wrist_tilt(wt_us)

        parts = []
        if request.base       is not None: parts.append(f"base={request.base:+.2f}")
        if request.shoulder   is not None: parts.append(f"shoulder={request.shoulder:+.2f}")
        if request.elbow      is not None: parts.append(f"elbow={request.elbow:+.2f}")
        if request.wrist_tilt is not None: parts.append(f"wrist_tilt={request.wrist_tilt:+.2f}")
        return f"Arm moved: {', '.join(parts)}."

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _move)
        return ArmMoveResponse(success=True, result=result)
    except Exception as e:
        logger.error("Arm move failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Arm move failed: {e}")


class ArmSequenceRequest(BaseModel):
    """Ordered list of arm moves to execute in sequence."""
    steps: List[ArmMoveRequest] = Field(..., min_length=1, description="Ordered list of arm moves")


class ArmSequenceResponse(BaseModel):
    success: bool
    steps_executed: int
    result: str


@app.post("/arm/sequence", response_model=ArmSequenceResponse)
async def arm_sequence(request: ArmSequenceRequest):
    """
    Execute multiple arm moves in order, one after the other.

    Called by the LLM via n8n toolHttpRequest when a motion requires several
    steps (e.g. raise arm, extend, lower). Each step runs to completion before
    the next starts.
    """
    if _arm_orch is None:
        raise HTTPException(status_code=503, detail="Servo hardware unavailable.")

    def _run_sequence() -> str:
        from action_servos.groups import normalized_to_us
        arm = _arm_orch.arm
        L = _arm_orch.layout
        summaries = []

        for step in request.steps:
            if all(v is None for v in (step.base, step.shoulder, step.elbow, step.wrist_tilt)):
                continue
            s = arm.last_state
            base_us     = normalized_to_us(L.base,       step.base)      if step.base     is not None else (s.base     or L.base.center_us)
            shoulder_us = normalized_to_us(L.shoulder_a, -step.shoulder) if step.shoulder is not None else (s.shoulder or L.shoulder_a.center_us)
            elbow_us    = normalized_to_us(L.elbow,      -step.elbow)    if step.elbow    is not None else (s.elbow    or L.elbow.center_us)
            arm.move_ramp(base_us, shoulder_us, elbow_us, duration_s=step.duration_s, steps=30)
            if step.wrist_tilt is not None and L.wrist_tilt is not None:
                arm.set_wrist_tilt(normalized_to_us(L.wrist_tilt, step.wrist_tilt))
            parts = []
            if step.base       is not None: parts.append(f"base={step.base:+.2f}")
            if step.shoulder   is not None: parts.append(f"shoulder={step.shoulder:+.2f}")
            if step.elbow      is not None: parts.append(f"elbow={step.elbow:+.2f}")
            if step.wrist_tilt is not None: parts.append(f"wrist_tilt={step.wrist_tilt:+.2f}")
            summaries.append(f"[{', '.join(parts)}]")

        return " → ".join(summaries)

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _run_sequence)
        return ArmSequenceResponse(success=True, steps_executed=len(request.steps), result=result)
    except Exception as e:
        logger.error("Arm sequence failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Arm sequence failed: {e}")


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
