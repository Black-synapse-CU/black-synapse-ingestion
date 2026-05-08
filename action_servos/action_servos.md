# action_servos

PCA9685 servo control for the AtlasAI robot over I2C (Jetson). Drives the 6-DOF arm and head pan/tilt.

---

## Channel Map

| Channel | Joint | Range |
|---------|-------|-------|
| 0 | base | 900–2100 µs, center 1500 µs |
| 1 | shoulder_a | 900–2100 µs, center 1500 µs |
| 3 | shoulder_b | 900–2100 µs, center 1500 µs (mirrors shoulder_a) |
| 4 | wrist_tilt | 900–2100 µs, center 1500 µs |
| 5 | head_pan | 1000–2500 µs, center 1700 µs |
| 6 | head_tilt | 1200–2500 µs, center 1700 µs |
| 7 | elbow | 900–2100 µs, center 1500 µs |

**shoulder_b** mirrors shoulder_a automatically — never command it directly.

---

## Hardware

| Component | Details |
|-----------|---------|
| Host | Jetson (I2C bus 7 by default) |
| Servo driver | PCA9685, I2C address 0x40 |
| PWM frequency | 50 Hz |

Confirm the I2C bus with `i2cdetect -l` and verify the PCA9685 is visible at 0x40 with `sudo i2cdetect -y 7`.

---

## Normalized Values

All joints accept normalized values in `[-1, 1]`:

| Value | Meaning |
|-------|---------|
| `-1.0` | min_us (e.g. full right for pan, full up for tilt) |
| `0.0` | center_us |
| `+1.0` | max_us (e.g. full left for pan, full down for tilt) |

---

## CLI

Run from the repo root. All commands accept `--bus`, `--address`, `--hz` overrides.

```bash
# Center all joints
python -m action_servos center

# Move arm joints (normalized or raw µs)
python -m action_servos arm --base 0.5
python -m action_servos arm --shoulder 0.3 --elbow -0.2
python -m action_servos arm --elbow-us 1800

# Move wrist
python -m action_servos arm --wrist-tilt 0.5

# Move head
python -m action_servos head --pan -1.0       # full right
python -m action_servos head --tilt -1.0      # look up
python -m action_servos head --pan 0.0 --tilt 0.0  # center

# Run a named sequence
python -m action_servos sequence wave
python -m action_servos sequence extend --amount 75

# Release (servos go limp), resume, reset
python -m action_servos release
python -m action_servos resume
python -m action_servos reset --arm
```

---

## Hardware Test

Slow ramp test to verify wiring without sudden jumps:

```bash
python -m action_servos.slow_test
python -m action_servos.slow_test --duration 5 --steps 50
```

---

## LLM HTTP Endpoints

The worker FastAPI app exposes two endpoints for LLM tool use via n8n `toolHttpRequest` nodes.

### `POST /head/turn`

Move the head to a pan/tilt position.

**Request**
```json
{
  "pan":        0.5,
  "tilt":       -0.3,
  "duration_s": 0.6
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `pan` | float [-1, 1] | `0.0` | -1 = full right, 1 = full left |
| `tilt` | float [-1, 1] | `0.0` | -1 = full up, 1 = full down |
| `duration_s` | float | `0.6` | Seconds to reach target |

**Response**
```json
{ "success": true, "result": "Head moved to pan=+0.50 tilt=-0.30." }
```

---

### `POST /arm/move`

Move one or more arm joints. Any joint omitted stays at its current position.

**Request**
```json
{
  "base":       0.0,
  "shoulder":   0.4,
  "elbow":      0.7,
  "wrist_tilt": -0.2,
  "duration_s": 0.8
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `base` | float [-1, 1] | stay | Base rotation |
| `shoulder` | float [-1, 1] | stay | Shoulder lift |
| `elbow` | float [-1, 1] | stay | Elbow flex |
| `wrist_tilt` | float [-1, 1] | stay | Wrist tilt |
| `duration_s` | float | `0.8` | Seconds to reach target |

**Response**
```json
{ "success": true, "result": "Arm moved: shoulder=+0.40, elbow=+0.70." }
```

---

## Wiring to the LLM (n8n)

Each endpoint maps to a `toolHttpRequest` node in the n8n agent workflow. The LLM receives a JSON tool schema and calls the endpoint when appropriate.

**`turn_head` tool schema (paste into n8n)**
```json
{
  "name": "turn_head",
  "description": "Move the robot's head. pan controls left/right, tilt controls up/down.",
  "parameters": {
    "type": "object",
    "properties": {
      "pan":        { "type": "number", "description": "Left/right -1..1 (-1=right, 1=left)" },
      "tilt":       { "type": "number", "description": "Up/down -1..1 (-1=up, 1=down)" },
      "duration_s": { "type": "number", "description": "Seconds to move (default 0.6)" }
    }
  }
}
```

**`move_arm` tool schema (paste into n8n)**
```json
{
  "name": "move_arm",
  "description": "Move one or more arm joints. Omit any joint to leave it in place. Always include at least one joint. base is arm rotation, NOT head pan.",
  "parameters": {
    "type": "object",
    "properties": {
      "base":       { "type": "number", "description": "Arm base rotation -1..1 (NOT head pan)" },
      "shoulder":   { "type": "number", "description": "Arm shoulder: -1=up, 1=down" },
      "elbow":      { "type": "number", "description": "Arm elbow: -1=up, 1=down" },
      "wrist_tilt": { "type": "number", "description": "Arm wrist tilt: -1=up, 1=down" },
      "duration_s": { "type": "number", "description": "Seconds to move (default 0.8)" }
    }
  }
}
```

**Flow**
```
User speaks → ASR → n8n agent → LLM decides to call turn_head or move_arm
  → toolHttpRequest → POST /head/turn or /arm/move
  → worker executes on hardware → returns result string to LLM
  → LLM incorporates result into its response
```

---

## File Reference

```
action_servos/
├── config.py       — JointSpec, ServoLayout, channel map, defaults
├── hardware.py     — PCA9685 I2C driver
├── groups.py       — ArmController, HeadController, EarController, ServoOrchestrator
├── sequences.py    — Pose, Keyframe, Sequence (keyframe playback)
├── cli.py          — CLI entry point (python -m action_servos)
├── slow_test.py    — Hardware wiring verification
└── tests/
    └── test_groups.py
```
