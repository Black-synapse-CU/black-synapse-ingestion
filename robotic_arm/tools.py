"""
LLM tool API for the robotic arm.

Public functions (called by the LLM planner):
    wave_hello(orch)
    look_at_workspace(client, prompt)
    inspect_object(orch, client, object_id, move_to_workspace)
    dance(orch)
    ik_pick_and_place(orch, object_id, target, H)

ArUco utilities (used internally and by the IK pipeline):
    get_objects(camera_index)        → {marker_id: (cx_px, cy_px)}
    calibrate_homography(...)        → 3×3 np.ndarray H

Servo module: action_servos (ServoOrchestrator + Sequence/Pose/Keyframe).
VLM: OpenAI GPT-4o via a custom _describe_frame wrapper.
Cameras: workspace=1, arm=2, main=0.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import math
import os
import sys
import time
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from action_servos.groups import ServoOrchestrator
from action_servos.sequences import Pose, Sequence
from worker.app.arm_actions import execute_action

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Camera indices
# ---------------------------------------------------------------------------

CAMERA_WORKSPACE: int = int(os.getenv("CAMERA_WORKSPACE", "1"))
CAMERA_ARM: int       = int(os.getenv("CAMERA_ARM",       "2"))
CAMERA_MAIN: int      = int(os.getenv("CAMERA_MAIN",      "0"))

# ---------------------------------------------------------------------------
# ArUco config — must match aruco_generator.py (DICT_4X4_50)
# ---------------------------------------------------------------------------

ARUCO_DICT_ID: int     = cv2.aruco.DICT_4X4_50
REF_MARKER_IDS         = [0, 1, 2, 3]   # loc_0–loc_3: used as homography anchors
OBJ_MARKER_IDS         = [4, 5, 6]      # cube_1, cube_2, bin_1: pick targets

# ---------------------------------------------------------------------------
# IK parameters — MUST BE SET FROM PHYSICAL CALIBRATION
# ---------------------------------------------------------------------------

# Link lengths in cm (measure from the physical arm).
L1_CM: float = float(os.getenv("L1_CM", "15.0"))   # shoulder pivot → elbow pivot
L2_CM: float = float(os.getenv("L2_CM", "12.0"))   # elbow pivot → wrist centre

# Physical degree range each joint sweeps (min_deg to max_deg).
# These define how IK angles map to normalized [-1, 1] servo space.
# Set from calibration data (robotic_arm/calibrate.py).  # CALIBRATION REQUIRED
BASE_DEG_RANGE:     float = float(os.getenv("BASE_DEG_RANGE",     "180.0"))
SHOULDER_DEG_RANGE: float = float(os.getenv("SHOULDER_DEG_RANGE", "180.0"))
ELBOW_DEG_RANGE:    float = float(os.getenv("ELBOW_DEG_RANGE",    "180.0"))

# Physical angle (degrees) that corresponds to normalized 0.0 for each joint.
# Offset from the servo's mechanical centre.  # CALIBRATION REQUIRED
BASE_ZERO_OFFSET_N:     float = float(os.getenv("BASE_ZERO_OFFSET_N",     "0.0"))
SHOULDER_ZERO_OFFSET_N: float = float(os.getenv("SHOULDER_ZERO_OFFSET_N", "0.0"))
ELBOW_ZERO_OFFSET_N:    float = float(os.getenv("ELBOW_ZERO_OFFSET_N",    "0.0"))

# ---------------------------------------------------------------------------
# Pick/place motion constants (normalized)
# ---------------------------------------------------------------------------

APPROACH_HEIGHT_N: float = 0.4    # shoulder when hovering above object
GRASP_HEIGHT_N:    float = -0.2   # shoulder lowered to table surface
LIFT_HEIGHT_N:     float = 0.5    # shoulder after grasping
GRIPPER_OPEN_N:    float = 1.0
GRIPPER_CLOSED_N:  float = -1.0

# Named drop targets: world-frame (x_cm, y_cm) measured from robot base.
# MUST BE MEASURED on the physical workspace and updated here.  # CALIBRATION REQUIRED
DROP_TARGETS: Dict[str, Tuple[float, float]] = {
    "bin":    (20.0,  5.0),
    "zone_a": (10.0, 10.0),
    "zone_b": (15.0, 10.0),
}

_SLOW_STEPS: int = 50   # interpolation steps per keyframe (higher = smoother)

# Cached homography matrix set by calibrate_homography().
_H_CACHE: Optional[np.ndarray] = None


# ===========================================================================
# Private helpers
# ===========================================================================


def _capture_frame(camera_index: int, warmup_frames: int = 5) -> Optional[np.ndarray]:
    """
    Open a camera, discard warmup frames, capture one BGR frame, release.

    Uses CAP_V4L2 on Linux/Jetson and DirectShow on Windows for reliability.

    Returns:
        BGR numpy array, or None if the camera could not be opened or read.
    """
    if sys.platform.startswith("linux"):
        cap = cv2.VideoCapture(camera_index, cv2.CAP_V4L2)
    elif sys.platform == "win32":
        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(camera_index)

    if not cap.isOpened():
        logger.error("Could not open camera %d", camera_index)
        return None

    for _ in range(warmup_frames):
        cap.read()

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        logger.error("Camera %d failed to produce a frame", camera_index)
        return None

    return frame


def _build_aruco_detector() -> cv2.aruco.ArucoDetector:
    """
    Build an ArucoDetector tuned for the workspace (small/distant markers,
    uneven lighting).  Matches the params in aruco_detection._build_detector.
    """
    aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT_ID)
    params = cv2.aruco.DetectorParameters()
    params.minMarkerPerimeterRate = 0.01          # detect small/distant markers
    params.adaptiveThreshWinSizeMin  = 3
    params.adaptiveThreshWinSizeMax  = 53
    params.adaptiveThreshWinSizeStep = 10
    params.minDistanceToBorder = 0                # allow edge markers
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return cv2.aruco.ArucoDetector(aruco_dict, params)


async def _describe_frame(pil_img: Image.Image, client: Any, prompt: str) -> str:
    """
    Encode pil_img as a base64 PNG and call GPT-4o Vision with a custom prompt.

    Mirrors describe_image_with_openai() from worker/app/pdf_processor.py but
    accepts a caller-controlled prompt instead of the fixed document description.

    Args:
        pil_img: PIL Image (RGB).
        client:  Configured openai.OpenAI client instance.
        prompt:  Instruction sent to GPT-4o.

    Returns:
        Description string, or empty string on failure.
    """
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    data_url = f"data:image/png;base64,{img_b64}"

    try:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                    ],
                }
            ],
            max_tokens=500,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("GPT-4o vision call failed: %s", exc)
        return ""


def _pixel_to_world(cx: float, cy: float, H: np.ndarray) -> Tuple[float, float]:
    """
    Apply homography H to map pixel centre (cx, cy) to world (x_cm, y_cm).

    Args:
        cx, cy: Pixel centre of the detected object.
        H:      3×3 homography matrix from calibrate_homography().

    Returns:
        (world_x_cm, world_y_cm) tuple.
    """
    pt = np.array([[[float(cx), float(cy)]]], dtype=np.float32)
    world_pt = cv2.perspectiveTransform(pt, H)
    return float(world_pt[0, 0, 0]), float(world_pt[0, 0, 1])


def _solve_ik(
    world_x: float,
    world_y: float,
    world_z: float,
    l1: float = L1_CM,
    l2: float = L2_CM,
) -> Optional[Tuple[float, float, float]]:
    """
    Solve 2-DOF planar IK for the arm given a world-frame Cartesian target.

    Uses the law of cosines.  The shoulder and elbow are treated as a 2-link
    planar arm in the vertical plane defined by the base pan angle.

    Args:
        world_x, world_y: Horizontal target position (cm from robot base).
        world_z:          Vertical height of target (cm; 0 = table surface).
        l1:               Upper-arm link length (cm).
        l2:               Forearm link length (cm).

    Returns:
        (base_angle_rad, shoulder_angle_rad, elbow_angle_rad) or None if the
        target is unreachable or the solution violates joint limits.
    """
    base_angle = math.atan2(world_y, world_x)
    r = math.sqrt(world_x ** 2 + world_y ** 2)    # horizontal reach
    d = math.sqrt(r ** 2 + world_z ** 2)          # straight-line distance to target

    if d > l1 + l2:
        logger.warning("IK: target unreachable (d=%.2f > L1+L2=%.2f)", d, l1 + l2)
        return None
    if d < abs(l1 - l2):
        logger.warning("IK: target too close for solution (d=%.2f)", d)
        return None

    cos_elbow = (l1 ** 2 + l2 ** 2 - d ** 2) / (2 * l1 * l2)
    cos_elbow = max(-1.0, min(1.0, cos_elbow))    # numerical clamp
    elbow_angle = math.acos(cos_elbow)

    cos_sh_add = (l1 ** 2 + d ** 2 - l2 ** 2) / (2 * l1 * d)
    cos_sh_add = max(-1.0, min(1.0, cos_sh_add))
    shoulder_angle = math.atan2(world_z, r) + math.acos(cos_sh_add)

    return base_angle, shoulder_angle, elbow_angle


def _ik_to_normalized(
    base_r: float,
    shoulder_r: float,
    elbow_r: float,
) -> Tuple[float, float, float]:
    """
    Convert IK angles (radians) to normalized [-1, 1] servo values.

    Accuracy depends on BASE_DEG_RANGE, SHOULDER_DEG_RANGE, ELBOW_DEG_RANGE,
    and the corresponding *_ZERO_OFFSET_N constants being calibrated.
    """
    half_base     = math.radians(BASE_DEG_RANGE     / 2.0)
    half_shoulder = math.radians(SHOULDER_DEG_RANGE / 2.0)
    half_elbow    = math.radians(ELBOW_DEG_RANGE    / 2.0)

    base_n     = base_r     / half_base     + BASE_ZERO_OFFSET_N
    shoulder_n = shoulder_r / half_shoulder + SHOULDER_ZERO_OFFSET_N
    elbow_n    = elbow_r    / half_elbow    + ELBOW_ZERO_OFFSET_N

    return (
        max(-1.0, min(1.0, base_n)),
        max(-1.0, min(1.0, shoulder_n)),
        max(-1.0, min(1.0, elbow_n)),
    )


# ===========================================================================
# Public tool functions
# ===========================================================================


def wave_hello(orch: ServoOrchestrator) -> str:
    """
    Wave hello for approximately 11 seconds.

    Delegates to the pre-built wave sequence in arm_actions:
    raise arm → wave out/in × 2 → return neutral.

    Args:
        orch: Live, opened ServoOrchestrator instance.

    Returns:
        Human-readable status string.
    """
    execute_action(orch, "wave", None)
    return "Waved hello."


def look_at_workspace(client: Any, prompt: str = "") -> str:
    """
    Capture a single frame from the workspace camera and ask GPT-4o to
    describe what it sees.

    Args:
        client: Configured openai.OpenAI client instance.
        prompt: Optional extra instruction appended to the default workspace
                description prompt.

    Returns:
        VLM description string, or an error string on failure.

    Note:
        If called from within an already-running event loop (e.g. FastAPI),
        replace asyncio.run() with: await _describe_frame(pil, client, p)
        and make this function async.
    """
    frame = _capture_frame(CAMERA_WORKSPACE)
    if frame is None:
        return "Error: could not capture workspace frame."

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)

    base_prompt = (
        "Describe the robot workspace: what objects are visible, "
        "their approximate positions, and any relevant spatial relationships."
    )
    effective_prompt = base_prompt + (" " + prompt if prompt else "")

    return asyncio.run(_describe_frame(pil, client, effective_prompt))


def inspect_object(
    orch: ServoOrchestrator,
    client: Any,
    object_id: int,
    move_to_workspace: bool = False,
) -> str:
    """
    Inspect an object using the arm-mounted camera (index 2).

    If move_to_workspace is True, first moves the arm to a forward-facing
    inspection pose so the wrist camera faces the workspace surface before
    capturing.  Otherwise captures immediately (assumes arm is pre-positioned).

    Args:
        orch:              Live, opened ServoOrchestrator instance.
        client:            Configured openai.OpenAI client instance.
        object_id:         ArUco marker ID of the target object (for the prompt).
        move_to_workspace: Move arm to inspection pose before capturing.

    Returns:
        VLM description string, or an error string on failure.
    """
    if move_to_workspace:
        inspection_pose = Pose.from_normalized(
            orch,
            base=0.0,
            shoulder=0.3,
            elbow=0.5,
            wrist_pitch=-0.2,   # tilt wrist down toward workspace
            wrist_roll=0.0,
            gripper=0.5,
        )
        Sequence().add(inspection_pose, duration_s=2.0, steps=_SLOW_STEPS).play(orch)
        time.sleep(0.5)         # allow camera to settle after motion

    frame = _capture_frame(CAMERA_ARM)
    if frame is None:
        return "Error: could not capture arm camera frame."

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)

    prompt = (
        f"Describe the object with ArUco marker ID {object_id}: "
        "its shape, color, estimated size, and position relative to the gripper."
    )
    return asyncio.run(_describe_frame(pil, client, prompt))


def dance(orch: ServoOrchestrator) -> str:
    """
    Perform an expressive multi-keyframe arm motion sequence (~13 seconds).

    The sequence uses rhythmic sweeping, wrist rolls, and shoulder oscillations.
    Tune the normalized pose values on hardware for the best visual effect.

    Args:
        orch: Live, opened ServoOrchestrator instance.

    Returns:
        "Danced." on completion.
    """
    def p(base, shoulder, elbow, wrist_pitch, wrist_roll, gripper) -> Pose:
        return Pose.from_normalized(
            orch,
            base=base, shoulder=shoulder, elbow=elbow,
            wrist_pitch=wrist_pitch, wrist_roll=wrist_roll, gripper=gripper,
        )

    (
        Sequence()
        .add(p( 0.6,  0.3,  0.5,  0.0,  0.7, 0.5), duration_s=1.5, steps=_SLOW_STEPS)  # sweep right
        .add(p(-0.6,  0.4,  0.4,  0.0, -0.7, 0.5), duration_s=1.5, steps=_SLOW_STEPS)  # sweep left
        .add(p( 0.0,  0.7,  0.2,  0.3,  0.0, 0.8), duration_s=1.2, steps=_SLOW_STEPS)  # reach up
        .add(p( 0.0,  0.5,  0.5,  0.0,  1.0, 0.5), duration_s=0.8, steps=_SLOW_STEPS)  # wrist spin +
        .add(p( 0.0,  0.5,  0.5,  0.0, -1.0, 0.5), duration_s=0.8, steps=_SLOW_STEPS)  # wrist spin −
        .add(p( 0.5,  0.2,  0.7, -0.2,  0.5, 0.5), duration_s=1.5, steps=_SLOW_STEPS)  # sweep right low
        .add(p( 0.0,  0.0,  0.0,  0.0,  0.0, 0.5), duration_s=2.0, steps=_SLOW_STEPS)  # neutral
    ).play(orch)

    return "Danced."


def get_objects(camera_index: int = CAMERA_WORKSPACE) -> Dict[int, Tuple[int, int]]:
    """
    Capture one frame and return ArUco pixel-centre positions for object
    markers (IDs 4–6).  Reference markers (IDs 0–3) are excluded.

    Args:
        camera_index: OpenCV camera index (default: CAMERA_WORKSPACE = 1).

    Returns:
        {marker_id: (cx_pixel, cy_pixel)} for visible object markers.
        Empty dict if none detected or camera fails.
    """
    frame = _capture_frame(camera_index)
    if frame is None:
        return {}

    detector = _build_aruco_detector()
    corners, ids, _ = detector.detectMarkers(frame)

    if ids is None:
        return {}

    result: Dict[int, Tuple[int, int]] = {}
    for corner, mid in zip(corners, ids.flatten()):
        if int(mid) not in OBJ_MARKER_IDS:
            continue
        cx, cy = corner.reshape(4, 2).mean(axis=0).astype(int)
        result[int(mid)] = (int(cx), int(cy))

    return result


def calibrate_homography(
    camera_index: int = CAMERA_WORKSPACE,
    marker_world_coords: Optional[Dict[int, Tuple[float, float]]] = None,
) -> Optional[np.ndarray]:
    """
    Detect the 4 reference ArUco markers (IDs 0–3) in the workspace camera
    and compute the homography matrix H that maps pixel coords to world coords
    (cm from robot base).

    The computed H is cached in _H_CACHE so ik_pick_and_place can use it
    without re-calibrating each call.

    Args:
        camera_index:        Camera to capture from (default: CAMERA_WORKSPACE = 1).
        marker_world_coords: Physical world positions of the 4 reference markers
                             in cm from the robot base, keyed by marker ID 0–3.
                             DEFAULT VALUES ARE PLACEHOLDERS — measure the actual
                             positions of your printed markers on the workspace mat
                             and pass them in.  # CALIBRATION REQUIRED

    Returns:
        3×3 np.ndarray homography H, or None if fewer than 4 ref markers detected.
    """
    if marker_world_coords is None:
        marker_world_coords = {
            0: (0.0,  0.0),    # front-left  — MEASURE AND UPDATE
            1: (25.0, 0.0),    # front-right — MEASURE AND UPDATE
            2: (0.0,  20.0),   # back-left   — MEASURE AND UPDATE
            3: (25.0, 20.0),   # back-right  — MEASURE AND UPDATE
        }

    frame = _capture_frame(camera_index)
    if frame is None:
        logger.error("calibrate_homography: camera %d failed", camera_index)
        return None

    detector = _build_aruco_detector()
    corners, ids, _ = detector.detectMarkers(frame)

    pixel_centers: Dict[int, Tuple[int, int]] = {}
    if ids is not None:
        for corner, mid in zip(corners, ids.flatten()):
            if int(mid) in REF_MARKER_IDS:
                cx, cy = corner.reshape(4, 2).mean(axis=0).astype(int)
                pixel_centers[int(mid)] = (int(cx), int(cy))

    missing = [i for i in REF_MARKER_IDS if i not in pixel_centers]
    if missing:
        logger.error(
            "calibrate_homography: reference markers not detected: %s "
            "(detected: %s). Ensure markers are visible and well-lit.",
            missing, list(pixel_centers.keys()),
        )
        return None

    src_pts = np.array(
        [pixel_centers[i] for i in REF_MARKER_IDS], dtype=np.float32
    )
    dst_pts = np.array(
        [marker_world_coords[i] for i in REF_MARKER_IDS], dtype=np.float32
    )

    H = cv2.getPerspectiveTransform(src_pts, dst_pts)

    global _H_CACHE
    _H_CACHE = H
    logger.info("Homography calibrated and cached.")
    return H


def ik_pick_and_place(
    orch: ServoOrchestrator,
    object_id: int,
    target: str,
    H: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """
    Full IK pick-and-place pipeline (~18–20 seconds).

    Steps:
        1.  Detect object pixel position via ArUco (get_objects).
        2.  Convert pixel → world coords via homography H.
        3.  Solve IK for pick position.
        4.  Open gripper.
        5.  Move to approach pose (above object).
        6.  Lower to grasp height.
        7.  Close gripper.
        8.  Lift to carry height.
        9.  Resolve target world position from DROP_TARGETS.
        10. Solve IK for place position, move above target.
        11. Lower to place height.
        12. Open gripper (release).
        13. Return to neutral.

    Args:
        orch:      Live, opened ServoOrchestrator instance.
        object_id: ArUco marker ID of the object to pick (4=cube_1, 5=cube_2, 6=bin_1).
        target:    Named drop target key in DROP_TARGETS (e.g. "bin", "zone_a").
        H:         Homography matrix. If None, uses the cached _H_CACHE from
                   calibrate_homography(). Raises via error dict if neither available.

    Returns:
        {"success": True,  "object_id": int, "placed_at": str} on success.
        {"success": False, "error": str}                        on failure.
    """
    # ── Step 1: resolve homography ──────────────────────────────────────────
    h_matrix = H if H is not None else _H_CACHE
    if h_matrix is None:
        return {
            "success": False,
            "error": "No homography matrix available. Run calibrate_homography() first.",
        }

    # ── Step 2: detect object ───────────────────────────────────────────────
    objects = get_objects(CAMERA_WORKSPACE)
    if object_id not in objects:
        return {"success": False, "error": f"Object {object_id} not visible in workspace camera."}

    cx_px, cy_px = objects[object_id]

    # ── Step 3: pixel → world ───────────────────────────────────────────────
    world_x, world_y = _pixel_to_world(cx_px, cy_px, h_matrix)
    logger.info(
        "Object %d at pixel (%d, %d) → world (%.1f cm, %.1f cm)",
        object_id, cx_px, cy_px, world_x, world_y,
    )

    # ── Step 4: IK solve for pick ───────────────────────────────────────────
    ik_pick = _solve_ik(world_x, world_y, 0.0)
    if ik_pick is None:
        return {
            "success": False,
            "error": f"Object {object_id} at ({world_x:.1f}, {world_y:.1f}) cm is unreachable.",
        }
    base_n, shoulder_n, elbow_n = _ik_to_normalized(*ik_pick)

    # ── Step 5: open gripper ────────────────────────────────────────────────
    execute_action(orch, "release_grip", None)

    # ── Step 6: move to approach pose (above object) ────────────────────────
    Sequence().add(
        Pose.from_normalized(
            orch,
            base=base_n, shoulder=APPROACH_HEIGHT_N, elbow=elbow_n,
            wrist_pitch=-0.2, wrist_roll=0.0, gripper=GRIPPER_OPEN_N,
        ),
        duration_s=2.5, steps=_SLOW_STEPS,
    ).play(orch)
    time.sleep(0.3)

    # ── Step 7: lower to grasp height ──────────────────────────────────────
    Sequence().add(
        Pose.from_normalized(
            orch,
            base=base_n, shoulder=GRASP_HEIGHT_N, elbow=elbow_n,
            wrist_pitch=-0.4, wrist_roll=0.0, gripper=GRIPPER_OPEN_N,
        ),
        duration_s=1.5, steps=_SLOW_STEPS,
    ).play(orch)
    time.sleep(0.2)

    # ── Step 8: close gripper ───────────────────────────────────────────────
    execute_action(orch, "grab", None)
    time.sleep(0.5)

    # ── Step 9: lift ────────────────────────────────────────────────────────
    Sequence().add(
        Pose.from_normalized(
            orch,
            base=base_n, shoulder=LIFT_HEIGHT_N, elbow=elbow_n,
            wrist_pitch=0.0, wrist_roll=0.0, gripper=GRIPPER_CLOSED_N,
        ),
        duration_s=1.5, steps=_SLOW_STEPS,
    ).play(orch)
    time.sleep(0.3)

    # ── Step 10: resolve target ─────────────────────────────────────────────
    if target not in DROP_TARGETS:
        return {
            "success": False,
            "error": f"Unknown target '{target}'. Known: {list(DROP_TARGETS)}",
        }
    target_wx, target_wy = DROP_TARGETS[target]

    # ── Step 11: IK solve for place ─────────────────────────────────────────
    ik_place = _solve_ik(target_wx, target_wy, 0.0)
    if ik_place is None:
        return {
            "success": False,
            "error": f"Target '{target}' at ({target_wx:.1f}, {target_wy:.1f}) cm is unreachable.",
        }
    t_base_n, t_shoulder_n, t_elbow_n = _ik_to_normalized(*ik_place)

    # ── Step 12: move above target ──────────────────────────────────────────
    Sequence().add(
        Pose.from_normalized(
            orch,
            base=t_base_n, shoulder=APPROACH_HEIGHT_N, elbow=t_elbow_n,
            wrist_pitch=-0.2, wrist_roll=0.0, gripper=GRIPPER_CLOSED_N,
        ),
        duration_s=2.5, steps=_SLOW_STEPS,
    ).play(orch)
    time.sleep(0.3)

    # ── Step 13: lower to place height ─────────────────────────────────────
    Sequence().add(
        Pose.from_normalized(
            orch,
            base=t_base_n, shoulder=GRASP_HEIGHT_N, elbow=t_elbow_n,
            wrist_pitch=-0.3, wrist_roll=0.0, gripper=GRIPPER_CLOSED_N,
        ),
        duration_s=1.5, steps=_SLOW_STEPS,
    ).play(orch)
    time.sleep(0.2)

    # ── Step 14: release ────────────────────────────────────────────────────
    execute_action(orch, "release_grip", None)
    time.sleep(0.4)

    # ── Step 15: return to neutral ──────────────────────────────────────────
    execute_action(orch, "neutral", None)

    return {"success": True, "object_id": object_id, "placed_at": target}
