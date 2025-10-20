"""
Unit tests for utility functions.
"""

import pytest
import tiktoken
from unittest.mock import Mock, patch
from datetime import datetime

from app.utils import (
    chunk_text,
    get_embedding,
    validate_document_payload,
    format_api_response,
    calculate_text_similarity,
    sanitize_text,
    estimate_tokens,
    create_metadata_summary
)

class TestChunkText:
    """Test text chunking functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.tokenizer = tiktoken.get_encoding("cl100k_base")
    
    def test_chunk_short_text(self):
        """Test chunking of text shorter than max tokens."""
        text = "This is a short text."
        chunks = chunk_text(text, self.tokenizer, max_tokens=100)
        
        assert len(chunks) == 1
        assert chunks[0]["text"] == text
        assert chunks[0]["token_count"] > 0
    
    def test_chunk_long_text(self):
        """Test chunking of text longer than max tokens."""
        text = "This is a test sentence. " * 100  # Create long text
        chunks = chunk_text(text, self.tokenizer, max_tokens=50)
        
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk["token_count"] <= 50
            assert chunk["text"].strip()
    
    def test_chunk_empty_text(self):
        """Test chunking of empty text."""
        chunks = chunk_text("", self.tokenizer)
        assert chunks == []
    
    def test_chunk_whitespace_text(self):
        """Test chunking of whitespace-only text."""
        chunks = chunk_text("   \n\t   ", self.tokenizer)
        assert chunks == []

class TestGetEmbedding:
    """Test embedding generation functionality."""
    
    @pytest.mark.asyncio
    async def test_get_embedding_single_text(self):
        """Test embedding generation for single text."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.data = [Mock(embedding=[0.1, 0.2, 0.3])]
        mock_client.embeddings.create.return_value = mock_response
        
        with patch('app.utils.asyncio.to_thread') as mock_to_thread:
            mock_to_thread.return_value = mock_response
            embeddings = await get_embedding(["test text"], mock_client)
            
            assert len(embeddings) == 1
            assert len(embeddings[0]) == 3
            assert embeddings[0] == [0.1, 0.2, 0.3]
    
    @pytest.mark.asyncio
    async def test_get_embedding_multiple_texts(self):
        """Test embedding generation for multiple texts."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.data = [
            Mock(embedding=[0.1, 0.2, 0.3]),
            Mock(embedding=[0.4, 0.5, 0.6])
        ]
        
        with patch('app.utils.asyncio.to_thread') as mock_to_thread:
            mock_to_thread.return_value = mock_response
            embeddings = await get_embedding(["text1", "text2"], mock_client)
            
            assert len(embeddings) == 2
            assert embeddings[0] == [0.1, 0.2, 0.3]
            assert embeddings[1] == [0.4, 0.5, 0.6]
    
    @pytest.mark.asyncio
    async def test_get_embedding_empty_list(self):
        """Test embedding generation for empty text list."""
        mock_client = Mock()
        embeddings = await get_embedding([], mock_client)
        assert embeddings == []

class TestValidateDocumentPayload:
    """Test document payload validation."""
    
    def test_valid_payload(self):
        """Test validation of valid payload."""
        payload = {
            "doc_id": "test_123",
            "source": "notion",
            "title": "Test Document",
            "uri": "https://example.com/doc",
            "text": "This is test content",
            "author": "Test Author",
            "created_at": "2023-01-01T00:00:00Z",
            "updated_at": "2023-01-01T00:00:00Z"
        }
        
        errors = validate_document_payload(payload)
        assert errors == []
    
    def test_missing_required_fields(self):
        """Test validation with missing required fields."""
        payload = {
            "doc_id": "test_123",
            "source": "notion"
            # Missing other required fields
        }
        
        errors = validate_document_payload(payload)
        assert len(errors) > 0
        assert any("Missing required field" in error for error in errors)
    
    def test_empty_required_fields(self):
        """Test validation with empty required fields."""
        payload = {
            "doc_id": "",
            "source": "notion",
            "title": "Test Document",
            "uri": "https://example.com/doc",
            "text": "This is test content",
            "author": "Test Author",
            "created_at": "2023-01-01T00:00:00Z",
            "updated_at": "2023-01-01T00:00:00Z"
        }
        
        errors = validate_document_payload(payload)
        assert any("Empty value for required field: doc_id" in error for error in errors)
    
    def test_invalid_timestamp_format(self):
        """Test validation with invalid timestamp format."""
        payload = {
            "doc_id": "test_123",
            "source": "notion",
            "title": "Test Document",
            "uri": "https://example.com/doc",
            "text": "This is test content",
            "author": "Test Author",
            "created_at": "invalid-timestamp",
            "updated_at": "2023-01-01T00:00:00Z"
        }
        
        errors = validate_document_payload(payload)
        assert any("Invalid timestamp format for created_at" in error for error in errors)

class TestFormatApiResponse:
    """Test API response formatting."""
    
    def test_success_response(self):
        """Test formatting of success response."""
        response = format_api_response(
            success=True,
            message="Operation completed",
            data={"key": "value"}
        )
        
        assert response["success"] is True
        assert response["message"] == "Operation completed"
        assert response["data"] == {"key": "value"}
        assert "timestamp" in response
    
    def test_error_response(self):
        """Test formatting of error response."""
        response = format_api_response(
            success=False,
            message="Operation failed",
            error="Test error"
        )
        
        assert response["success"] is False
        assert response["message"] == "Operation failed"
        assert response["error"] == "Test error"

class TestCalculateTextSimilarity:
    """Test text similarity calculation."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.tokenizer = tiktoken.get_encoding("cl100k_base")
    
    def test_identical_texts(self):
        """Test similarity of identical texts."""
        text = "This is a test sentence."
        similarity = calculate_text_similarity(text, text, self.tokenizer)
        assert similarity == 1.0
    
    def test_different_texts(self):
        """Test similarity of completely different texts."""
        text1 = "This is a test sentence."
        text2 = "Completely different content here."
        similarity = calculate_text_similarity(text1, text2, self.tokenizer)
        assert 0.0 <= similarity < 1.0
    
    def test_empty_texts(self):
        """Test similarity with empty texts."""
        similarity = calculate_text_similarity("", "", self.tokenizer)
        assert similarity == 0.0

class TestSanitizeText:
    """Test text sanitization."""
    
    def test_sanitize_normal_text(self):
        """Test sanitization of normal text."""
        text = "This is normal text."
        sanitized = sanitize_text(text)
        assert sanitized == text
    
    def test_sanitize_text_with_control_chars(self):
        """Test sanitization of text with control characters."""
        text = "Text with\x00null\x01bytes"
        sanitized = sanitize_text(text)
        assert "\x00" not in sanitized
        assert "\x01" not in sanitized
    
    def test_sanitize_whitespace(self):
        """Test sanitization of excessive whitespace."""
        text = "Text   with    multiple    spaces"
        sanitized = sanitize_text(text)
        assert "   " not in sanitized
        assert "    " not in sanitized

class TestEstimateTokens:
    """Test token estimation."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.tokenizer = tiktoken.get_encoding("cl100k_base")
    
    def test_estimate_tokens_normal_text(self):
        """Test token estimation for normal text."""
        text = "This is a test sentence."
        estimated = estimate_tokens(text, self.tokenizer)
        assert estimated > 0
    
    def test_estimate_tokens_empty_text(self):
        """Test token estimation for empty text."""
        estimated = estimate_tokens("", self.tokenizer)
        assert estimated == 0

class TestCreateMetadataSummary:
    """Test metadata summary creation."""
    
    def test_create_metadata_summary(self):
        """Test creation of metadata summary."""
        document = {
            "doc_id": "test_123",
            "source": "notion",
            "title": "Very Long Title That Should Be Truncated Because It Exceeds The Maximum Length Allowed For Display Purposes",
            "text": "This is test content",
            "author": "Test Author",
            "created_at": "2023-01-01T00:00:00Z",
            "updated_at": "2023-01-01T00:00:00Z"
        }
        
        summary = create_metadata_summary(document)
        
        assert summary["doc_id"] == "test_123"
        assert summary["source"] == "notion"
        assert len(summary["title"]) <= 100  # Should be truncated
        assert summary["text_length"] == len(document["text"])
        assert summary["author"] == "Test Author"
    
    def test_create_metadata_summary_missing_fields(self):
        """Test creation of metadata summary with missing fields."""
        document = {"doc_id": "test_123"}
        
        summary = create_metadata_summary(document)
        
        assert summary["doc_id"] == "test_123"
        assert summary["source"] == "unknown"
        assert summary["title"] == "untitled"
        assert summary["text_length"] == 0
