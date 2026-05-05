"""
Interactive FK/IK visualizer for the robotic arm.

Run from the repo root:
    python -m robotic_arm.visualizer
    python -m robotic_arm.visualizer --port COM3   # also drives real hardware

Dependencies:
    pip install matplotlib numpy pyserial
"""

from __future__ import annotations

import argparse
import math
import tkinter as tk
from tkinter import ttk
from typing import Optional

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 — registers 3-D projection

# ---------------------------------------------------------------------------
# Arm geometry defaults (cm) — edit to match your hardware
# ---------------------------------------------------------------------------
L1_DEFAULT = 15.0   # shoulder → elbow
L2_DEFAULT = 12.0   # elbow → tip

# Joint limits (degrees) — kept in sync with config.py
BASE_MIN,     BASE_MAX     = 0.0, 180.0
SHOULDER_MIN, SHOULDER_MAX = 0.0, 130.0
ELBOW_MIN,    ELBOW_MAX    = 130.0, 180.0

_BG     = "#1e1e1e"
_FG     = "#cccccc"
_ACCENT = "#3a7bd5"


# ---------------------------------------------------------------------------
# Kinematics
# ---------------------------------------------------------------------------

def fk(
    base_deg: float,
    shoulder_deg: float,
    elbow_deg: float,
    l1: float = L1_DEFAULT,
    l2: float = L2_DEFAULT,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Forward kinematics.

    shoulder_deg: elevation above horizontal (0° = arm horizontal, 90° = pointing straight up).
    elbow_deg: interior angle at elbow (180° = fully extended, 130° = slightly bent downward).

    Returns (shoulder_pos, elbow_pos, tip_pos), each a (3,) array in cm.
    Shoulder joint is at the origin.
    """
    b = math.radians(base_deg)
    s = math.radians(shoulder_deg)
    e = math.radians(elbow_deg)

    # Upper-arm vector in the sagittal plane, then rotated by base
    ux = l1 * math.cos(s)
    uz = l1 * math.sin(s)

    # Forearm angle from horizontal (interior elbow angle: 180° = straight)
    forearm_angle = s + e - math.pi
    fx = l2 * math.cos(forearm_angle)
    fz = l2 * math.sin(forearm_angle)

    shoulder = np.zeros(3)
    elbow    = np.array([ux * math.cos(b), ux * math.sin(b), uz])
    tip      = elbow + np.array([fx * math.cos(b), fx * math.sin(b), fz])
    return shoulder, elbow, tip


def ik(
    x: float,
    y: float,
    z: float,
    l1: float = L1_DEFAULT,
    l2: float = L2_DEFAULT,
) -> Optional[tuple[float, float, float]]:
    """
    Analytic IK for the 3-DOF arm (base + shoulder + elbow).
    Returns (base_deg, shoulder_deg, elbow_deg) or None if unreachable.
    Uses the elbow-up solution.
    """
    base_deg = math.degrees(math.atan2(y, x))
    base_deg = max(BASE_MIN, min(BASE_MAX, base_deg))

    r = math.hypot(x, y)          # horizontal reach
    d = math.hypot(r, z)          # straight-line distance shoulder → target

    if d < 1e-6 or d > l1 + l2 - 1e-6:
        return None

    # Elbow interior angle via law of cosines on the shoulder-elbow-tip triangle
    cos_e = (l1**2 + l2**2 - d**2) / (2.0 * l1 * l2)
    elbow_deg = math.degrees(math.acos(max(-1.0, min(1.0, cos_e))))

    # Shoulder elevation: elevation to target + angular offset from l1/l2
    phi = math.atan2(z, r)
    cos_delta = (l1**2 + d**2 - l2**2) / (2.0 * l1 * d)
    delta = math.acos(max(-1.0, min(1.0, cos_delta)))
    shoulder_deg = math.degrees(phi + delta)

    if not (SHOULDER_MIN <= shoulder_deg <= SHOULDER_MAX):
        return None
    if not (ELBOW_MIN <= elbow_deg <= ELBOW_MAX):
        return None

    return base_deg, shoulder_deg, elbow_deg


# ---------------------------------------------------------------------------
# Visualizer
# ---------------------------------------------------------------------------

class ArmVisualizer:
    def __init__(self, bridge=None) -> None:
        self._bridge = bridge
        self._ik_target: Optional[np.ndarray] = None

        self.root = tk.Tk()
        self.root.title("Robotic Arm Visualizer")
        self.root.configure(bg=_BG)

        self._build_ui()
        self._refresh()

    # ------------------------------------------------------------------
    # Layout

    def _build_ui(self) -> None:
        left = tk.Frame(self.root, bg=_BG, padx=14, pady=14, width=270)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)

        # Link lengths
        self._section(left, "Arm Geometry (cm)")
        self._l1_var = tk.DoubleVar(value=L1_DEFAULT)
        self._l2_var = tk.DoubleVar(value=L2_DEFAULT)
        self._number_row(left, "L1  shoulder → elbow", self._l1_var)
        self._number_row(left, "L2  elbow → tip      ", self._l2_var)

        self._divider(left)

        # FK sliders
        self._section(left, "Forward Kinematics")
        self._base_var     = tk.DoubleVar(value=9.0)
        self._shoulder_var = tk.DoubleVar(value=50.0)
        self._elbow_var    = tk.DoubleVar(value=180.0)
        self._slider(left, "Base",     self._base_var,     BASE_MIN,     BASE_MAX)
        self._slider(left, "Shoulder", self._shoulder_var, SHOULDER_MIN, SHOULDER_MAX)
        self._slider(left, "Elbow",    self._elbow_var,    ELBOW_MIN,    ELBOW_MAX)

        self._divider(left)

        # IK
        self._section(left, "Inverse Kinematics")
        tk.Label(left, text="Target position (cm)", fg="#777777",
                 bg=_BG, font=("Helvetica", 9)).pack(anchor="w")
        self._ik_x = self._coord_row(left, "X")
        self._ik_y = self._coord_row(left, "Y")
        self._ik_z = self._coord_row(left, "Z")

        tk.Button(
            left, text="Solve IK →", bg=_ACCENT, fg="white",
            font=("Helvetica", 11, "bold"), relief=tk.FLAT, padx=8, pady=6,
            activebackground="#2a5db0", activeforeground="white",
            command=self._solve_ik,
        ).pack(fill=tk.X, pady=(10, 0))

        self._status_var = tk.StringVar(value="")
        tk.Label(left, textvariable=self._status_var, fg="#ffcc00", bg=_BG,
                 font=("Helvetica", 9), wraplength=240, justify=tk.LEFT,
                 ).pack(anchor="w", pady=(6, 0))

        # 3-D canvas
        self._fig = plt.figure(figsize=(7, 6), facecolor=_BG)
        self._ax  = self._fig.add_subplot(111, projection="3d")
        canvas    = FigureCanvasTkAgg(self._fig, master=self.root)
        canvas.get_tk_widget().pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self._canvas = canvas

    # ------------------------------------------------------------------
    # Widget helpers

    def _section(self, parent, text: str) -> None:
        tk.Label(parent, text=text, fg="#ffffff", bg=_BG,
                 font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 6))

    def _divider(self, parent) -> None:
        ttk.Separator(parent, orient="horizontal").pack(fill=tk.X, pady=12)

    def _slider(self, parent, label: str, var: tk.DoubleVar,
                lo: float, hi: float) -> None:
        frame = tk.Frame(parent, bg=_BG)
        frame.pack(fill=tk.X, pady=4)

        row = tk.Frame(frame, bg=_BG)
        row.pack(fill=tk.X)
        tk.Label(row, text=f"{label}  ({lo:.0f}–{hi:.0f}°)",
                 fg=_FG, bg=_BG, font=("Helvetica", 9)).pack(side=tk.LEFT)
        val_lbl = tk.Label(row, text=f"{var.get():.1f}°", fg="#ffffff",
                           bg=_BG, font=("Helvetica", 9, "bold"), width=7, anchor="e")
        val_lbl.pack(side=tk.RIGHT)

        def on_move(v, vl=val_lbl):
            vl.config(text=f"{float(v):.1f}°")
            self._on_slider()

        tk.Scale(
            frame, variable=var, from_=lo, to=hi,
            orient=tk.HORIZONTAL, resolution=0.5, showvalue=False,
            bg=_BG, troughcolor="#3a3a3a", activebackground=_ACCENT,
            highlightthickness=0, command=on_move,
        ).pack(fill=tk.X)

    def _number_row(self, parent, label: str, var: tk.DoubleVar) -> None:
        frame = tk.Frame(parent, bg=_BG)
        frame.pack(fill=tk.X, pady=2)
        tk.Label(frame, text=label, fg=_FG, bg=_BG,
                 font=("Helvetica", 9), anchor="w").pack(side=tk.LEFT)
        entry = tk.Entry(frame, textvariable=var, bg="#2d2d2d", fg="#ffffff",
                         insertbackground="white", font=("Helvetica", 9),
                         width=6, relief=tk.FLAT)
        entry.pack(side=tk.RIGHT)
        entry.bind("<Return>",   lambda _: self._refresh())
        entry.bind("<FocusOut>", lambda _: self._refresh())

    def _coord_row(self, parent, label: str) -> tk.Entry:
        frame = tk.Frame(parent, bg=_BG)
        frame.pack(fill=tk.X, pady=2)
        tk.Label(frame, text=f"{label}:", fg=_FG, bg=_BG,
                 font=("Helvetica", 10), width=3, anchor="w").pack(side=tk.LEFT)
        entry = tk.Entry(frame, bg="#2d2d2d", fg="#ffffff",
                         insertbackground="white", font=("Helvetica", 10),
                         width=9, relief=tk.FLAT)
        entry.insert(0, "0.0")
        entry.pack(side=tk.LEFT, padx=4)
        entry.bind("<Return>", lambda _: self._solve_ik())
        return entry

    # ------------------------------------------------------------------
    # Callbacks

    def _on_slider(self) -> None:
        self._ik_target = None
        self._refresh()
        if self._bridge:
            self._send_to_hardware()

    def _solve_ik(self) -> None:
        try:
            x = float(self._ik_x.get())
            y = float(self._ik_y.get())
            z = float(self._ik_z.get())
        except ValueError:
            self._status_var.set("Enter numeric X Y Z values.")
            return

        l1 = self._l1_var.get()
        l2 = self._l2_var.get()
        self._ik_target = np.array([x, y, z])

        result = ik(x, y, z, l1, l2)
        if result is None:
            self._status_var.set(
                f"({x:.1f}, {y:.1f}, {z:.1f}) unreachable\nwith current joint limits."
            )
            self._refresh()
            return

        b, s, e = result
        self._base_var.set(round(b, 1))
        self._shoulder_var.set(round(s, 1))
        self._elbow_var.set(round(e, 1))
        self._status_var.set(
            f"IK solved\nBase {b:.1f}°  Shoulder {s:.1f}°  Elbow {e:.1f}°"
        )
        self._refresh()
        if self._bridge:
            self._send_to_hardware()

    # ------------------------------------------------------------------
    # Render

    def _refresh(self) -> None:
        ax = self._ax
        ax.cla()

        b  = self._base_var.get()
        s  = self._shoulder_var.get()
        e  = self._elbow_var.get()
        l1 = self._l1_var.get()
        l2 = self._l2_var.get()

        shoulder, elbow, tip = fk(b, s, e, l1, l2)

        # Arm links
        pts = np.stack([shoulder, elbow, tip])
        ax.plot(pts[:, 0], pts[:, 1], pts[:, 2],
                color=_ACCENT, linewidth=4, solid_capstyle="round", zorder=3)

        # Joints
        ax.scatter(*shoulder, color="#888888", s=70,  zorder=4)
        ax.scatter(*elbow,    color="#ffffff", s=70,  zorder=4)
        ax.scatter(*tip,      color="#ff6b6b", s=100, zorder=5)

        # Base platform ring
        theta = np.linspace(0, 2 * math.pi, 80)
        r_ring = 3.0
        ax.plot(r_ring * np.cos(theta), r_ring * np.sin(theta),
                np.zeros(80), color="#444444", linewidth=1)

        # IK target marker
        if self._ik_target is not None:
            ax.scatter(*self._ik_target, color="#ffcc00", s=120,
                       marker="x", linewidths=2.5, zorder=6)
            # Dashed line from tip to target (shows the gap when unreachable)
            gap = np.stack([tip, self._ik_target])
            ax.plot(gap[:, 0], gap[:, 1], gap[:, 2],
                    color="#ffcc0066", linewidth=1, linestyle="--")

        # Axes / style
        reach = l1 + l2
        ax.set_xlim(-reach, reach)
        ax.set_ylim(-reach, reach)
        ax.set_zlim(-reach * 0.15, reach)

        for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
            axis.pane.fill = False
            axis.pane.set_edgecolor("#2a2a2a")

        ax.set_xlabel("X (cm)", color="#555555", labelpad=2, fontsize=8)
        ax.set_ylabel("Y (cm)", color="#555555", labelpad=2, fontsize=8)
        ax.set_zlabel("Z (cm)", color="#555555", labelpad=2, fontsize=8)
        ax.tick_params(colors="#444444", labelsize=7)
        ax.set_facecolor(_BG)
        ax.grid(True, color="#252525")
        ax.set_title(
            f"Base {b:.1f}°   Shoulder {s:.1f}°   Elbow {e:.1f}°",
            color="#aaaaaa", fontsize=9, pad=8,
        )

        self._canvas.draw_idle()

    # ------------------------------------------------------------------
    # Hardware (optional)

    def _send_to_hardware(self) -> None:
        from robotic_arm.config import ArmLayout
        layout = ArmLayout()
        b = self._base_var.get()
        s = self._shoulder_var.get()
        e = self._elbow_var.get()

        # Convert degrees to normalized [-1, 1] within each joint's defined range,
        # then let JointSpec.to_us() map to the correct pulse width.
        base_norm     = (b / 180.0) * 2.0 - 1.0
        shoulder_norm = (s / 130.0) * 2.0 - 1.0
        elbow_norm    = ((e - 130.0) / 50.0) * 2.0 - 1.0

        try:
            self._bridge.set_channel(layout.base.channel,
                                     layout.base.to_us(base_norm))
            self._bridge.set_channel(layout.shoulder2.channel,
                                     layout.shoulder2.to_us(shoulder_norm))
            self._bridge.set_channel(layout.elbow.channel,
                                     layout.elbow.to_us(elbow_norm))
        except Exception as exc:
            self._status_var.set(f"Hardware error: {exc}")

    # ------------------------------------------------------------------

    def run(self) -> None:
        self.root.mainloop()


# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Robotic arm FK/IK visualizer")
    parser.add_argument("--port", default=None,
                        help="Serial port to drive real hardware (e.g. COM3 or /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--skip-ready", action="store_true",
                        help="Don't wait for READY banner (use before Arduino firmware is flashed)")
    args = parser.parse_args()

    bridge = None
    if args.port:
        from robotic_arm.serial_bridge import SerialBridge
        bridge = SerialBridge(args.port, args.baud, require_ready=not args.skip_ready)
        bridge.open()

    try:
        ArmVisualizer(bridge=bridge).run()
    finally:
        if bridge:
            bridge.close()


if __name__ == "__main__":
    main()
