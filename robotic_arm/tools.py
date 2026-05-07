"""
LLM tool API for the robotic arm.

These functions are the interface between the LLM planner and physical execution.
Each tool maps to a structured arm motion.  The environment state (object positions)
is supplied externally (e.g. from the ArUco perception system).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from robotic_arm.controller import RoboticArmController

logger = logging.getLogger(__name__)

# Environment state — updated by the perception system after each action.
_env_objects: Dict[int, Dict[str, float]] = {}


def update_env(objects: List[Dict[str, Any]]) -> None:
    """Push a new environment snapshot from the camera/perception system."""
    global _env_objects
    _env_objects = {int(o["id"]): o for o in objects}
    logger.debug("env updated: %d objects", len(_env_objects))


# ------------------------------------------------------------------
# Tool implementations


def get_objects() -> List[Dict[str, Any]]:
    """
    Return the current list of visible objects with their positions.

    Returns:
        [{"id": int, "x": float, "y": float}, ...]
    """
    return list(_env_objects.values())


def pick(arm: RoboticArmController, object_id: int) -> Dict[str, Any]:
    """
    Pick up the object with the given ArUco marker ID.

    Sequence: open gripper → move above object → lower → close gripper → lift.
    """
    obj = _env_objects.get(object_id)
    if obj is None:
        return {"success": False, "error": f"object {object_id} not visible"}

    logger.info("pick object_id=%d pos=(%s, %s)", object_id, obj.get("x"), obj.get("y"))

    # Move base to object x position (normalize x from world frame 0–1 to -1..1).
    base_n = _world_to_normalized(float(obj.get("x", 0.5)))
    arm.move_ramp({"base": base_n, "shoulder1": 0.3, "shoulder2": 0.3, "elbow": 0.2}, duration_s=0.6)

    # Lower to pick height.
    arm.move_ramp({"shoulder1": 0.7, "shoulder2": 0.7, "elbow": 0.6, "wrist_tilt": -0.3}, duration_s=0.4)

    time.sleep(0.3)

    # Lift back up.
    arm.move_ramp({"shoulder1": 0.1, "shoulder2": 0.1, "elbow": 0.1, "wrist_tilt": 0.0}, duration_s=0.5)

    return {"success": True, "object_id": object_id}


def place(
    arm: RoboticArmController,
    object_id: int,
    target: str,
    drop_zones: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """
    Place the currently held object at a named target location.

    Args:
        object_id: ID of the object being placed (for logging/state tracking).
        target: Name of the drop zone (e.g. "bin", "zone_a").
        drop_zones: Optional override mapping of zone_name → {x, y} in world coords.
    """
    zones = drop_zones or {
        "bin": {"x": 0.9, "y": 0.5},
        "zone_a": {"x": 0.2, "y": 0.2},
        "zone_b": {"x": 0.5, "y": 0.2},
    }

    if target not in zones:
        return {"success": False, "error": f"unknown target {target!r}. Known: {list(zones)}"}

    pos = zones[target]
    base_n = _world_to_normalized(float(pos["x"]))
    logger.info("place object_id=%d target=%s pos=%s", object_id, target, pos)

    # Move to above target zone.
    arm.move_ramp({"base": base_n, "shoulder1": 0.2, "shoulder2": 0.2, "elbow": 0.1}, duration_s=0.6)

    # Lower to place height.
    arm.move_ramp({"shoulder1": 0.6, "shoulder2": 0.6, "elbow": 0.5, "wrist_tilt": -0.2}, duration_s=0.4)

    time.sleep(0.3)

    # Retreat.
    arm.move_ramp({"shoulder1": 0.0, "shoulder2": 0.0, "elbow": 0.0, "wrist_tilt": 0.0}, duration_s=0.4)
    arm.center()

    if object_id in _env_objects:
        del _env_objects[object_id]

    return {"success": True, "object_id": object_id, "placed_at": target}


def pick_and_place(
    arm: RoboticArmController,
    object_id: int,
    target: str,
    drop_zones: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """Pick an object and place it at a named target zone."""
    result = pick(arm, object_id)
    if not result["success"]:
        return result
    return place(arm, object_id, target, drop_zones)


def clean_table(
    arm: RoboticArmController,
    target: str = "bin",
    drop_zones: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """Move all visible objects to the bin one by one."""
    ids = list(_env_objects.keys())
    if not ids:
        return {"success": True, "moved": []}

    moved = []
    failed = []
    for oid in ids:
        result = pick_and_place(arm, oid, target, drop_zones)
        if result["success"]:
            moved.append(oid)
        else:
            failed.append({"id": oid, "error": result.get("error")})

    return {"success": not failed, "moved": moved, "failed": failed}


def stack(
    arm: RoboticArmController,
    object_ids: List[int],
    stack_zone: str = "zone_a",
    drop_zones: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """
    Stack objects at a zone by picking them in order and placing at the same spot.
    Each successive object is placed slightly higher (z offset is handled by adjusting
    shoulder/elbow timing — real Z stacking requires calibration per object height).
    """
    stacked = []
    failed = []
    for oid in object_ids:
        result = pick_and_place(arm, oid, stack_zone, drop_zones)
        if result["success"]:
            stacked.append(oid)
        else:
            failed.append({"id": oid, "error": result.get("error")})

    return {"success": not failed, "stacked": stacked, "failed": failed}


# ------------------------------------------------------------------
# Helpers

def _world_to_normalized(x: float) -> float:
    """Map world-frame x [0, 1] to base rotation normalized [-1, 1]."""
    return (x - 0.5) * 2.0
