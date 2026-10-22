"""
C4 regression test: no chart frame must be taller than 260px on any PSX tab.

We check two things:
1. No section template has an inline --chart-h override that parses to > 260 px.
2. The CSS tokens --chart-h-mini and --chart-h-inline are ≤ 260 px (at 16 px/rem).
"""
import os
import re
import glob


_SECTION_GLOB = os.path.join(
    os.path.dirname(__file__), "..", "templates", "psx", "section_*.html"
)
_TOKENS_CSS = os.path.join(
    os.path.dirname(__file__), "..", "static", "css", "tokens.css"
)
_REM_PX = 16.0
_MAX_PX = 260.0


def _rem_to_px(value: str) -> float:
    value = value.strip()
    if value.endswith("rem"):
        return float(value[:-3]) * _REM_PX
    if value.endswith("px"):
        return float(value[:-2])
    return float(value)


def test_no_section_template_overrides_chart_height_over_limit():
    """No section template should set --chart-h to a value exceeding 260 px."""
    pattern = re.compile(r"--chart-h\s*:\s*([\d.]+(?:rem|px))")
    violations = []
    for path in glob.glob(_SECTION_GLOB):
        with open(path) as f:
            src = f.read()
        for m in pattern.finditer(src):
            px = _rem_to_px(m.group(1))
            if px > _MAX_PX:
                violations.append(f"{os.path.basename(path)}: --chart-h={m.group(1)} ({px:.0f}px > {_MAX_PX:.0f}px)")

    assert not violations, "Section templates set chart height above limit:\n" + "\n".join(violations)


def test_css_token_chart_h_mini_within_limit():
    """--chart-h-mini must be ≤ 260 px."""
    with open(_TOKENS_CSS) as f:
        src = f.read()
    m = re.search(r"--chart-h-mini\s*:\s*([\d.]+(?:rem|px))", src)
    assert m, "--chart-h-mini token not found in tokens.css"
    px = _rem_to_px(m.group(1))
    assert px <= _MAX_PX, f"--chart-h-mini is {px:.0f}px, exceeds {_MAX_PX:.0f}px limit"


def test_css_token_chart_h_inline_within_limit():
    """--chart-h-inline must be ≤ 260 px."""
    with open(_TOKENS_CSS) as f:
        src = f.read()
    m = re.search(r"--chart-h-inline\s*:\s*([\d.]+(?:rem|px))", src)
    assert m, "--chart-h-inline token not found in tokens.css"
    px = _rem_to_px(m.group(1))
    assert px <= _MAX_PX, f"--chart-h-inline is {px:.0f}px, exceeds {_MAX_PX:.0f}px limit"
