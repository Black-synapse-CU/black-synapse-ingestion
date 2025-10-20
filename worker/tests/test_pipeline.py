"""
Unit tests for the ingestion pipeline.
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime

from app.pipeline import IngestionPipeline
from app.utils import DocumentPayload

class TestIngestionPipeline:
    """Test the ingestion pipeline functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        with patch.dict('os.environ', {
            'OPENAI_API_KEY': 'test-key',
            'QDRANT_URL': 'http://localhost:6333',
            'POSTGRES_URL': 'postgresql://test:test@localhost/test'
        }):
            self.pipeline = IngestionPipeline()
    
    def test_compute_content_hash(self):
        """Test content hash computation."""
        text = "This is test content"
        hash1 = self.pipeline._compute_content_hash(text)
        hash2 = self.pipeline._compute_content_hash(text)
        
        # Same text should produce same hash
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 produces 64-character hex string
    
    def test_compute_content_hash_different_texts(self):
        """Test that different texts produce different hashes."""
        text1 = "This is test content"
        text2 = "This is different content"
        
        hash1 = self.pipeline._compute_content_hash(text1)
        hash2 = self.pipeline._compute_content_hash(text2)
        
        assert hash1 != hash2
    
    @pytest.mark.asyncio
    async def test_is_document_unchanged_existing_document(self):
        """Test checking unchanged document with existing document."""
        doc_id = "test_123"
        content_hash = "abc123"
        
        with patch('psycopg2.connect') as mock_connect:
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchone.return_value = (content_hash,)
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_connect.return_value.__enter__.return_value = mock_conn
            
            result = await self.pipeline._is_document_unchanged(doc_id, content_hash)
            assert result is True
    
    @pytest.mark.asyncio
    async def test_is_document_unchanged_changed_document(self):
        """Test checking unchanged document with changed content."""
        doc_id = "test_123"
        content_hash = "abc123"
        stored_hash = "def456"
        
        with patch('psycopg2.connect') as mock_connect:
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchone.return_value = (stored_hash,)
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_connect.return_value.__enter__.return_value = mock_conn
            
            result = await self.pipeline._is_document_unchanged(doc_id, content_hash)
            assert result is False
    
    @pytest.mark.asyncio
    async def test_is_document_unchanged_new_document(self):
        """Test checking unchanged document with new document."""
        doc_id = "test_123"
        content_hash = "abc123"
        
        with patch('psycopg2.connect') as mock_connect:
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchone.return_value = None  # Document doesn't exist
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_connect.return_value.__enter__.return_value = mock_conn
            
            result = await self.pipeline._is_document_unchanged(doc_id, content_hash)
            assert result is False
    
    @pytest.mark.asyncio
    async def test_log_ingestion_event(self):
        """Test logging ingestion events."""
        doc_id = "test_123"
        event_type = "processed"
        message = "Test message"
        metadata = {"key": "value"}
        
        with patch('psycopg2.connect') as mock_connect:
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_connect.return_value.__enter__.return_value = mock_conn
            
            await self.pipeline._log_ingestion_event(doc_id, event_type, message, metadata)
            
            # Verify the cursor.execute was called with correct parameters
            mock_cursor.execute.assert_called_once()
            call_args = mock_cursor.execute.call_args[0]
            assert "INSERT INTO ingestion_log" in call_args[0]
            assert call_args[1] == (doc_id, event_type, message, '{"key": "value"}')
    
    @pytest.mark.asyncio
    async def test_process_document_unchanged_content(self):
        """Test processing document with unchanged content."""
        document = DocumentPayload(
            doc_id="test_123",
            source="notion",
            title="Test Document",
            uri="https://example.com",
            text="This is test content",
            author="Test Author",
            created_at="2023-01-01T00:00:00Z",
            updated_at="2023-01-01T00:00:00Z"
        )
        
        with patch.object(self.pipeline, '_is_document_unchanged', return_value=True):
            with patch.object(self.pipeline, '_log_ingestion_event') as mock_log:
                result = await self.pipeline.process_document(document)
                
                assert result["success"] is True
                assert result["chunks_processed"] == 0
                assert "skipped" in result["message"]
                mock_log.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_process_document_new_content(self):
        """Test processing document with new content."""
        document = DocumentPayload(
            doc_id="test_123",
            source="notion",
            title="Test Document",
            uri="https://example.com",
            text="This is test content for chunking. " * 50,  # Long text for chunking
            author="Test Author",
            created_at="2023-01-01T00:00:00Z",
            updated_at="2023-01-01T00:00:00Z"
        )
        
        with patch.object(self.pipeline, '_is_document_unchanged', return_value=False):
            with patch.object(self.pipeline, 'get_embedding', return_value=[[0.1] * 1536] * 3):
                with patch.object(self.pipeline.qdrant_client, 'upsert'):
                    with patch.object(self.pipeline, '_update_document_metadata'):
                        with patch.object(self.pipeline, '_log_ingestion_event'):
                            result = await self.pipeline.process_document(document)
                            
                            assert result["success"] is True
                            assert result["chunks_processed"] > 0
    
    @pytest.mark.asyncio
    async def test_process_document_force_reindex(self):
        """Test processing document with force reindex."""
        document = DocumentPayload(
            doc_id="test_123",
            source="notion",
            title="Test Document",
            uri="https://example.com",
            text="This is test content",
            author="Test Author",
            created_at="2023-01-01T00:00:00Z",
            updated_at="2023-01-01T00:00:00Z"
        )
        
        with patch.object(self.pipeline, '_is_document_unchanged', return_value=True):
            with patch.object(self.pipeline, 'get_embedding', return_value=[[0.1] * 1536]):
                with patch.object(self.pipeline.qdrant_client, 'upsert'):
                    with patch.object(self.pipeline, '_update_document_metadata'):
                        with patch.object(self.pipeline, '_log_ingestion_event'):
                            result = await self.pipeline.process_document(document, force_reindex=True)
                            
                            # Should process even if unchanged when force_reindex=True
                            assert result["success"] is True
                            assert result["chunks_processed"] > 0
    
    @pytest.mark.asyncio
    async def test_get_document_by_id_existing(self):
        """Test retrieving existing document by ID."""
        doc_id = "test_123"
        expected_doc = {
            "doc_id": doc_id,
            "source": "notion",
            "title": "Test Document",
            "uri": "https://example.com",
            "author": "Test Author",
            "created_at": "2023-01-01T00:00:00Z",
            "updated_at": "2023-01-01T00:00:00Z"
        }
        
        with patch('psycopg2.connect') as mock_connect:
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchone.return_value = expected_doc
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_connect.return_value.__enter__.return_value = mock_conn
            
            result = await self.pipeline.get_document_by_id(doc_id)
            assert result == expected_doc
    
    @pytest.mark.asyncio
    async def test_get_document_by_id_not_found(self):
        """Test retrieving non-existent document by ID."""
        doc_id = "nonexistent"
        
        with patch('psycopg2.connect') as mock_connect:
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchone.return_value = None
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_connect.return_value.__enter__.return_value = mock_conn
            
            result = await self.pipeline.get_document_by_id(doc_id)
            assert result is None
    
    @pytest.mark.asyncio
    async def test_sync_source(self):
        """Test source synchronization."""
        source = "notion"
        
        with patch.object(self.pipeline, '_log_ingestion_event') as mock_log:
            result = await self.pipeline.sync_source(source)
            
            assert result["documents_processed"] == 0
            assert result["documents_deleted"] == 0
            assert result["errors"] == []
            assert mock_log.call_count == 2  # sync_started and sync_completed
