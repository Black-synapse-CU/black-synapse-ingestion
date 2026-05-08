#!/usr/bin/env python3
"""
ArUco Marker Detection Test Script

Tests ArUco detection from three sources:
  1. Live camera frame (via capture_frame.py /latest endpoint)
  2. A local image file (pass as argument)
  3. Continuous live feed (--live flag, prints detections until Ctrl+C)

Usage:
    python perception/test_qr_detection.py                   # test live camera frame
    python perception/test_qr_detection.py image.jpg         # test local image
    python perception/test_qr_detection.py --live            # continuous camera scan
    python perception/test_qr_detection.py --live --camera 0 # specific camera index
"""

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import requests

CAPTURE_URL = "http://127.0.0.1:8089/latest"

# Must match capture_frame.py
ARUCO_LOCATION_MAP: dict[int, str] = {
    17: "North Building",
}

# ── Detector (same config as capture_frame.py) ────────────────────────────────

_aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
_aruco_params = cv2.aruco.DetectorParameters()
_aruco_params.adaptiveThreshWinSizeMin = 3
_aruco_params.adaptiveThreshWinSizeMax = 53
_aruco_params.adaptiveThreshWinSizeStep = 4
_aruco_params.minMarkerPerimeterRate = 0.01
_aruco_params.polygonalApproxAccuracyRate = 0.1
_aruco_detector = cv2.aruco.ArucoDetector(_aruco_dict, _aruco_params)


def _preprocess(bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    thresh = cv2.adaptiveThreshold(
        enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
    )
    return cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)


def detect_aruco(bgr: np.ndarray) -> list[tuple[int, np.ndarray | None, bool]]:
    """
    Returns list of (marker_id, corners_4x2_or_None, used_preprocessing).
    Tries raw frame first, then preprocessed.
    """
    for preprocessed, frame in [(False, bgr), (True, _preprocess(bgr))]:
        corners, ids, _ = _aruco_detector.detectMarkers(frame)
        if ids is not None and len(ids) > 0:
            results = []
            for i, marker_id in enumerate(ids.flatten().tolist()):
                pts = corners[i].reshape(4, 2).astype(np.float32) if i < len(corners) else None
                results.append((int(marker_id), pts, preprocessed))
            return results
    return []


# ── Frame sources ─────────────────────────────────────────────────────────────

def frame_from_api() -> np.ndarray | None:
    try:
        resp = requests.get(CAPTURE_URL, timeout=5)
        resp.raise_for_status()
        arr = np.frombuffer(resp.content, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception as e:
        print(f"  Could not reach {CAPTURE_URL}: {e}")
        return None


def frame_from_file(path: str) -> np.ndarray | None:
    img = cv2.imread(path)
    if img is None:
        print(f"  Could not open image: {path}")
    return img


def frame_from_camera(index: int) -> np.ndarray | None:
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        print(f"  Could not open camera {index}")
        return None
    ret, frame = cap.read()
    cap.release()
    return frame if ret else None


# ── Report ────────────────────────────────────────────────────────────────────

def report(results: list[tuple[int, np.ndarray | None, bool]]):
    if not results:
        print("  No ArUco markers detected")
        return
    for marker_id, pts, preprocessed in results:
        location = ARUCO_LOCATION_MAP.get(marker_id, "(unknown location)")
        corners_ok = pts is not None and pts.size >= 8
        prep_note = " [needed preprocessing]" if preprocessed else ""
        print(f"  Marker ID {marker_id} → {location}  corners={'yes' if corners_ok else 'no'}{prep_note}")


def run_single(args):
    if args.image:
        bgr = frame_from_file(args.image)
        label = args.image
    elif args.camera is not None:
        bgr = frame_from_camera(args.camera)
        label = f"camera {args.camera}"
    else:
        print(f"Grabbing frame from {CAPTURE_URL} ...")
        bgr = frame_from_api()
        label = "live API frame"

    if bgr is None:
        sys.exit(1)

    print(f"\nSource: {label}  ({bgr.shape[1]}x{bgr.shape[0]})")
    results = detect_aruco(bgr)
    report(results)

    if not results:
        out = Path("qr_test_frame.jpg")
        cv2.imwrite(str(out), bgr)
        print(f"\n  Frame saved to {out} for inspection.")


def run_live(args):
    camera_index = args.camera if args.camera is not None else 0
    print(f"Live ArUco scan from camera {camera_index} (Ctrl+C to stop)\n")
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        print(f"Could not open camera {camera_index}")
        sys.exit(1)

    last_seen: set[int] = set()
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            hits = detect_aruco(frame)
            current_ids = {marker_id for marker_id, _, _ in hits}

            new = current_ids - last_seen
            gone = last_seen - current_ids

            for marker_id in new:
                location = ARUCO_LOCATION_MAP.get(marker_id, "(unknown)")
                print(f"[{time.strftime('%H:%M:%S')}] DETECTED  ID {marker_id} → {location}")
            for marker_id in gone:
                print(f"[{time.strftime('%H:%M:%S')}] gone      ID {marker_id}")

            last_seen = current_ids
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        cap.release()


def main():
    parser = argparse.ArgumentParser(description="ArUco marker detection test")
    parser.add_argument("image", nargs="?", help="Path to an image file to test")
    parser.add_argument("--live", action="store_true", help="Continuous scan from camera")
    parser.add_argument("--camera", type=int, default=None, help="Camera index")
    args = parser.parse_args()

    if args.live:
        run_live(args)
    else:
        run_single(args)


if __name__ == "__main__":
    main()
