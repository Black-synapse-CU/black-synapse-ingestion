"""
Black Synapse Data Ingestion Utilities

Utility functions for text processing, embedding generation, and logging.
"""

import logging
import os
import asyncio
from typing import List, Dict, Any
import tiktoken
import openai
import numpy as np

def setup_logging():
    """Configure logging for the application."""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    
    logging.basicConfig(
        level=getattr(logging, log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('ingestion.log')
        ]
    )

def chunk_text(text: str, tokenizer: tiktoken.Encoding, 
               max_tokens: int = 500, overlap_tokens: int = 50) -> List[Dict[str, Any]]:
    """
    Chunk text into overlapping segments for embedding.
    
    Args:
        text: Input text to chunk
        tokenizer: Tiktoken tokenizer instance
        max_tokens: Maximum tokens per chunk
        overlap_tokens: Number of tokens to overlap between chunks
        
    Returns:
        List of chunk dictionaries with 'text' and 'token_count' keys
    """
    if not text.strip():
        return []
    
    # Tokenize the text
    tokens = tokenizer.encode(text)
    
    if len(tokens) <= max_tokens:
        return [{
            "text": text,
            "token_count": len(tokens)
        }]
    
    chunks = []
    start = 0
    
    while start < len(tokens):
        # Calculate end position
        end = min(start + max_tokens, len(tokens))
        
        # Extract chunk tokens
        chunk_tokens = tokens[start:end]
        
        # Decode back to text
        chunk_text = tokenizer.decode(chunk_tokens)
        
        # Clean up chunk text (remove partial words at boundaries)
        if start > 0:  # Not the first chunk
            # Find the first complete word boundary
            first_space = chunk_text.find(' ')
            if first_space > 0:
                chunk_text = chunk_text[first_space + 1:]
        
        if end < len(tokens):  # Not the last chunk
            # Find the last complete word boundary
            last_space = chunk_text.rfind(' ')
            if last_space > 0:
                chunk_text = chunk_text[:last_space]
        
        if chunk_text.strip():
            chunks.append({
                "text": chunk_text.strip(),
                "token_count": len(tokenizer.encode(chunk_text))
            })
        
        # Move start position with overlap
        start = end - overlap_tokens
        
        # Prevent infinite loop
        if start >= len(tokens) - overlap_tokens:
            break
    
    return chunks

async def get_embedding(texts: List[str], openai_client: openai.OpenAI, 
                       model: str = "text-embedding-3-small") -> List[List[float]]:
    """
    Generate embeddings for a list of texts using OpenAI's embedding API.
    
    Args:
        texts: List of texts to embed
        openai_client: OpenAI client instance
        model: Embedding model to use
        
    Returns:
        List of embedding vectors
    """
    if not texts:
        return []
    
    try:
        # OpenAI API supports batching, so we can process all texts at once
        response = await asyncio.to_thread(
            openai_client.embeddings.create,
            model=model,
            input=texts
        )
        
        # Extract embeddings from response
        embeddings = [data.embedding for data in response.data]
        
        return embeddings
        
    except Exception as e:
        logging.error(f"Failed to generate embeddings: {e}")
        raise

def validate_document_payload(payload: Dict[str, Any]) -> List[str]:
    """
    Validate a document payload against the unified schema.
    
    Args:
        payload: Document payload to validate
        
    Returns:
        List of validation errors (empty if valid)
    """
    errors = []
    
    required_fields = ["doc_id", "source", "title", "uri", "text", "author", "created_at", "updated_at"]
    
    for field in required_fields:
        if field not in payload:
            errors.append(f"Missing required field: {field}")
        elif not payload[field] or (isinstance(payload[field], str) and not payload[field].strip()):
            errors.append(f"Empty value for required field: {field}")
    
    # Validate doc_id format (should be non-empty string)
    if "doc_id" in payload and not isinstance(payload["doc_id"], str):
        errors.append("doc_id must be a string")
    
    # Validate source format
    if "source" in payload and not isinstance(payload["source"], str):
        errors.append("source must be a string")
    
    # Validate timestamps (basic ISO format check)
    for timestamp_field in ["created_at", "updated_at"]:
        if timestamp_field in payload:
            try:
                from datetime import datetime
                datetime.fromisoformat(payload[timestamp_field].replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                errors.append(f"Invalid timestamp format for {timestamp_field}")
    
    return errors

def format_api_response(success: bool, message: str, **kwargs) -> Dict[str, Any]:
    """
    Format a standardized API response.
    
    Args:
        success: Whether the operation was successful
        message: Response message
        **kwargs: Additional fields to include in response
        
    Returns:
        Formatted response dictionary
    """
    from datetime import datetime
    
    response = {
        "success": success,
        "message": message,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    response.update(kwargs)
    return response

def calculate_text_similarity(text1: str, text2: str, tokenizer: tiktoken.Encoding) -> float:
    """
    Calculate similarity between two texts using token overlap.
    
    Args:
        text1: First text
        text2: Second text
        tokenizer: Tiktoken tokenizer instance
        
    Returns:
        Similarity score between 0 and 1
    """
    tokens1 = set(tokenizer.encode(text1))
    tokens2 = set(tokenizer.encode(text2))
    
    if not tokens1 or not tokens2:
        return 0.0
    
    intersection = len(tokens1.intersection(tokens2))
    union = len(tokens1.union(tokens2))
    
    return intersection / union if union > 0 else 0.0

def sanitize_text(text: str) -> str:
    """
    Sanitize text for processing by removing or normalizing problematic characters.
    
    Args:
        text: Input text to sanitize
        
    Returns:
        Sanitized text
    """
    if not text:
        return ""
    
    # Remove null bytes and other control characters except newlines and tabs
    sanitized = "".join(char for char in text if ord(char) >= 32 or char in '\n\t')
    
    # Normalize whitespace
    sanitized = ' '.join(sanitized.split())
    
    return sanitized

def estimate_tokens(text: str, tokenizer: tiktoken.Encoding) -> int:
    """
    Estimate the number of tokens in text without full tokenization.
    
    Args:
        text: Input text
        tokenizer: Tiktoken tokenizer instance
        
    Returns:
        Estimated token count
    """
    if not text:
        return 0
    
    # Simple estimation: average 4 characters per token
    return len(text) // 4

def create_metadata_summary(document: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a summary of document metadata for logging and debugging.
    
    Args:
        document: Document dictionary
        
    Returns:
        Summary dictionary with key metadata
    """
    return {
        "doc_id": document.get("doc_id", "unknown"),
        "source": document.get("source", "unknown"),
        "title": document.get("title", "untitled")[:100],  # Truncate long titles
        "text_length": len(document.get("text", "")),
        "author": document.get("author", "unknown"),
        "created_at": document.get("created_at", "unknown"),
        "updated_at": document.get("updated_at", "unknown")
    }
