"""Servo Tuner — live visualizer and hardware jog tool for the 6-DOF arm.

Run (simulation / preview only):
    python -m action_servos.tuner

Run (hardware toggle enabled — Jetson with PCA9685 connected):
    python -m action_servos.tuner --hw
"""
from __future__ import annotations

import argparse
import logging
import math
import tkinter as tk
from typing import Callable, Dict, Optional

from action_servos.config import (
    DEFAULT_I2C_BUS,
    DEFAULT_PCA9685_ADDRESS,
    DEFAULT_PWM_FREQUENCY_HZ,
    JointSpec,
    ServoLayout,
)
from action_servos.groups import ServoOrchestrator, normalized_to_us, us_to_normalized

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canvas geometry
# ---------------------------------------------------------------------------
_CW, _CH = 560, 430

# Side-view arm base (shoulder pivot)
_BASE_X, _BASE_Y = 210, 370

# Segment pixel lengths
_L_UPPER = 120   # shoulder → elbow
_L_FORE  = 95    # elbow → tip

# Base-rotation inset (top-down mini-view)
_ROT_CX, _ROT_CY, _ROT_R = 60, 360, 38

# Palette
_C = {
    "base":    "#9b59b6",
    "shoulder":"#3a7bd5",
    "elbow":   "#e74c3c",
    "grid":    "#ebebeb",
    "ground":  "#aaaaaa",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pulse_to_deg(spec: JointSpec, pulse_us: float) -> float:
    """Map pulse_us → degrees: centre_us→0°, edges→±90°."""
    half = (spec.max_us - spec.min_us) / 2.0
    if half <= 0:
        return 0.0
    n = (pulse_us - spec.center_us) / half
    return max(-1.0, min(1.0, n)) * 90.0


# ---------------------------------------------------------------------------
# SliderRow
# ---------------------------------------------------------------------------

class SliderRow:
    """Labelled Scale with µs and normalised readout."""

    def __init__(
        self,
        parent: tk.Widget,
        row: int,
        label: str,
        spec: JointSpec,
        color: str,
        on_change: Callable[[], None],
    ) -> None:
        self._spec = spec
        self._on_change = on_change
        self._var = tk.DoubleVar(value=spec.center_us)

        tk.Label(parent, text=label, width=12, anchor="w",
                 font=("Helvetica", 11), bg="#f4f4f4").grid(
            row=row, column=0, sticky="w", padx=(8, 4), pady=4)

        self._scale = tk.Scale(
            parent,
            from_=spec.min_us, to=spec.max_us,
            orient="horizontal", resolution=1,
            variable=self._var, length=200,
            showvalue=False,
            troughcolor="#dedede",
            activebackground=color,
            highlightthickness=0,
            command=self._changed,
        )
        self._scale.grid(row=row, column=1, padx=4)

        self._lbl_us = tk.Label(parent, text=f"{spec.center_us:.0f} µs",
                                 width=8, font=("Helvetica", 10, "bold"),
                                 fg="#222", bg="#f4f4f4")
        self._lbl_us.grid(row=row, column=2, padx=4)

        self._lbl_n = tk.Label(parent, text=" 0.00", width=6,
                                font=("Helvetica", 10), fg="#888", bg="#f4f4f4")
        self._lbl_n.grid(row=row, column=3, padx=(0, 8))

    def _changed(self, _: str) -> None:
        us = self._var.get()
        n  = us_to_normalized(self._spec, us)
        self._lbl_us.config(text=f"{us:.0f} µs")
        self._lbl_n.config(text=f"{n:+.2f}")
        self._on_change()

    @property
    def pulse_us(self) -> float:
        return self._var.get()

    def set_pulse(self, us: float) -> None:
        clamped = max(self._spec.min_us, min(self._spec.max_us, us))
        self._var.set(clamped)
        n = us_to_normalized(self._spec, clamped)
        self._lbl_us.config(text=f"{clamped:.0f} µs")
        self._lbl_n.config(text=f"{n:+.2f}")

    def set_normalized(self, n: float) -> None:
        self.set_pulse(normalized_to_us(self._spec, n))


# ---------------------------------------------------------------------------
# TunerApp
# ---------------------------------------------------------------------------

class TunerApp:
    def __init__(self, root: tk.Tk, layout: ServoLayout, hw_available: bool) -> None:
        self._root = root
        self._layout = layout
        self._hw_available = hw_available
        self._orch: Optional[ServoOrchestrator] = None
        self._hw_on = False

        root.title("6-DOF Servo Tuner")
        root.resizable(False, False)
        root.configure(bg="#f4f4f4")

        self._build_ui()
        self._redraw()

    # ------------------------------------------------------------------ UI --

    def _build_ui(self) -> None:
        L = self._layout
        outer = tk.Frame(self._root, bg="#f4f4f4")
        outer.pack(padx=12, pady=10)

        # Canvas (left)
        cw = tk.Frame(outer, bg="#ffffff", relief="solid", bd=1)
        cw.grid(row=0, column=0, sticky="n", padx=(0, 14))
        tk.Label(cw, text="Arm Visualization", font=("Helvetica", 11, "bold"),
                 bg="#ffffff", fg="#333").pack(pady=(6, 0))
        self._canvas = tk.Canvas(cw, width=_CW, height=_CH,
                                  bg="#fafafa", highlightthickness=0)
        self._canvas.pack(padx=8, pady=(2, 8))

        # Controls (right)
        ctrl = tk.Frame(outer, bg="#f4f4f4")
        ctrl.grid(row=0, column=1, sticky="n")

        # ── Sliders ──────────────────────────────────────────────────────
        sf = tk.LabelFrame(ctrl, text="Servo Positions",
                            font=("Helvetica", 11, "bold"),
                            bg="#f4f4f4", padx=4, pady=4)
        sf.pack(fill="x", pady=(0, 8))

        for col, hdr in enumerate(["Joint", "Position", "µs", "Norm"]):
            tk.Label(sf, text=hdr, font=("Helvetica", 9), fg="#aaa",
                     bg="#f4f4f4").grid(row=0, column=col)

        cb = self._slider_changed
        self._sliders: Dict[str, SliderRow] = {
            "base":     SliderRow(sf, 1, "Base (pan)", L.base,       _C["base"],     cb),
            "shoulder": SliderRow(sf, 2, "Shoulder",   L.shoulder_a, _C["shoulder"], cb),
            "elbow":    SliderRow(sf, 3, "Elbow",      L.elbow,      _C["elbow"],    cb),
        }

        # ── Presets ───────────────────────────────────────────────────────
        pf = tk.LabelFrame(ctrl, text="Presets",
                            font=("Helvetica", 11, "bold"),
                            bg="#f4f4f4", padx=4, pady=4)
        pf.pack(fill="x", pady=(0, 8))

        presets = [
            ("Center",  self._preset_center),
            ("Neutral", lambda: self._arm_preset("neutral")),
            ("Rest",    lambda: self._arm_preset("rest")),
            ("Extend",  lambda: self._arm_preset("extend")),
            ("Retract", lambda: self._arm_preset("retract")),
            ("Point",   lambda: self._arm_preset("point")),
        ]
        for i, (lbl, cmd) in enumerate(presets):
            r, c = divmod(i, 3)
            tk.Button(pf, text=lbl, command=cmd, width=10,
                      font=("Helvetica", 9), bg="#e2e2e2",
                      relief="flat", cursor="hand2",
                      activebackground="#d0d0d0").grid(
                row=r, column=c, padx=3, pady=3)

        # ── Hardware ──────────────────────────────────────────────────────
        hf = tk.LabelFrame(ctrl, text="Hardware",
                            font=("Helvetica", 11, "bold"),
                            bg="#f4f4f4", padx=6, pady=6)
        hf.pack(fill="x")

        self._hw_btn = tk.Button(
            hf, text="Connect Hardware",
            command=self._toggle_hw,
            font=("Helvetica", 10, "bold"),
            bg="#27ae60", fg="white",
            activebackground="#1e8449",
            relief="flat", cursor="hand2", pady=5,
        )
        self._hw_btn.pack(fill="x", pady=(0, 4))
        if not self._hw_available:
            self._hw_btn.config(state="disabled", bg="#aaa",
                                 text="Hardware (use --hw to enable)")

        self._hw_lbl = tk.Label(hf, text="Simulation mode",
                                 font=("Helvetica", 9), fg="#888", bg="#f4f4f4")
        self._hw_lbl.pack()

    # -------------------------------------------------------- slider changes -

    def _slider_changed(self) -> None:
        self._redraw()
        if self._hw_on and self._orch is not None:
            try:
                self._push_to_hw()
            except OSError as exc:
                logger.warning("HW write error: %s", exc)
                self._set_hw(connected=False, error=str(exc))

    def _push_to_hw(self) -> None:
        s = self._sliders
        self._orch.arm.set_all(
            s["base"].pulse_us,
            s["shoulder"].pulse_us,
            s["elbow"].pulse_us,
        )

    # -------------------------------------------------------------- presets -

    def _preset_center(self) -> None:
        L = self._layout
        self._sliders["base"].set_pulse(L.base.center_us)
        self._sliders["shoulder"].set_pulse(L.shoulder_a.center_us)
        self._sliders["elbow"].set_pulse(L.elbow.center_us)
        self._slider_changed()

    # Maps mirror arm_actions.py constants
    _ARM_PRESETS = {
        "neutral": (0.0,  0.0,  0.0),
        "rest":    (0.0, -0.5, -0.8),
        "extend":  (0.0,  0.3,  0.7),
        "retract": (0.0, -0.3, -0.6),
        "point":   (0.0,  0.6,  0.3),
    }

    def _arm_preset(self, name: str) -> None:
        if name not in self._ARM_PRESETS:
            return
        base_n, sh_n, el_n = self._ARM_PRESETS[name]
        self._sliders["base"].set_normalized(base_n)
        self._sliders["shoulder"].set_normalized(sh_n)
        self._sliders["elbow"].set_normalized(el_n)
        self._slider_changed()

    # ------------------------------------------------------------- hardware -

    def _toggle_hw(self) -> None:
        if self._hw_on:
            self._disconnect_hw()
        else:
            self._connect_hw()

    def _connect_hw(self) -> None:
        try:
            orch = ServoOrchestrator(self._layout)
            orch.open(DEFAULT_I2C_BUS, DEFAULT_PCA9685_ADDRESS, DEFAULT_PWM_FREQUENCY_HZ)
            self._orch = orch
            self._set_hw(connected=True)
            self._push_to_hw()
        except Exception as exc:
            self._set_hw(connected=False, error=str(exc))

    def _disconnect_hw(self) -> None:
        if self._orch:
            try:
                self._orch.close()
            except Exception:
                pass
            self._orch = None
        self._set_hw(connected=False)

    def _set_hw(self, *, connected: bool, error: Optional[str] = None) -> None:
        self._hw_on = connected
        if connected:
            self._hw_btn.config(text="Disconnect Hardware",
                                 bg="#e74c3c", activebackground="#c0392b")
            self._hw_lbl.config(text="Live — every slider move goes to hardware", fg="#27ae60")
        else:
            self._hw_btn.config(text="Connect Hardware",
                                 bg="#27ae60", activebackground="#1e8449")
            if error:
                msg = error[:65] + "…" if len(error) > 65 else error
                self._hw_lbl.config(text=f"Error: {msg}", fg="#e74c3c")
            else:
                self._hw_lbl.config(text="Simulation mode", fg="#888")

    # --------------------------------------------------------------- drawing -

    def _redraw(self) -> None:
        c = self._canvas
        c.delete("all")
        L = self._layout
        s = self._sliders

        base_deg = _pulse_to_deg(L.base,       s["base"].pulse_us)
        sh_deg   = _pulse_to_deg(L.shoulder_a, s["shoulder"].pulse_us)
        el_deg   = _pulse_to_deg(L.elbow,      s["elbow"].pulse_us)

        self._draw_grid(c)
        self._draw_base_rotation(c, base_deg)
        self._draw_arm(c, sh_deg, el_deg)
        self._draw_legend(c)

    def _draw_grid(self, c: tk.Canvas) -> None:
        for x in range(0, _CW, 40):
            c.create_line(x, 0, x, _CH, fill=_C["grid"])
        for y in range(0, _CH, 40):
            c.create_line(0, y, _CW, y, fill=_C["grid"])

    def _draw_base_rotation(self, c: tk.Canvas, base_deg: float) -> None:
        """Top-down mini view showing base pan rotation."""
        cx, cy, r = _ROT_CX, _ROT_CY, _ROT_R
        # Outer ring
        c.create_oval(cx - r, cy - r, cx + r, cy + r,
                      fill="#f0f0f0", outline="#888", width=1)
        # Range arc ±90°
        c.create_arc(cx - r, cy - r, cx + r, cy + r,
                     start=0, extent=180,
                     style="arc", outline="#cccccc", width=1, dash=(3, 3))
        # Rotation indicator line
        rad = math.radians(base_deg)
        lx = cx + r * 0.85 * math.cos(rad)
        ly = cy - r * 0.85 * math.sin(rad)
        c.create_line(cx, cy, lx, ly, fill=_C["base"], width=3, capstyle="round")
        c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=_C["base"], outline="")
        # Label
        c.create_text(cx, cy + r + 10, text=f"base\n{base_deg:+.1f}°",
                       font=("Helvetica", 8), fill="#555", justify="center")

    def _draw_arm(self, c: tk.Canvas, sh_deg: float, el_deg: float) -> None:
        bx, by = _BASE_X, _BASE_Y

        # Ground hatch
        c.create_line(bx - 40, by, bx + 40, by, fill=_C["ground"], width=2)
        for dx in range(-35, 40, 10):
            c.create_line(bx + dx, by, bx + dx - 7, by + 8, fill="#cccccc")

        # ── range arcs (dashed) ──────────────────────────────────────────
        self._arc(c, bx, by, _L_UPPER + 6, 0, 180, "#c8d8f0")

        sh_r = math.radians(sh_deg)
        ex = bx + _L_UPPER * math.cos(sh_r)
        ey = by - _L_UPPER * math.sin(sh_r)
        self._arc(c, ex, ey, _L_FORE + 4, sh_deg - 90, 180, "#f5c6c4")

        el_total = sh_deg + el_deg
        el_r = math.radians(el_total)
        tx = ex + _L_FORE * math.cos(el_r)
        ty = ey - _L_FORE * math.sin(el_r)

        # ── arm segments ─────────────────────────────────────────────────
        c.create_line(bx, by, ex, ey, fill=_C["shoulder"], width=9, capstyle="round")
        c.create_line(ex, ey, tx, ty, fill=_C["elbow"],    width=7, capstyle="round")

        # ── joints ───────────────────────────────────────────────────────
        c.create_oval(bx - 9, by - 9, bx + 9, by + 9, fill="#333", outline="#111", width=2)
        c.create_oval(ex - 7, ey - 7, ex + 7, ey + 7, fill=_C["elbow"], outline="#c0392b", width=2)
        c.create_oval(tx - 5, ty - 5, tx + 5, ty + 5, fill=_C["elbow"], outline="#c0392b", width=2)

        # ── angle labels ─────────────────────────────────────────────────
        c.create_text(bx - 14, by - 22, text=f"{sh_deg:+.1f}°",
                       font=("Helvetica", 9, "bold"), fill=_C["shoulder"], anchor="e")
        c.create_text(ex + 12, ey - 12, text=f"{el_deg:+.1f}°",
                       font=("Helvetica", 9, "bold"), fill=_C["elbow"], anchor="w")

    def _arc(self, c: tk.Canvas, cx: float, cy: float, r: float,
             start: float, extent: float, color: str) -> None:
        c.create_arc(
            cx - r, cy - r, cx + r, cy + r,
            start=start, extent=extent,
            style="arc", outline=color, width=1, dash=(4, 4),
        )

    def _draw_legend(self, c: tk.Canvas) -> None:
        items = [
            (_C["base"],     "Base pan"),
            (_C["shoulder"], "Shoulder"),
            (_C["elbow"],    "Elbow"),
        ]
        x, y = 8, 8
        for color, label in items:
            c.create_rectangle(x, y, x + 12, y + 12, fill=color, outline="")
            c.create_text(x + 16, y + 6, text=label, anchor="w",
                           font=("Helvetica", 9), fill="#555")
            y += 18

    # -------------------------------------------------------------- close ---

    def on_close(self) -> None:
        self._disconnect_hw()
        self._root.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="6-DOF visual servo tuner")
    parser.add_argument("--hw", action="store_true",
                        help="Enable hardware connect button (requires I2C on device)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    layout = ServoLayout.default_layout()
    root = tk.Tk()
    app = TunerApp(root, layout, hw_available=args.hw)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
