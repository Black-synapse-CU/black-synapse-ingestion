"""
YOLO instance-segmentation workspace detector.

Detects and segments objects on the workspace from a live camera or image.
Reports per-object class, confidence, bounding box, and centroid pixel.

Requirements:
    pip install ultralytics

Usage:
    python robotic_arm/computer_vision/workspace_detector.py --mode camera
    python robotic_arm/computer_vision/workspace_detector.py --mode camera --model yolov8s-seg.pt
    python robotic_arm/computer_vision/workspace_detector.py --mode camera --classes cup bottle
    python robotic_arm/computer_vision/workspace_detector.py --mode camera --conf 0.4 --json
    python robotic_arm/computer_vision/workspace_detector.py --mode image --image snap.jpg
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

try:
    from ultralytics import YOLO
except ImportError:
    raise SystemExit("ultralytics is required — run: pip install ultralytics")


# --------------------------------------------------------------------------- #
# Color helpers
# --------------------------------------------------------------------------- #


def _class_color(cls_id: int) -> tuple[int, int, int]:
    """Deterministic, visually distinct BGR color per class id."""
    hue = (cls_id * 37 + 11) % 180
    hsv = np.uint8([[[hue, 210, 230]]])
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0][0]
    return int(bgr[0]), int(bgr[1]), int(bgr[2])


# --------------------------------------------------------------------------- #
# Drawing / annotation
# --------------------------------------------------------------------------- #


def _draw_detections(
    frame: np.ndarray,
    result,
    class_filter: Optional[set[str]],
) -> tuple[np.ndarray, list[dict]]:
    """
    Overlay segmentation masks, contours, and labels onto *frame*.

    Returns the annotated frame and a list of detection dicts:
        {"class", "confidence", "bbox": [x1,y1,x2,y2], "centroid": [cx,cy]}
    """
    overlay = frame.copy()
    detections: list[dict] = []

    if result.masks is None:
        return overlay, detections

    for seg_pts, box in zip(result.masks.xy, result.boxes):
        cls_id = int(box.cls[0])
        cls_name = result.names[cls_id]
        conf = float(box.conf[0])

        if class_filter and cls_name not in class_filter:
            continue

        pts = seg_pts.astype(np.int32)
        if len(pts) < 3:
            continue

        color = _class_color(cls_id)

        # Filled mask (alpha blend)
        mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        cv2.fillPoly(mask, [pts], 255)
        colored = overlay.copy()
        colored[mask > 0] = color
        cv2.addWeighted(colored, 0.40, overlay, 0.60, 0, overlay)

        # Contour outline
        cv2.polylines(overlay, [pts.reshape(-1, 1, 2)], True, color, 2)

        # Centroid via image moments
        M = cv2.moments(mask)
        if M["m00"] > 0:
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])
        else:
            x1r, y1r, x2r, y2r = map(int, box.xyxy[0])
            cx, cy = (x1r + x2r) // 2, (y1r + y2r) // 2

        cv2.circle(overlay, (cx, cy), 5, (255, 255, 255), -1)
        cv2.circle(overlay, (cx, cy), 5, color, 2)

        # Label above bounding box
        x1, y1 = int(box.xyxy[0][0]), int(box.xyxy[0][1])
        x2, y2 = int(box.xyxy[0][2]), int(box.xyxy[0][3])
        label = f"{cls_name}  {conf:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        lx, ly = x1, max(y1 - 6, th + 6)
        cv2.rectangle(overlay, (lx, ly - th - 4), (lx + tw + 4, ly + 2), color, -1)
        cv2.putText(overlay, label, (lx + 2, ly - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

        detections.append({
            "class": cls_name,
            "confidence": round(conf, 3),
            "bbox": [x1, y1, x2, y2],
            "centroid": [cx, cy],
        })

    return overlay, detections


# --------------------------------------------------------------------------- #
# Camera helpers (MSMF warmup)
# --------------------------------------------------------------------------- #


def _open_camera(index: int) -> Optional[cv2.VideoCapture]:
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        print(f"Error: could not open camera {index}")
        return None
    for _ in range(30):
        ret, _ = cap.read()
        if ret:
            return cap
    print("Error: camera failed to produce a frame after warmup.")
    cap.release()
    return None


# --------------------------------------------------------------------------- #
# Run modes
# --------------------------------------------------------------------------- #


def run_camera(
    model_path: str,
    camera_index: int,
    class_filter: Optional[set[str]],
    conf: float,
    print_json: bool,
) -> None:
    model = YOLO(model_path)
    cap = _open_camera(camera_index)
    if cap is None:
        return

    print("Workspace detector live — press 'q' to quit.")
    consecutive_failures = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            consecutive_failures += 1
            if consecutive_failures >= 10:
                print("Error: camera stopped producing frames.")
                break
            continue
        consecutive_failures = 0

        results = model(frame, conf=conf, device="cpu", verbose=False)
        annotated, detections = _draw_detections(frame, results[0], class_filter)

        # Detection count banner
        cv2.rectangle(annotated, (0, 0), (200, 26), (30, 30, 30), -1)
        cv2.putText(annotated, f"detected: {len(detections)}", (6, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.58, (200, 200, 200), 1, cv2.LINE_AA)

        cv2.imshow("Workspace Detector", annotated)

        if print_json and detections:
            print(json.dumps({"timestamp": round(time.time(), 3), "items": detections}))

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


def run_image(
    image_path: str,
    model_path: str,
    class_filter: Optional[set[str]],
    conf: float,
    print_json: bool,
) -> None:
    path = Path(image_path)
    if not path.exists():
        print(f"Error: image not found: {path}")
        return

    frame = cv2.imread(str(path))
    if frame is None:
        print(f"Error: failed to load image: {path}")
        return

    model = YOLO(model_path)
    results = model(frame, conf=conf, device="cpu", verbose=False)
    annotated, detections = _draw_detections(frame, results[0], class_filter)

    if print_json:
        print(json.dumps({"items": detections}, indent=2))

    cv2.imshow("Workspace Detector", annotated)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(
        description="YOLO segmentation-based workspace item detector."
    )
    parser.add_argument("--mode", choices=["camera", "image"], required=True)
    parser.add_argument("--image", type=str, help="Image path (--mode image).")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument(
        "--model",
        type=str,
        default="yolov8n-seg.pt",
        help="YOLO segmentation weights (default: yolov8n-seg.pt, auto-downloaded).",
    )
    parser.add_argument(
        "--classes",
        nargs="+",
        metavar="CLASS",
        help="Only show these class names, e.g. --classes cup bottle person.",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=0.35,
        help="Minimum confidence threshold (default: 0.35).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print detections as JSON each frame.",
    )
    args = parser.parse_args()

    class_filter = set(args.classes) if args.classes else None

    if args.mode == "image":
        if not args.image:
            parser.error("--image is required when --mode image")
        run_image(args.image, args.model, class_filter, args.conf, args.json)
    else:
        run_camera(args.model, args.camera_index, class_filter, args.conf, args.json)


if __name__ == "__main__":
    main()
