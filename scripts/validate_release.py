#!/usr/bin/env python3
"""Validate release_check.json before pushing a release tag.

Rules (all must pass):
  1. docs/pipeline/release_check.json exists.
  2. Its "commit" field equals git rev-parse HEAD^ (the pre-tag commit).
  3. git diff --name-only HEAD^ HEAD lists only that one file.
  4. Its "wrong_figures" field is 0.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_DEFAULT_FILE = "docs/pipeline/release_check.json"


def validate(release_file: str = _DEFAULT_FILE) -> str | None:
    """Return an error string, or None if all checks pass."""
    p = Path(release_file)
    if not p.exists():
        return f"MISSING: {release_file}"

    d = json.loads(p.read_text())

    recorded  = d.get("commit", "")
    head_prev = subprocess.check_output(
        ["git", "rev-parse", "HEAD^"], text=True
    ).strip()
    if recorded != head_prev:
        return (
            f"commit mismatch: release_check.json has {recorded!r}, "
            f"HEAD^ is {head_prev!r}"
        )

    changed = subprocess.check_output(
        ["git", "diff", "--name-only", "HEAD^", "HEAD"], text=True
    ).strip().splitlines()
    if changed != [release_file]:
        return (
            f"HEAD introduces changes beyond {release_file}: {changed!r} — "
            "the release commit must only add release_check.json"
        )

    wrong = d.get("wrong_figures", 1)
    if wrong != 0:
        return f"wrong_figures={wrong}, must be 0 before a release"

    return None


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_FILE
    err = validate(target)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
    print("Release check passed — 0 wrong figures, commit recorded correctly.")
