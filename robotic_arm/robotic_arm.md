# Robotic Arm Module

Python control layer for a 6-DOF arm driven by a Jetson + PCA9685 servo driver over I2C. Includes LLM tool API (wave, inspect, dance, IK pick-and-place), servo calibration, an interactive pulse visualizer, and a computer vision subsystem (ArUco + YOLO).

---

## Directory Structure

```
robotic_arm/
├── config.py                        # JointSpec + ArmLayout dataclasses
├── i2c_bridge.py                    # Direct I2C comms to PCA9685 (I2CBridge)
├── controller.py                    # Named-joint interface + smooth ramps
├── tools.py                         # LLM tool API (wave, dance, IK pick-and-place, VLM inspection)
├── visualizer.py                    # Tkinter raw-pulse slider GUI
├── calibrate.py                     # Interactive servo calibration REPL
├── requirements.txt
├── computer_vision/
│   ├── aruco_detection.py           # ArUco marker detection (camera or image)
│   ├── aruco_generator.py           # ArUco marker image generator
│   └── workspace_detector.py        # YOLO instance-segmentation detector
└── arduino/                         # Legacy — not used with Jetson+I2C setup
    └── servo_bridge/
        └── servo_bridge.ino
```

---

## Setup

```bash
pip install -r robotic_arm/requirements.txt
```

---

## Hardware

| Component | Details |
|-----------|---------|
| Host | Jetson (Nano / Orin / Xavier — any with I2C GPIO) |
| Servo driver | PCA9685 16-channel PWM board, I2C address 0x40 |
| Servos | Standard hobby servos, 400–2700 µs pulse range |
| Connection | I2C (SDA/SCL on the Jetson 40-pin header) |
| Cameras | 0 = main, 1 = workspace (overhead), 2 = arm-mounted |

The Jetson talks directly to the PCA9685 over I2C — no Arduino required. Wire SDA → pin 3, SCL → pin 5, GND, and 3.3 V (or 5 V for the PCA9685 VCC; servo power is separate).

---

## Channel Map

> `tools.py` uses the `action_servos` module (not `robotic_arm/config.py`) for servo control. The table below documents the `robotic_arm` config layout, used by `i2c_bridge.py`, `controller.py`, `visualizer.py`, and `calibrate.py`.

| Channel | Joint | Range |
|---------|-------|-------|
| 0 | base | 0° – 180° (center_deg=9°) |
| 1 | shoulder1 | 500–2500 µs |
| 2 | wrist_tilt | 500–2500 µs |
| 3 | shoulder2 | 900–1900 µs (center=1400 µs) |
| 4 | wrist_rotate | 500–2500 µs |
| 5 | elbow | 130° – 180° (center=180°) |

Limits are enforced in `config.py` via `JointSpec` and clamped before reaching the servo driver.

---

## i2c_bridge.py

Direct I2C bridge to the PCA9685. No Arduino required.

```python
from robotic_arm.i2c_bridge import I2CBridge

with I2CBridge(address=0x40) as bridge:
    bridge.ping()               # True if PCA9685 responds
    bridge.set_channel(0, 1500) # move channel 0 to 1500 µs
    bridge.center_all()         # all 16 channels → 1500 µs
```

Pulse widths are converted to 16-bit PCA9685 duty cycles internally:
`duty = round(pulse_us / 20000 * 65535)`

---

## config.py

Defines two dataclasses used by `i2c_bridge`, `controller`, `visualizer`, and `calibrate`.

**`JointSpec`** — one servo's hardware spec:

| Field | Type | Description |
|-------|------|-------------|
| `channel` | int | PCA9685 channel (0–15) |
| `min_us` | float | Minimum pulse width in µs |
| `max_us` | float | Maximum pulse width in µs |
| `center_us` | float | Center/rest pulse width in µs |
| `name` | str | Human-readable joint name |

```python
spec = JointSpec.from_degrees(channel=5, min_deg=130, max_deg=180, center_deg=180, name="elbow")

spec.clamp(pulse_us)         # clamp raw µs to [min_us, max_us]
spec.to_us(normalized)       # map normalized [-1, 1] → [min_us, max_us]
spec.to_normalized(pulse_us) # inverse of to_us
```

**`ArmLayout`** — the full joint map:

```python
from robotic_arm.config import ArmLayout

layout = ArmLayout()           # default channel map + limits
layout.elbow                   # JointSpec for channel 5
layout.all_joints              # dict of name → JointSpec
layout.by_channel(3)           # look up JointSpec by channel number
```

---

## controller.py

Named-joint interface on top of any `ArmBridge` (I2CBridge, or any object with `set_channel` / `center_all` / `ping`).

```python
from robotic_arm.config import ArmLayout
from robotic_arm.i2c_bridge import I2CBridge
from robotic_arm.controller import RoboticArmController

with I2CBridge() as bridge:
    arm = RoboticArmController(bridge, layout=ArmLayout())

    arm.set_joint("base", 0.0)         # normalized: 0.0 = center
    arm.set_joint("shoulder2", -1.0)   # normalized: -1.0 = min_us
    arm.set_joint_us("elbow", 2200)    # raw µs

    arm.center()                        # all joints to their center_us
    arm.center_all_channels()           # all 16 channels → 1500 µs

    arm.get_joint_us("base")            # last sent µs, or None
    arm.get_joint_normalized("base")    # last sent normalized, or None

    # Smooth multi-joint motion (smoothstep interpolation)
    arm.move_ramp(
        target={"base": 0.5, "shoulder1": 0.3, "shoulder2": 0.3, "elbow": 0.8},
        duration_s=0.8,
        steps=30,
        normalized=True,
    )
```

`move_ramp` interpolates all listed joints simultaneously using a smoothstep curve (`t² (3 − 2t)`).

---

## tools.py

LLM tool API for the robotic arm. Uses `action_servos.ServoOrchestrator` + `Sequence`/`Pose`/`Keyframe` for servo control and OpenAI GPT-4o for vision tasks.

### Camera indices

| Index | Role |
|-------|------|
| 0 | Main camera |
| 1 | Workspace (overhead) — used by `look_at_workspace`, `get_objects`, `calibrate_homography`, `ik_pick_and_place` |
| 2 | Arm-mounted — used by `inspect_object` |

Override via env vars: `CAMERA_WORKSPACE`, `CAMERA_ARM`, `CAMERA_MAIN`.

---

### wave_hello

```python
from robotic_arm.tools import wave_hello

wave_hello(orch)   # raises arm, waves out/in × 2, returns neutral (~11 s)
```

Delegates to `execute_action(orch, "wave", None)` from `worker/app/arm_actions.py`.

---

### look_at_workspace

```python
from robotic_arm.tools import look_at_workspace

description = look_at_workspace(openai_client)
description = look_at_workspace(openai_client, prompt="Focus on any red objects.")
```

Captures one frame from camera 1 (workspace), sends it to GPT-4o Vision with a default workspace description prompt (optionally extended by `prompt`). Returns the VLM's description string.

---

### inspect_object

```python
from robotic_arm.tools import inspect_object

# Arm already positioned — capture immediately
result = inspect_object(orch, openai_client, object_id=4)

# Move arm to inspection pose first, then capture
result = inspect_object(orch, openai_client, object_id=5, move_to_workspace=True)
```

Captures from camera 2 (arm-mounted). When `move_to_workspace=True`, moves the arm to a forward-facing pose (shoulder=0.3, elbow=0.5, wrist_pitch=-0.2) before capturing. Returns GPT-4o description.

---

### dance

```python
from robotic_arm.tools import dance

dance(orch)   # 7-keyframe expressive sequence (~10 s), returns "Danced."
```

Sweeps, wrist spins, reach-up, and neutral return. Tune normalized pose values in `tools.py` on hardware.

---

### get_objects

```python
from robotic_arm.tools import get_objects

objects = get_objects()            # workspace camera (default)
objects = get_objects(camera_index=1)

# Returns: {marker_id: (cx_pixel, cy_pixel)}
# e.g. {4: (320, 240), 5: (180, 310)}
```

Captures one frame, runs ArUco detection (DICT_4X4_50), and returns pixel centroids for **object markers only** (IDs 4–6: cube_1, cube_2, bin_1). Reference markers (IDs 0–3) are excluded.

---

### calibrate_homography

```python
from robotic_arm.tools import calibrate_homography

H = calibrate_homography()

# With custom physical measurements (required for accuracy):
H = calibrate_homography(
    marker_world_coords={
        0: (0.0,  0.0),    # loc_0 position in cm from robot base
        1: (25.0, 0.0),    # loc_1
        2: (0.0,  20.0),   # loc_2
        3: (25.0, 20.0),   # loc_3
    }
)
```

Detects the 4 reference ArUco markers (IDs 0–3, loc_0–loc_3) from the workspace camera and computes a homography matrix H via `cv2.getPerspectiveTransform`. H is cached in `_H_CACHE` for use by `ik_pick_and_place`.

> **Required before IK:** Print markers 0–3 (`aruco_generator.py`), place them at known positions on the workspace mat, measure each position from the robot base in cm, then pass those measurements as `marker_world_coords`.

---

### ik_pick_and_place

Full IK pipeline (~18–20 seconds):

```python
from robotic_arm.tools import ik_pick_and_place

# calibrate_homography() must have been called first (or pass H explicitly)
result = ik_pick_and_place(orch, object_id=4, target="bin")
result = ik_pick_and_place(orch, object_id=5, target="zone_a", H=my_H)

# Returns:
# {"success": True,  "object_id": 4, "placed_at": "bin"}
# {"success": False, "error": "..."} on failure
```

| Step | Action |
|------|--------|
| 1 | `get_objects()` — detect object pixel position via ArUco |
| 2 | Apply homography H → world coordinates (cm from robot base) |
| 3 | Solve IK (`_solve_ik`) — law of cosines for base, shoulder, elbow |
| 4 | Open gripper |
| 5 | Move to approach pose (above object) |
| 6 | Lower to grasp height |
| 7 | Close gripper |
| 8 | Lift |
| 9 | Solve IK for drop target |
| 10 | Move above target |
| 11 | Lower to place height |
| 12 | Release gripper |
| 13 | Return to neutral |

**Built-in drop targets** (world-frame cm, update in `tools.py`):

| Target | x cm | y cm |
|--------|------|------|
| `bin` | 20.0 | 5.0 |
| `zone_a` | 10.0 | 10.0 |
| `zone_b` | 15.0 | 10.0 |

#### IK calibration (required)

Set these env vars from physical calibration data (`robotic_arm/calibrate.py`):

| Env var | Default | Meaning |
|---------|---------|---------|
| `L1_CM` | 15.0 | Upper-arm link length (shoulder → elbow, cm) |
| `L2_CM` | 12.0 | Forearm link length (elbow → wrist, cm) |
| `BASE_DEG_RANGE` | 180.0 | Total degree sweep of base joint |
| `SHOULDER_DEG_RANGE` | 180.0 | Total degree sweep of shoulder |
| `ELBOW_DEG_RANGE` | 180.0 | Total degree sweep of elbow |
| `BASE_ZERO_OFFSET_N` | 0.0 | Normalized offset at 0° for base |
| `SHOULDER_ZERO_OFFSET_N` | 0.0 | Normalized offset at 0° for shoulder |
| `ELBOW_ZERO_OFFSET_N` | 0.0 | Normalized offset at 0° for elbow |

Motion height constants (normalized, tune per arm):

| Constant | Default | Meaning |
|----------|---------|---------|
| `APPROACH_HEIGHT_N` | 0.4 | Shoulder height hovering above object |
| `GRASP_HEIGHT_N` | -0.2 | Shoulder lowered to table |
| `LIFT_HEIGHT_N` | 0.5 | Shoulder after grasping |

---

## visualizer.py

Tkinter GUI for directly controlling raw pulse widths per channel. Useful for testing servo ranges without writing code.

```bash
python -m robotic_arm.visualizer                                   # simulation only
python -m robotic_arm.visualizer --hardware                        # drive real servos via I2C
python -m robotic_arm.visualizer --hardware --i2c-address 0x41    # non-default address
```

Sliders show channels 0–5 (Base, Shoulder 1, Wrist Tilt, Shoulder 2, Wrist Rotate, Elbow) with range 400–2700 µs. Each slider move immediately sends the pulse to the PCA9685 when `--hardware` is supplied. **Reset to Start** returns all sliders to their startup defaults.

---

## calibrate.py

Interactive REPL for mapping pulse widths to real angles on the physical arm. Output is used to set the IK calibration env vars. Saves data to `robotic_arm/calibration.json`.

```bash
python -m robotic_arm.calibrate                      # default I2C address 0x40
python -m robotic_arm.calibrate --i2c-address 0x41
```

| Command | Description |
|---------|-------------|
| `<ch> <pulse>` | Send pulse (µs) to channel (e.g. `3 1500`) |
| `center` | All 16 channels → 1500 µs |
| `log <ch> <pulse> <angle>` | Record a (pulse, angle) calibration point |
| `show` | Print all recorded calibration points |
| `save` | Write points to `robotic_arm/calibration.json` |
| `quit` / `q` | Exit |

Pulse range accepted: 400–2700 µs.

---

## computer_vision/

### aruco_detection.py

Detects ArUco markers from a live camera or static image. Requires `opencv-contrib-python`.

**Known markers:**

| ID | Name | Role |
|----|------|------|
| 0 | loc_0 | Homography reference |
| 1 | loc_1 | Homography reference |
| 2 | loc_2 | Homography reference |
| 3 | loc_3 | Homography reference |
| 4 | cube_1 | Pick target |
| 5 | cube_2 | Pick target |
| 6 | bin_1 | Pick target |

```bash
python robotic_arm/computer_vision/aruco_detection.py --mode camera
python robotic_arm/computer_vision/aruco_detection.py --mode camera --dictionary DICT_4X4_50
python robotic_arm/computer_vision/aruco_detection.py --mode image --image snap.jpg
```

Returns `{marker_id: (cx_pixel, cy_pixel)}` from `draw_markers()`.

### aruco_generator.py

Generates ArUco marker images (DICT_4X4_50, 600 px) for printing. Creates IDs 0–3 (location/reference) and 4–6 (object markers). Output: `aruco_markers/marker_{name}_id{id}.png`.

### workspace_detector.py

YOLO instance-segmentation detector for workspace objects. Reports class, confidence, bounding box, and centroid per object.

```bash
python robotic_arm/computer_vision/workspace_detector.py --mode camera
python robotic_arm/computer_vision/workspace_detector.py --mode camera --model yolov8s-seg.pt
python robotic_arm/computer_vision/workspace_detector.py --mode camera --classes cup bottle
python robotic_arm/computer_vision/workspace_detector.py --mode camera --conf 0.4 --json
python robotic_arm/computer_vision/workspace_detector.py --mode image --image snap.jpg
```

| Flag | Default | Description |
|------|---------|-------------|
| `--model` | `yolov8n-seg.pt` | YOLO segmentation weights (auto-downloaded) |
| `--classes` | all | Filter to specific class names |
| `--conf` | 0.35 | Minimum detection confidence |
| `--json` | off | Print detections as JSON each frame |
| `--camera-index` | 0 | OpenCV camera index |

Detection output per object:
```json
{"class": "cup", "confidence": 0.87, "bbox": [x1, y1, x2, y2], "centroid": [cx, cy]}
```

---

## arduino/ (legacy)

`arduino/servo_bridge/servo_bridge.ino` is kept for reference but is no longer used. The Jetson talks to the PCA9685 directly over I2C via `i2c_bridge.py`.
