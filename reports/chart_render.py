"""
reports/chart_render.py - Phase 3 Task 3.8, static export side.

Renders the SAME chart specs that intelligence/psx_charts.py builds for the
browser, using matplotlib at 150 DPI, so a chart in the PDF carries identical
values, labels, units and title to the one on screen. Nothing is screenshotted.

Returns base64 PNG, or None when a chart cannot be drawn. A failed chart never
raises into report generation.
"""

from __future__ import annotations

import base64
import io
from typing import Optional

import structlog

log = structlog.get_logger()

DPI = 150
COLOR_SUBJECT = "#00a862"     # print-legible variant of the screen accent
COLOR_NEUTRAL = "#6b7280"
COLOR_WATCH = "#f59e0b"
COLOR_RISK = "#dc2626"


def _fig(width_in: float = 6.4, height_in: float = 2.8):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=DPI)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color="#e5e7eb", linewidth=0.6)
    ax.set_axisbelow(True)
    return fig, ax


def _finish(fig, ax, spec) -> str:
    if spec.get("title"):
        ax.set_title(spec["title"], fontsize=9, loc="left")
    if spec.get("x_label"):
        ax.set_xlabel(spec["x_label"], fontsize=8)
    if spec.get("y_label"):
        ax.set_ylabel(spec["y_label"], fontsize=8)
    ax.tick_params(labelsize=7)
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=DPI)
    import matplotlib.pyplot as plt
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _grouped_bar(spec) -> str:
    import numpy as np
    fig, ax = _fig()
    labels = spec["labels"]
    series = spec["series"]
    x = np.arange(len(labels))
    width = 0.8 / max(len(series), 1)
    for i, s in enumerate(series):
        # None stays a gap: matplotlib skips NaN rather than plotting a zero.
        vals = [np.nan if v is None else v for v in s["data"]]
        ax.bar(x + i * width - 0.4 + width / 2, vals, width,
               label=s["name"], color=s.get("color") or COLOR_NEUTRAL)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend(fontsize=7, frameon=False)
    return _finish(fig, ax, spec)


def _small_multiples(spec) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    panels = spec["series"]
    cols = 3
    rows = (len(panels) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(6.4, 1.5 * rows), dpi=DPI)
    axes = np.atleast_1d(axes).ravel()
    for ax, panel in zip(axes, panels):
        vals = [np.nan if v is None else v for v in panel["data"]]
        ax.plot(panel["labels"], vals, color=COLOR_SUBJECT, linewidth=1.4, marker="o",
                markersize=2.5)
        if panel.get("median") is not None:
            ax.axhline(panel["median"], color=COLOR_NEUTRAL, linestyle="--", linewidth=0.8)
        ax.set_title(panel["name"], fontsize=7, loc="left")
        ax.tick_params(labelsize=6)
        ax.grid(color="#e5e7eb", linewidth=0.5)
        ax.set_axisbelow(True)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    for ax in axes[len(panels):]:
        ax.set_visible(False)
    if spec.get("title"):
        fig.suptitle(spec["title"], fontsize=9, x=0.02, ha="left")
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=DPI)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _dot_plot(spec) -> str:
    fig, ax = _fig(6.4, max(1.6, 0.34 * len(spec["series"])))
    names = [r["name"] for r in spec["series"]]
    vals = [r["percentile"] for r in spec["series"]]
    y = range(len(names))
    ax.barh(list(y), [100] * len(names), color="#f0f0f0", height=0.45)
    ax.scatter(vals, list(y), color=COLOR_SUBJECT, zorder=3, s=42)
    ax.axvline(spec.get("median_marker", 50), color=COLOR_NEUTRAL,
               linestyle="--", linewidth=0.9)
    ax.set_yticks(list(y))
    ax.set_yticklabels(names, fontsize=7)
    ax.set_xlim(0, 100)
    ax.grid(axis="x", color="#e5e7eb", linewidth=0.6)
    return _finish(fig, ax, spec)


def _scatter(spec) -> str:
    fig, ax = _fig()
    for p in spec["series"]:
        ax.scatter(p["x"], p["y"],
                   color=COLOR_SUBJECT if p.get("is_subject") else COLOR_NEUTRAL,
                   s=70 if p.get("is_subject") else 26, zorder=3)
        if p.get("is_subject"):
            ax.annotate(p["label"], (p["x"], p["y"]), fontsize=7,
                        xytext=(4, 4), textcoords="offset points")
    if spec.get("x_median") is not None:
        ax.axvline(spec["x_median"], color="#cbd5e1", linewidth=0.8)
    if spec.get("y_median") is not None:
        ax.axhline(spec["y_median"], color="#cbd5e1", linewidth=0.8)
    return _finish(fig, ax, spec)


def _stacked_bar(spec) -> str:
    import numpy as np
    fig, ax = _fig(6.4, 2.2)
    labels = spec["labels"]
    left = np.zeros(len(labels))
    for s in spec["series"]:
        vals = np.array([0 if v is None else v for v in s["data"]], dtype=float)
        ax.barh(labels, vals, left=left, label=s["name"],
                color=s.get("color") or COLOR_NEUTRAL)
        left = left + vals
    ax.legend(fontsize=7, frameon=False)
    return _finish(fig, ax, spec)


def _gauge(spec) -> str:
    fig, ax = _fig(6.4, 1.5)
    left = 0.0
    for b in spec.get("bands", []):
        ax.barh([0], [b["width"]], left=left, color=b["color"], height=0.4,
                label=b["name"])
        left += b["width"]
    score = spec.get("score")
    if score is not None:
        ax.plot([score], [0], marker="D", color="#111", markersize=9, zorder=4)
        ax.annotate(f"{score}", (score, 0), fontsize=8, xytext=(0, 12),
                    textcoords="offset points", ha="center")
    ax.set_yticks([])
    ax.legend(fontsize=6.5, frameon=False, ncol=3, loc="upper center")
    return _finish(fig, ax, spec)


def _heatmap(spec) -> str:
    return _grouped_bar(spec)


_RENDERERS = {
    "grouped_bar": _grouped_bar,
    "small_multiples": _small_multiples,
    "dot_plot": _dot_plot,
    "scatter": _scatter,
    "stacked_bar": _stacked_bar,
    "gauge": _gauge,
    "heatmap": _heatmap,
}


def render_chart_png_base64(spec: dict) -> Optional[str]:
    """Render one chart spec to a base64 PNG. Returns None if it cannot be drawn."""
    try:
        fn = _RENDERERS.get(spec.get("kind"))
        if not fn:
            return None
        return fn(spec)
    except Exception as e:
        log.info("static chart render failed", kind=spec.get("kind"), error=str(e)[:100])
        return None
