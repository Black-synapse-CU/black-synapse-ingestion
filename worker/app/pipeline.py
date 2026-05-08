"""Ingestion pipeline — qdrant/postgres removed. Stub retained for import compatibility."""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class IngestionPipeline:
    def __init__(self) -> None:
        self.openai_client = None

    async def process_document(self, document: Any, force_reindex: bool = False) -> Dict[str, Any]:
        return {"success": False, "chunks_processed": 0, "error": "Ingestion pipeline disabled."}

    async def get_document_by_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        return None

    async def sync_source(self, source: str) -> Dict[str, Any]:
        return {"documents_processed": 0, "documents_deleted": 0, "errors": ["Pipeline disabled."]}

    async def log_chat_message(self, session_id: str, role: str, message: str, meta: Dict = None) -> bool:
        return False

    async def consolidate_chat_memory(self) -> Dict[str, Any]:
        return {"error": "Pipeline disabled."}
