"""
ArUco marker detection from a live camera feed or a static image.

Requires opencv-contrib-python (not plain opencv-python) for cv2.aruco support:
    pip install opencv-contrib-python
"""

import argparse
from pathlib import Path

import cv2
import numpy as np

# Ordered from most-commonly-used to largest; the script tries each in order
# when --dictionary is 'auto'.
ARUCO_DICTS = {
    "DICT_4X4_50": cv2.aruco.DICT_4X4_50,
    "DICT_4X4_100": cv2.aruco.DICT_4X4_100,
    "DICT_4X4_250": cv2.aruco.DICT_4X4_250,
    "DICT_4X4_1000": cv2.aruco.DICT_4X4_1000,
    "DICT_5X5_50": cv2.aruco.DICT_5X5_50,
    "DICT_5X5_100": cv2.aruco.DICT_5X5_100,
    "DICT_5X5_250": cv2.aruco.DICT_5X5_250,
    "DICT_5X5_1000": cv2.aruco.DICT_5X5_1000,
    "DICT_6X6_50": cv2.aruco.DICT_6X6_50,
    "DICT_6X6_100": cv2.aruco.DICT_6X6_100,
    "DICT_6X6_250": cv2.aruco.DICT_6X6_250,
    "DICT_6X6_1000": cv2.aruco.DICT_6X6_1000,
    "DICT_7X7_50": cv2.aruco.DICT_7X7_50,
    "DICT_7X7_100": cv2.aruco.DICT_7X7_100,
    "DICT_7X7_250": cv2.aruco.DICT_7X7_250,
    "DICT_7X7_1000": cv2.aruco.DICT_7X7_1000,
    "DICT_ARUCO_ORIGINAL": cv2.aruco.DICT_ARUCO_ORIGINAL,
}

# Only these IDs are considered valid — anything else is a false positive.
KNOWN_MARKERS: dict[int, str] = {
    0: "loc_0",
    1: "loc_1",
    2: "loc_2",
    3: "loc_3",
    4: "cube_1",
    5: "cube_2",
    6: "bin_1",
}

# Colors (BGR)
COLOR_BORDER = (0, 255, 0)
COLOR_ID_BG = (0, 255, 0)
COLOR_ID_TEXT = (0, 0, 0)
COLOR_CORNER = (0, 0, 255)


def _build_detector(dict_id: int) -> cv2.aruco.ArucoDetector:
    """Return an ArucoDetector for the given dictionary constant."""
    aruco_dict = cv2.aruco.getPredefinedDictionary(dict_id)
    params = cv2.aruco.DetectorParameters()
    # Lower perimeter threshold so small/distant markers aren't discarded
    params.minMarkerPerimeterRate = 0.01  # default 0.03
    # Wider adaptive threshold window range handles uneven lighting across frame
    params.adaptiveThreshWinSizeMin = 3
    params.adaptiveThreshWinSizeMax = 53
    params.adaptiveThreshWinSizeStep = 10
    # Allow markers whose corners are at or near the image border (default 3px)
    params.minDistanceToBorder = 0
    # More accurate corner position after initial detection
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return cv2.aruco.ArucoDetector(aruco_dict, params)


def draw_markers(
    image: np.ndarray,
    corners: list,
    ids: np.ndarray,
    dict_name: str = "",
) -> dict[int, tuple[int, int]]:
    """Draw bounding polygons, center dots, and ID labels on *image* in-place.

    Returns a dict of marker_id → (cx, cy) pixel center for known markers.
    """
    centers: dict[int, tuple[int, int]] = {}
    if ids is None:
        return centers

    for marker_corners, marker_id in zip(corners, ids.flatten()):
        if marker_id not in KNOWN_MARKERS:
            continue

        pts = marker_corners.reshape(4, 2).astype(int)
        cx, cy = pts.mean(axis=0).astype(int)
        centers[int(marker_id)] = (int(cx), int(cy))

        cv2.polylines(image, [pts], isClosed=True, color=COLOR_BORDER, thickness=2)

        for pt in pts:
            cv2.circle(image, tuple(pt), 4, COLOR_CORNER, -1)

        # Center dot
        cv2.circle(image, (cx, cy), 5, (255, 0, 255), -1)

        name = KNOWN_MARKERS[marker_id]
        label = f"{name} ({cx}, {cy})"

        x, y = pts[0]
        (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        cv2.rectangle(image, (x, y - h - 6), (x + w + 2, y), COLOR_ID_BG, -1)
        cv2.putText(
            image,
            label,
            (x + 1, y - 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            COLOR_ID_TEXT,
            2,
            cv2.LINE_AA,
        )

    return centers


def detect_frame(
    frame: np.ndarray,
    detectors: dict[str, cv2.aruco.ArucoDetector],
) -> tuple[np.ndarray, list[tuple[str, int, tuple[int, int]]]]:
    """
    Run all detectors against *frame* and annotate matches.

    Returns the annotated frame and a list of (dict_name, marker_id, (cx, cy)) tuples.
    """
    found: list[tuple[str, int, tuple[int, int]]] = []

    for dict_name, detector in detectors.items():
        corners, ids, _ = detector.detectMarkers(frame)
        if ids is not None and len(ids) > 0:
            centers = draw_markers(frame, corners, ids, dict_name)
            for mid, center in centers.items():
                found.append((dict_name, mid, center))

    return frame, found


def detect_aruco_from_camera(
    camera_index: int,
    dictionary: str,
) -> None:
    """Live ArUco detection from a webcam."""
    detectors = _select_detectors(dictionary)

    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        print(f"Error: could not open camera {camera_index}")
        return

    print(
        f"Starting ArUco detection (dicts: {list(detectors.keys())}, "
        f"camera: {camera_index}). Press 'q' to quit."
    )

    # MSMF on Windows often drops the first several frames during init;
    # discard up to 30 reads before giving up.
    warmup_failures = 0
    while warmup_failures < 30:
        ret, frame = cap.read()
        if ret:
            break
        warmup_failures += 1
    else:
        print("Error: camera failed to produce a frame after warmup.")
        cap.release()
        return

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

        frame, found = detect_frame(frame, detectors)

        for dict_name, mid, (cx, cy) in found:
            name = KNOWN_MARKERS[mid]
            print(f"[CAMERA] {name} (id={mid}) @ ({cx}, {cy})")

        cv2.imshow("ArUco Detection - Camera", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


def detect_aruco_in_image(image_path: str, dictionary: str) -> None:
    """ArUco detection on a single image file."""
    path = Path(image_path)
    if not path.exists():
        print(f"Error: image not found: {path}")
        return

    img = cv2.imread(str(path))
    if img is None:
        print(f"Error: failed to load image: {path}")
        return

    detectors = _select_detectors(dictionary)
    img, found = detect_frame(img, detectors)

    if found:
        for dict_name, mid, (cx, cy) in found:
            name = KNOWN_MARKERS[mid]
            print(f"[IMAGE] {name} (id={mid}) @ ({cx}, {cy})")
    else:
        print("[IMAGE] No ArUco markers found.")

    cv2.imshow("ArUco Detection - Image", img)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


def _select_detectors(dictionary: str) -> dict[str, cv2.aruco.ArucoDetector]:
    """Return a name → detector mapping based on the --dictionary argument."""
    if dictionary == "auto":
        return {name: _build_detector(did) for name, did in ARUCO_DICTS.items()}
    if dictionary not in ARUCO_DICTS:
        raise ValueError(
            f"Unknown dictionary '{dictionary}'. "
            f"Choose from: {list(ARUCO_DICTS.keys())} or 'auto'."
        )
    return {dictionary: _build_detector(ARUCO_DICTS[dictionary])}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect ArUco markers from a camera feed or image file."
    )
    parser.add_argument(
        "--mode",
        choices=["camera", "image"],
        required=True,
        help="Detection source: 'camera' for live feed, 'image' for a file.",
    )
    parser.add_argument(
        "--image",
        type=str,
        help="Path to image file (required when --mode image).",
    )
    parser.add_argument(
        "--camera-index",
        type=int,
        default=0,
        help="Camera device index (default: 0).",
    )
    parser.add_argument(
        "--dictionary",
        type=str,
        default="DICT_4X4_50",
        help=(
            "ArUco dictionary to use, e.g. DICT_4X4_50 (default). "
            "Pass 'auto' to try all built-in dictionaries simultaneously."
        ),
    )

    args = parser.parse_args()

    if args.mode == "image":
        if not args.image:
            parser.error("--image is required when --mode image")
        detect_aruco_in_image(args.image, args.dictionary)
    else:
        detect_aruco_from_camera(args.camera_index, args.dictionary)


if __name__ == "__main__":
    main()
