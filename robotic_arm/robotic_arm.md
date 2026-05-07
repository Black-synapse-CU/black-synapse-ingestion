# Robotic Arm Module

Python control layer for a 6-DOF serial arm driven by an Arduino + PCA9685 servo driver. Includes servo calibration, an interactive pulse visualizer, LLM tool API, and a computer vision subsystem (ArUco + YOLO).

---

## Directory Structure

```
robotic_arm/
├── config.py                        # JointSpec + ArmLayout dataclasses
├── i2c_bridge.py                    # Direct I2C comms to PCA9685 (I2CBridge)
├── controller.py                    # Named-joint interface + smooth ramps
├── tools.py                         # LLM tool API (pick, place, stack, etc.)
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

The Jetson talks directly to the PCA9685 over I2C — no Arduino required. Wire SDA → pin 3, SCL → pin 5, GND, and 3.3 V (or 5 V for the PCA9685 VCC; servo power is separate).

---

## Channel Map

| Channel | Joint | Range |
|---------|-------|-------|
| 0 | base | 0° – 180° (center_deg=9°) |
| 1 | shoulder1 | 500–2500 µs |
| 2 | wrist_tilt | 500–2500 µs |
| 3 | shoulder2 | 900–1900 µs (center=1400 µs) |
| 4 | wrist_rotate | 500–2500 µs |
| 5 | elbow | 130° – 180° (center=180°) |

Limits are enforced in `config.py` via `JointSpec` and clamped on every command before it reaches the Arduino.

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

Defines two dataclasses.

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
    arm.center_all_channels()           # broadcast CENTER (all 16 ch → 1500 µs)

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

`move_ramp` interpolates all listed joints simultaneously using a smoothstep curve (`t² (3 − 2t)`). Start positions are taken from internal state (last sent µs), falling back to `center_us` if the joint hasn't been driven yet.

---

## tools.py

High-level LLM tool API. The perception system calls `update_env()` to push object positions; the LLM planner calls the action functions.

```python
from robotic_arm.tools import update_env, get_objects, pick, place, pick_and_place, clean_table, stack

# Feed in a fresh camera snapshot (object positions, world-frame 0–1)
update_env([
    {"id": 10, "x": 0.2, "y": 0.5},
    {"id": 11, "x": 0.7, "y": 0.3},
])

get_objects()                               # returns current object list
pick(arm, object_id=10)                    # pick object 10
place(arm, object_id=10, target="bin")     # place at a named drop zone
pick_and_place(arm, 11, "zone_a")          # pick + place in one call
clean_table(arm)                            # move all visible objects to bin
stack(arm, [10, 11], stack_zone="zone_a")  # stack objects at a zone
```

Built-in drop zones (world-frame x):

| Zone | x |
|------|---|
| `bin` | 0.9 |
| `zone_a` | 0.2 |
| `zone_b` | 0.5 |

Pass a custom `drop_zones` dict (`{name: {"x": float, "y": float}}`) to override.

World-frame x [0, 1] maps to base rotation normalized [-1, 1] via `(x − 0.5) × 2`.

> **Note:** Motion primitives use placeholder joint angles. Calibrate `move_ramp` targets in `pick()` and `place()` against your actual arm geometry before use.

---

## visualizer.py

Tkinter GUI for directly controlling raw pulse widths per channel. Useful for testing servo ranges without writing code.

```bash
python -m robotic_arm.visualizer                           # simulation only (no hardware)
python -m robotic_arm.visualizer --hardware                # drive real servos via I2C
python -m robotic_arm.visualizer --hardware --i2c-address 0x41  # non-default address
```

Sliders show channels 0–5 (Base, Shoulder 1, Wrist Tilt, Shoulder 2, Wrist Rotate, Elbow) with range 400–2700 µs. Each slider move immediately sends `SET <ch> <pulse>` to the Arduino when `--port` is supplied. **Reset to Start** returns all sliders to their startup defaults.

---

## calibrate.py

Interactive REPL for mapping pulse widths to real angles on the physical arm. Saves data to `robotic_arm/calibration.json`.

```bash
python -m robotic_arm.calibrate                        # default 0x40
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

```bash
# Live camera
python robotic_arm/computer_vision/aruco_detection.py --mode camera
python robotic_arm/computer_vision/aruco_detection.py --mode camera --dictionary DICT_4X4_50

# Static image
python robotic_arm/computer_vision/aruco_detection.py --mode image --image snap.jpg
```

Tries dictionaries in order (4×4 → 7×7) when `--dictionary auto` (default). Draws marker corners and IDs on the frame.

### aruco_generator.py

Generates ArUco marker images for printing.

### workspace_detector.py

YOLO instance-segmentation detector for workspace objects. Reports class, confidence, bounding box, and centroid per object.

```bash
# Live camera (default model: yolov8n-seg.pt, auto-downloaded)
python robotic_arm/computer_vision/workspace_detector.py --mode camera
python robotic_arm/computer_vision/workspace_detector.py --mode camera --model yolov8s-seg.pt
python robotic_arm/computer_vision/workspace_detector.py --mode camera --classes cup bottle
python robotic_arm/computer_vision/workspace_detector.py --mode camera --conf 0.4 --json

# Static image
python robotic_arm/computer_vision/workspace_detector.py --mode image --image snap.jpg
```

| Flag | Default | Description |
|------|---------|-------------|
| `--model` | `yolov8n-seg.pt` | YOLO segmentation weights |
| `--classes` | all | Filter to specific class names |
| `--conf` | 0.35 | Minimum detection confidence |
| `--json` | off | Print detections as JSON each frame |
| `--camera-index` | 0 | OpenCV camera index |

Detection output per object:
```json
{"class": "cup", "confidence": 0.87, "bbox": [x1, y1, x2, y2], "centroid": [cx, cy]}
```

The centroid pixel coordinates feed into `tools.update_env()` after coordinate normalization (pixel → world-frame 0–1).

---

## arduino/ (legacy)

`arduino/servo_bridge/servo_bridge.ino` is kept for reference but is no longer used. The Jetson talks to the PCA9685 directly over I2C via `i2c_bridge.py`.
