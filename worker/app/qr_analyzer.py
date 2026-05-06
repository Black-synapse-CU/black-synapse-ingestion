"""
QR Code Analyzer

Decodes QR codes from uploaded images and routes their content through the
ingestion pipeline: URLs are scraped and embedded; plain text is embedded directly.
"""

import base64
import io
import logging
import re
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

try:
    from pyzbar.pyzbar import decode as pyzbar_decode
    _PYZBAR_AVAILABLE = True
except Exception:
    pyzbar_decode = None  # type: ignore[assignment]
    _PYZBAR_AVAILABLE = False
    logger.warning("pyzbar native libraries not found — QR decoding disabled")

# Simple URL pattern — used to decide whether decoded data should be scraped
_URL_RE = re.compile(r'^https?://', re.IGNORECASE)


def decode_qr_from_bytes(image_bytes: bytes) -> list[str]:
    """
    Decode all QR codes found in raw image bytes.

    Args:
        image_bytes: Raw binary image data (JPEG, PNG, BMP, etc.).

    Returns:
        List of decoded string values (one per QR code found in the image).

    Raises:
        ValueError: If the image cannot be opened.
    """
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:
        raise ValueError(f"Cannot open image: {exc}") from exc

    if not _PYZBAR_AVAILABLE:
        return []
    decoded_objects = pyzbar_decode(image)
    results = []
    for obj in decoded_objects:
        try:
            data = obj.data.decode("utf-8")
            results.append(data)
        except UnicodeDecodeError:
            # Fall back to latin-1 so non-UTF-8 codes are not silently dropped
            results.append(obj.data.decode("latin-1"))

    return results


def decode_qr_from_base64(b64_string: str) -> list[str]:
    """
    Decode all QR codes from a base64-encoded image.

    Args:
        b64_string: Base64-encoded image (optionally with a data-URI prefix).

    Returns:
        List of decoded string values.
    """
    # Strip optional data-URI prefix: "data:image/png;base64,..."
    if "," in b64_string:
        b64_string = b64_string.split(",", 1)[1]

    image_bytes = base64.b64decode(b64_string)
    return decode_qr_from_bytes(image_bytes)


def classify_qr_content(value: str) -> str:
    """
    Classify a decoded QR value.

    Returns:
        "url" if the value looks like an HTTP/HTTPS URL, otherwise "text".
    """
    return "url" if _URL_RE.match(value.strip()) else "text"
