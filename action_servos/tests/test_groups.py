"""Pure logic tests (no I2C hardware)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from action_servos.config import JointSpec, ServoLayout
from action_servos.groups import (
    ArmController,
    EarController,
    HeadController,
    clamp_pulse,
    normalized_to_us,
    presets_head_pose,
    us_to_normalized,
)
from action_servos.sequences import Pose, Sequence, _ramp_to_pose


def _mock_pca() -> Any:
    return MagicMock()


def _layout() -> ServoLayout:
    return ServoLayout.default_layout()


def _arm(pca: Any | None = None) -> ArmController:
    if pca is None:
        pca = _mock_pca()
    return ArmController(pca, _layout())


def _ear(pca: Any | None = None) -> EarController:
    if pca is None:
        pca = _mock_pca()
    return EarController(pca, JointSpec(3))


# ---------------------------------------------------------------------------
# Pulse helpers
# ---------------------------------------------------------------------------

def test_clamp_pulse() -> None:
    s = JointSpec(0, min_us=1000.0, max_us=2000.0)
    assert clamp_pulse(s, 500)  == 1000.0
    assert clamp_pulse(s, 2500) == 2000.0
    assert clamp_pulse(s, 1500) == 1500.0


def test_normalized_round_trip() -> None:
    s = JointSpec(0, min_us=1000.0, max_us=2000.0)
    assert normalized_to_us(s, -1.0) == 1000.0
    assert normalized_to_us(s, 1.0)  == 2000.0
    assert normalized_to_us(s, 0.0)  == 1500.0
    assert us_to_normalized(s, 1250.0) == pytest.approx(-0.5, abs=1e-6)


def test_us_to_normalized_none() -> None:
    assert us_to_normalized(JointSpec(0), None) == 0.0


def test_joint_spec_channel_bounds() -> None:
    with pytest.raises(ValueError):
        JointSpec(16)
    with pytest.raises(ValueError):
        JointSpec(0, min_us=2000, max_us=1000)


# ---------------------------------------------------------------------------
# ServoLayout
# ---------------------------------------------------------------------------

def test_servo_layout_default() -> None:
    L = ServoLayout.default_layout()
    assert L.base.channel       == 0
    assert L.shoulder_a.channel   == 1
    assert L.shoulder_a.min_us    == 1500.0
    assert L.shoulder_a.max_us    == 2300.0
    assert L.shoulder_a.center_us == 1900.0
    assert L.shoulder_b.channel   == 3
    assert L.wrist_tilt is not None
    assert L.wrist_tilt.channel == 4
    assert L.elbow.channel      == 7
    assert L.head_pan  is not None
    assert L.head_pan.channel   == 5
    assert L.head_pan.min_us    == 1000.0
    assert L.head_pan.max_us    == 2500.0
    assert L.head_pan.center_us == 1700.0
    assert L.head_tilt is not None
    assert L.head_tilt.channel   == 6
    assert L.head_tilt.min_us    == 1200.0
    assert L.head_tilt.max_us    == 2500.0
    assert L.head_tilt.center_us == 1700.0
    assert L.ear is None


def test_presets_head_pose() -> None:
    assert presets_head_pose("neutral") is not None
    assert presets_head_pose("unknown_pose_xyz") is None


# ---------------------------------------------------------------------------
# ArmController
# ---------------------------------------------------------------------------

def test_arm_set_all_writes_channels() -> None:
    pca = _mock_pca()
    arm = _arm(pca)
    L = _layout()
    arm.set_all(1500, 1500, 1500)
    channels_written = {call.args[0] for call in pca.set_channel_pulse_us.call_args_list}
    assert channels_written == {L.base.channel, L.shoulder_a.channel, L.shoulder_b.channel, L.elbow.channel}


def test_arm_release_calls_full_off() -> None:
    pca = _mock_pca()
    arm = _arm(pca)
    L = _layout()
    arm.release()
    assert arm._released is True
    channels_off = {call.args[0] for call in pca.set_channel_full_off.call_args_list}
    expected = {L.base.channel, L.shoulder_a.channel, L.shoulder_b.channel, L.elbow.channel, L.wrist_tilt.channel}
    assert channels_off == expected


def test_arm_resume_restores_last_state() -> None:
    pca = _mock_pca()
    arm = _arm(pca)
    arm.set_all(1600, 1700, 1400)
    arm.release()
    arm.resume()
    assert arm._released is False
    s = arm.last_state
    assert s.base     == pytest.approx(1600.0)
    assert s.shoulder == pytest.approx(1700.0)
    assert s.elbow    == pytest.approx(1400.0)


def test_arm_resume_defaults_to_centre() -> None:
    pca = _mock_pca()
    arm = _arm(pca)
    L = _layout()
    arm.resume()   # no prior set_all — should use centre_us
    s = arm.last_state
    assert s.base     == pytest.approx(L.base.center_us)
    assert s.shoulder == pytest.approx(L.shoulder_a.center_us)
    assert s.elbow    == pytest.approx(L.elbow.center_us)


# ---------------------------------------------------------------------------
# EarController
# ---------------------------------------------------------------------------

def test_ear_release_and_resume() -> None:
    pca = _mock_pca()
    ear = _ear(pca)
    ear.set_pulse(1700.0)
    ear.release()
    assert ear._released is True
    pca.set_channel_full_off.assert_called_once_with(3)
    ear.resume()
    assert ear._released is False
    assert ear.last_pulse == 1700.0


def test_ear_resume_defaults_to_centre() -> None:
    pca = _mock_pca()
    ear = _ear(pca)
    ear.resume()
    assert ear.last_pulse == ear.spec.center_us


# ---------------------------------------------------------------------------
# Pose / Sequence
# ---------------------------------------------------------------------------

def test_pose_from_normalized() -> None:
    orch = MagicMock()
    orch.layout = _layout()
    pose = Pose.from_normalized(orch, shoulder=0.0, elbow=0.0)
    assert pose.shoulder == pytest.approx(1900.0)  # center_us of shoulder_a
    assert pose.elbow    == pytest.approx(1500.0)
    assert pose.base is None


def test_sequence_play_visits_poses_in_order() -> None:
    """Verify play() ramps to each keyframe in order."""
    visited: list[tuple] = []

    pca = _mock_pca()
    arm = _arm(pca)

    original_set_all = arm.set_all

    def recording_set_all(*args: float) -> None:
        original_set_all(*args)
        visited.append(args)

    arm.set_all = recording_set_all  # type: ignore[method-assign]

    orch = MagicMock()
    orch.layout = _layout()
    orch.arm = arm
    orch._head_ctl = None
    orch._ear_ctl  = None

    target1 = (1500, 1800, 1200)
    target2 = (1500, 1500, 1500)

    seq = (
        Sequence()
        .add(Pose(shoulder=float(target1[1]), elbow=float(target1[2])),
             duration_s=0.01, steps=2)
        .add(Pose(shoulder=float(target2[1]), elbow=float(target2[2])),
             duration_s=0.01, steps=2)
    )
    seq.play(orch)

    # The last visited tuple should be close to target2
    assert visited[-1][1] == pytest.approx(1500.0, abs=1.0)
    assert visited[-1][2] == pytest.approx(1500.0, abs=1.0)
    # First ramp's final step should be close to target1
    assert visited[1][1] == pytest.approx(1800.0, abs=1.0)
    assert visited[1][2] == pytest.approx(1200.0, abs=1.0)
