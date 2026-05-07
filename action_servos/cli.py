"""Manual jog on hardware: run from repo root as `python -m action_servos`."""

from __future__ import annotations

import argparse
import logging
import sys

from action_servos.config import (
    DEFAULT_I2C_BUS,
    DEFAULT_PCA9685_ADDRESS,
    DEFAULT_PWM_FREQUENCY_HZ,
)
from action_servos.groups import ServoOrchestrator, us_to_normalized
from worker.app.arm_actions import execute_action

# All logical arm joints in command order
_ARM_JOINTS = ("base", "shoulder", "elbow", "wrist_pitch", "wrist_roll", "gripper")


def _add_joint_args(p: argparse.ArgumentParser) -> None:
    """Add --<joint> (normalised) and --<joint>-us flags for all 6 arm joints."""
    for joint in _ARM_JOINTS:
        p.add_argument(f"--{joint}", type=float, default=None,
                       metavar="N", help=f"{joint} normalised -1..1")
        p.add_argument(f"--{joint.replace('_', '-')}-us", type=float, default=None,
                       dest=f"{joint}_us", metavar="µs",
                       help=f"{joint} pulse microseconds")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Jog 6-DOF PCA9685 arm servos")
    parser.add_argument("--bus",     type=int,            default=DEFAULT_I2C_BUS)
    parser.add_argument("--address", type=lambda x: int(x, 0), default=DEFAULT_PCA9685_ADDRESS,
                        help="PCA9685 I2C address (e.g. 0x40)")
    parser.add_argument("--hz",      type=float,          default=DEFAULT_PWM_FREQUENCY_HZ,
                        help="PWM frequency Hz (default 50)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # center
    sub.add_parser("center", help="Move all joints to centre_us")

    # arm — set any combination of joints
    p_arm = sub.add_parser("arm", help="Set one or more arm joints")
    _add_joint_args(p_arm)

    # sequence
    p_seq = sub.add_parser("sequence",
                            help="Run a named sequence: rest, neutral, extend, retract, "
                                 "point, wave, grab, release_grip, release")
    p_seq.add_argument("name",   type=str, help="sequence name")
    p_seq.add_argument("--amount", type=int, default=None,
                       help="percent for extend/retract (0-100)")

    # release / resume / reset with optional per-group flags
    for cmd, help_text in (
        ("release", "Cut PWM to joints (servos go limp)"),
        ("resume",  "Re-engage torque on joints (restores last position)"),
        ("reset",   "Wake chip + clear FULL_OFF, re-send last position"),
    ):
        p = sub.add_parser(cmd, help=help_text)
        p.add_argument("--arm",  action="store_true", default=False)
        p.add_argument("--head", action="store_true", default=False)
        p.add_argument("--ear",  action="store_true", default=False)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    try:
        orch = ServoOrchestrator()
        orch.open(args.bus, args.address, args.hz)
    except OSError as e:
        print(f"I2C open failed (bus {args.bus} addr 0x{args.address:02x}): {e}",
              file=sys.stderr)
        return 1

    try:
        if args.cmd == "center":
            orch.all_center()

        elif args.cmd == "arm":
            L = orch.layout
            arm = orch.arm
            s = arm.last_state

            # Build target for each joint: prefer µs arg, then normalised arg, then current state
            def _resolve(joint: str, spec_attr: str) -> float:
                spec = getattr(L, spec_attr)
                us_val   = getattr(args, f"{joint}_us",   None)
                norm_val = getattr(args, joint,            None)
                state_val = getattr(s, joint if joint != "shoulder" else "shoulder", None)
                if us_val is not None:
                    return float(us_val)
                if norm_val is not None:
                    from action_servos.groups import normalized_to_us
                    return normalized_to_us(spec, norm_val)
                return state_val if state_val is not None else spec.center_us

            any_set = any(
                getattr(args, j, None) is not None or getattr(args, f"{j}_us", None) is not None
                for j in _ARM_JOINTS
            )
            if not any_set:
                parser.error("arm: specify at least one joint flag (--shoulder, --elbow-us, …)")

            arm.set_all(
                _resolve("base",        "base"),
                _resolve("shoulder",    "shoulder_a"),
                _resolve("elbow",       "elbow"),
                _resolve("wrist_pitch", "wrist_pitch"),
                _resolve("wrist_roll",  "wrist_roll"),
                _resolve("gripper",     "gripper"),
            )

        elif args.cmd == "sequence":
            try:
                result = execute_action(orch, args.name, args.amount)
                print(result)
            except ValueError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1

        elif args.cmd in ("release", "resume", "reset"):
            all_groups = not (args.arm or args.head or args.ear)

            if args.cmd == "release":
                if args.arm  or all_groups: orch.arm.release()
                if args.head or all_groups:
                    if orch._head_ctl: orch._head_ctl.release()
                if args.ear  or all_groups:
                    if orch._ear_ctl:  orch._ear_ctl.release()

            elif args.cmd == "resume":
                if args.arm  or all_groups: orch.arm.resume()
                if args.head or all_groups:
                    if orch._head_ctl: orch._head_ctl.resume()
                if args.ear  or all_groups:
                    if orch._ear_ctl:  orch._ear_ctl.resume()

            else:  # reset
                if args.arm  or all_groups: orch.arm.reset()
                if args.head or all_groups:
                    if orch._head_ctl: orch._head_ctl.reset()
                if args.ear  or all_groups:
                    if orch._ear_ctl:  orch._ear_ctl.reset()

        return 0

    finally:
        orch.close()


if __name__ == "__main__":
    raise SystemExit(main())
