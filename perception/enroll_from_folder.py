#!/usr/bin/env python3
"""
Bulk-enroll a person's face from a folder of images.

For each image in the folder, extracts a DeepFace embedding and saves it to
face_embeddings.json. Any anonymous user_N entries whose embeddings are similar
enough are automatically merged into the named person.

Usage:
    python perception/enroll_from_folder.py --name "Alice" --folder /path/to/photos/
    python perception/enroll_from_folder.py --name "Alice" --folder ./photos \\
        --embeddings-path /custom/face_embeddings.json --detector opencv
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

# ── Config (mirrors capture_frame.py env vars) ────────────────────────
DEFAULT_EMBEDDINGS_PATH = str(Path(__file__).resolve().parent.parent / "face_embeddings.json")
DEFAULT_EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Facenet512")
DEFAULT_DETECTOR = os.getenv("DEEPFACE_DETECTOR_BACKEND", "yunet")
DEFAULT_MAX_PER_USER = int(os.getenv("MAX_EMBEDDINGS_PER_USER", "20"))
MERGE_SIMILARITY_THRESHOLD = float(os.getenv("MERGE_SIMILARITY_THRESHOLD", "0.62"))

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff"}

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("enroll")


# ── Embedding DB helpers (mirrors capture_frame.py) ───────────────────

def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity in [0, 1]. Same person typically >= 0.5."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a < 1e-9 or norm_b < 1e-9:
        return 0.0
    return float(np.clip(dot / (norm_a * norm_b), 0, 1))


def _load_embeddings(path: str) -> dict:
    """Load face_embeddings.json, migrating legacy single-embedding format."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        for entry in data.values():
            if isinstance(entry, dict) and "embedding" in entry and "embeddings" not in entry:
                entry["embeddings"] = [entry.pop("embedding")]
        return data
    except Exception as exc:
        log.warning("Failed to load embeddings file: %s", exc)
        return {}


def _save_embeddings(data: dict, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── Core logic ────────────────────────────────────────────────────────

def _extract_embedding(
    img: np.ndarray,
    df,
    model_name: str,
    detector_backend: str,
) -> Optional[np.ndarray]:
    """
    Extract a single face embedding from an image.

    If multiple faces are detected, uses the largest (by bounding-box area).
    Returns None if no face was detected.
    """
    try:
        objs = df.represent(
            img_path=img,
            model_name=model_name,
            detector_backend=detector_backend,
            enforce_detection=False,
            align=True,
        )
    except Exception as exc:
        log.debug("represent() failed: %s", exc)
        return None

    if not objs:
        return None

    if len(objs) > 1:
        log.warning("  Multiple faces detected — using the largest.")
        objs = sorted(
            objs,
            key=lambda o: (o.get("facial_area") or {}).get("w", 0)
                         * (o.get("facial_area") or {}).get("h", 0),
            reverse=True,
        )

    emb = objs[0].get("embedding")
    if not emb:
        return None
    return np.array(emb, dtype=np.float64)


def enroll(
    name: str,
    folder: Path,
    embeddings_path: str,
    model_name: str,
    detector_backend: str,
    max_per_user: int,
) -> None:
    """Enroll all faces found in *folder* under *name*."""
    # Load DeepFace
    try:
        from deepface import DeepFace
    except ImportError:
        log.error("DeepFace is not installed. Run: pip install deepface")
        sys.exit(1)

    # Collect image paths
    images = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if not images:
        log.error("No supported image files found in %s", folder)
        sys.exit(1)

    print(f"Found {len(images)} image(s) in {folder}. Extracting embeddings…\n")

    # Extract embeddings from each image
    new_embeddings: List[List[float]] = []
    skipped = 0

    for img_path in images:
        img = cv2.imread(str(img_path))
        if img is None:
            log.warning("  [SKIP] Cannot read image: %s", img_path.name)
            skipped += 1
            continue

        emb = _extract_embedding(img, DeepFace, model_name, detector_backend)
        if emb is None:
            log.warning("  [SKIP] No face detected in: %s", img_path.name)
            skipped += 1
            continue

        new_embeddings.append(emb.tolist())
        print(f"  [OK]   {img_path.name}")

    print(f"\nExtracted {len(new_embeddings)} embedding(s), skipped {skipped} image(s).")

    if not new_embeddings:
        print("Nothing to save.")
        return

    # Load DB
    db = _load_embeddings(embeddings_path)

    # Merge matching anonymous user_N entries
    merged_from: List[str] = []
    for uid in list(db.keys()):
        if uid == name:
            continue
        if not (uid.startswith("user_") and uid[5:].isdigit()):
            continue
        stored_list = db[uid].get("embeddings", [])
        if not isinstance(stored_list, list):
            stored_list = [stored_list]
        matched = False
        for stored_raw in stored_list:
            stored = np.array(stored_raw, dtype=np.float64)
            for new_emb in new_embeddings:
                if (
                    len(stored) == len(new_emb)
                    and _cosine_similarity(stored, np.array(new_emb)) >= MERGE_SIMILARITY_THRESHOLD
                ):
                    matched = True
                    break
            if matched:
                break
        if matched:
            # Absorb their embeddings
            for e in stored_list:
                el = e if isinstance(e, list) else list(e)
                if el not in new_embeddings:
                    new_embeddings.append(el)
            merged_from.append(uid)
            del db[uid]
            log.info("Merged %s → %s", uid, name)

    # Add/update named entry
    if name in db:
        existing: List = db[name].get("embeddings", [])
        added = 0
        for e in new_embeddings:
            if e not in existing and len(existing) < max_per_user:
                existing.append(e)
                added += 1
        db[name]["embeddings"] = existing
    else:
        capped = new_embeddings[:max_per_user]
        db[name] = {"embeddings": capped, "first_seen": time.time()}
        added = len(capped)

    _save_embeddings(db, embeddings_path)

    # Summary
    total_stored = len(db[name]["embeddings"])
    print(f"\n✓ Enrolled '{name}'")
    print(f"  Embeddings added this run : {added}")
    print(f"  Total embeddings for '{name}': {total_stored}")
    if merged_from:
        print(f"  Merged anonymous entries  : {', '.join(merged_from)}")
    print(f"  Saved to: {embeddings_path}")


# ── CLI entry point ───────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk-enroll a person's face from a folder of images."
    )
    parser.add_argument("--name", required=True, help="Name to enroll (e.g. 'Alice')")
    parser.add_argument("--folder", required=True, type=Path, help="Directory of face images")
    parser.add_argument(
        "--embeddings-path",
        default=DEFAULT_EMBEDDINGS_PATH,
        help=f"Path to face_embeddings.json (default: {DEFAULT_EMBEDDINGS_PATH})",
    )
    parser.add_argument(
        "--detector",
        default=DEFAULT_DETECTOR,
        help=f"DeepFace detector backend (default: {DEFAULT_DETECTOR})",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_EMBEDDING_MODEL,
        help=f"DeepFace embedding model (default: {DEFAULT_EMBEDDING_MODEL})",
    )
    parser.add_argument(
        "--max-per-user",
        type=int,
        default=DEFAULT_MAX_PER_USER,
        help=f"Max embeddings stored per person (default: {DEFAULT_MAX_PER_USER})",
    )
    args = parser.parse_args()

    folder = args.folder.expanduser().resolve()
    if not folder.is_dir():
        log.error("Folder does not exist or is not a directory: %s", folder)
        sys.exit(1)

    enroll(
        name=args.name.strip(),
        folder=folder,
        embeddings_path=args.embeddings_path,
        model_name=args.model,
        detector_backend=args.detector,
        max_per_user=args.max_per_user,
    )


if __name__ == "__main__":
    main()
