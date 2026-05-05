# Robotic Arm Module

Python control layer for a 6-DOF serial arm driven by an Arduino + PCA9685 servo driver. Includes forward kinematics, inverse kinematics, and an interactive visualizer.

---

## Setup

```bash
pip install -r robotic_arm/requirements.txt
```

Run the visualizer from the repo root:

```bash
python -m robotic_arm.visualizer                   # simulation only
python -m robotic_arm.visualizer --port COM3        # + drive real hardware
python -m robotic_arm.visualizer --port /dev/ttyUSB0
```

---

## Hardware

| Component | Details |
|-----------|---------|
| Microcontroller | Arduino (any model with USB serial) |
| Servo driver | PCA9685 16-channel PWM board (I2C) |
| Servos | Standard hobby servos, 500–2500 µs pulse range |
| Connection | USB serial, 115200 baud |

The Arduino must implement the serial protocol described below and send `READY` after `setup()` completes.

---

## Channel Map

| Channel | Joint | Range |
|---------|-------|-------|
| 0 | Base | 0° – 180° |
| 1 | Shoulder 1 | (limits TBD) |
| 2 | Wrist tilt | (limits TBD) |
| 3 | Shoulder 2 | 0° – 130° |
| 4 | Wrist rotate | (limits TBD) |
| 5 | Elbow | 130° – 180° |

Limits are enforced in `config.py` via `JointSpec.from_degrees()` and clamped on every command before it reaches the Arduino.

---

## Serial Protocol

All messages are newline-terminated ASCII.

| Direction | Command | Description |
|-----------|---------|-------------|
| Python → Arduino | `SET <ch> <pulse_us>` | Move channel `ch` to `pulse_us` microseconds |
| Python → Arduino | `CENTER` | Move all 16 channels to 1500 µs |
| Python → Arduino | `PING` | Health check |
| Arduino → Python | `OK` | Command succeeded |
| Arduino → Python | `ERR <message>` | Command failed |
| Arduino → Python | `PONG` | Response to PING |
| Arduino → Python | `READY` | Sent once after `setup()` on power-up / reset |

---

## File Reference

```
robotic_arm/
├── config.py        # Joint specs, channel map, degree ↔ µs conversion
├── serial_bridge.py # Low-level serial comms to Arduino
├── controller.py    # High-level arm control (named joints, smooth ramps)
├── tools.py         # LLM tool API (pick, place, stack, clean_table)
├── visualizer.py    # Interactive FK/IK GUI
└── requirements.txt
```

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

Useful methods:

```python
spec = JointSpec.from_degrees(channel=5, min_deg=130, max_deg=180, name="elbow")

spec.clamp(pulse_us)            # clamp raw µs to [min_us, max_us]
spec.to_us(normalized)          # map normalized [-1, 1] → [min_us, max_us]
spec.to_normalized(pulse_us)    # inverse of to_us
```

**`ArmLayout`** — the full joint map. Instantiate with defaults or override individual joints:

```python
from robotic_arm.config import ArmLayout, JointSpec

layout = ArmLayout()            # default channel map + limits
layout.elbow                    # JointSpec for channel 5
layout.all_joints               # dict of name → JointSpec
layout.by_channel(3)            # look up JointSpec by channel number
```

---

## serial_bridge.py

Handles the raw serial connection. Use as a context manager:

```python
from robotic_arm.serial_bridge import SerialBridge

with SerialBridge("/dev/ttyUSB0", baud=115200) as bridge:
    bridge.ping()                        # True if Arduino responds
    bridge.set_channel(0, 1500)          # move channel 0 to 1500 µs
    bridge.center_all()                  # broadcast CENTER command
```

`SerialBridge.open()` blocks until the Arduino sends its `READY` banner (up to 5 s).

---

## controller.py

Named-joint interface on top of `SerialBridge`.

```python
from robotic_arm.config import ArmLayout
from robotic_arm.serial_bridge import SerialBridge
from robotic_arm.controller import RoboticArmController

with SerialBridge("COM3") as bridge:
    arm = RoboticArmController(bridge, layout=ArmLayout())

    arm.set_joint("base", 0.0)           # normalized: 0.0 = center (90°)
    arm.set_joint("shoulder2", -1.0)     # normalized: -1.0 = min (0°)
    arm.set_joint_us("elbow", 2200)      # raw µs

    arm.center()                         # all joints to center_us
    arm.center_all_channels()            # broadcast CENTER (all 16 ch to 1500 µs)

    # Smooth multi-joint motion (smoothstep interpolation)
    arm.move_ramp(
        target={"base": 0.5, "shoulder1": 0.3, "shoulder2": 0.3, "elbow": 0.8},
        duration_s=0.8,
        steps=30,
        normalized=True,
    )
```

`move_ramp` interpolates all listed joints simultaneously using a smoothstep curve — no abrupt starts or stops.

---

## tools.py

High-level LLM tool API. The perception system calls `update_env()` to push object positions; the LLM planner calls the action functions.

```python
from robotic_arm.tools import update_env, get_objects, pick, place, pick_and_place, clean_table, stack

# Feed in a fresh camera snapshot (ArUco marker positions, world-frame 0–1)
update_env([
    {"id": 10, "x": 0.2, "y": 0.5},
    {"id": 11, "x": 0.7, "y": 0.3},
])

get_objects()                              # returns current object list
pick(arm, object_id=10)                   # pick object 10
place(arm, object_id=10, target="bin")    # place at a named drop zone
pick_and_place(arm, 11, "zone_a")         # pick + place in one call
clean_table(arm)                          # move all visible objects to bin
stack(arm, [10, 11], stack_zone="zone_a") # stack objects at a zone
```

Built-in drop zones: `bin` (x=0.9), `zone_a` (x=0.2), `zone_b` (x=0.5). Pass a custom `drop_zones` dict to override.

> **Note:** The motion primitives use placeholder joint angles. Calibrate the `move_ramp` targets in `pick()` and `place()` against your actual arm geometry and link lengths before use.

---

## visualizer.py

Interactive GUI for testing FK and IK without needing hardware.

**Forward Kinematics panel**

Move the three sliders to pose the arm. The 3-D view updates in real time.

| Slider | Range |
|--------|-------|
| Base | 0° – 180° |
| Shoulder | 0° – 130° |
| Elbow | 130° – 180° |

**Inverse Kinematics panel**

Enter a target position (X, Y, Z in cm) and press **Solve IK**. The solver finds joint angles that reach that point and snaps the sliders to the solution. A yellow × marks the target in the 3-D view; a dashed line appears if the target is unreachable.

**Arm Geometry fields**

Set L1 (shoulder → elbow) and L2 (elbow → tip) in cm to match your physical arm. Press Enter to recompute.

**Hardware mode**

When `--port` is supplied, every slider move and every IK solve immediately writes pulse widths to channels 0, 3, and 5 over serial.

---

## Kinematics Reference

### Forward Kinematics

```
shoulder_angle s  — elevation above horizontal (0° = arm horizontal)
elbow_angle e     — interior angle at elbow (180° = fully extended)

upper_arm_angle   = s
forearm_angle     = s + e − 180°

elbow_pos = shoulder + L1 · [cos(s)·cos(b), cos(s)·sin(b), sin(s)]
tip_pos   = elbow   + L2 · [cos(f)·cos(b), cos(f)·sin(b), sin(f)]
```

where `b` is the base angle and `f` is the forearm angle.

### Inverse Kinematics

```
base_deg     = atan2(y, x)
r            = sqrt(x² + y²)          horizontal reach
d            = sqrt(r² + z²)          straight-line distance to target

elbow_deg    = acos((L1² + L2² − d²) / (2·L1·L2))    law of cosines
shoulder_deg = atan2(z, r) + acos((L1² + d² − L2²) / (2·L1·d))
```

Returns `None` if `d > L1 + L2` (out of reach) or if the solution violates any joint limit.
