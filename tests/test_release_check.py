"""Tests for scripts/validate_release.py."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from scripts.validate_release import validate


def _write(path: str, commit: str, wrong: int) -> None:
    Path(path).write_text(json.dumps({"commit": commit, "wrong_figures": wrong}))


def test_passes_when_commit_and_figures_ok(tmp_path):
    f = str(tmp_path / "release_check.json")
    _write(f, "abc123", 0)
    with patch("subprocess.check_output") as sp:
        sp.side_effect = ["abc123\n", f + "\n"]
        assert validate(f) is None


def test_fails_on_commit_mismatch(tmp_path):
    f = str(tmp_path / "release_check.json")
    _write(f, "wrong000", 0)
    with patch("subprocess.check_output") as sp:
        sp.side_effect = ["abc123\n", f + "\n"]
        err = validate(f)
    assert err is not None
    assert "mismatch" in err


def test_fails_when_extra_files_changed(tmp_path):
    f = str(tmp_path / "release_check.json")
    _write(f, "abc123", 0)
    with patch("subprocess.check_output") as sp:
        sp.side_effect = ["abc123\n", f + "\nREADME.md\n"]
        err = validate(f)
    assert err is not None
    assert "README" in err


def test_fails_when_wrong_figures_nonzero(tmp_path):
    f = str(tmp_path / "release_check.json")
    _write(f, "abc123", 3)
    with patch("subprocess.check_output") as sp:
        sp.side_effect = ["abc123\n", f + "\n"]
        err = validate(f)
    assert err is not None
    assert "wrong_figures" in err


def test_fails_when_file_missing(tmp_path):
    err = validate(str(tmp_path / "no_such.json"))
    assert err is not None
    assert "MISSING" in err
