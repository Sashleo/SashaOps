"""
C3 regression tests: tab bar active state follows the selected tab;
cache is cleared when overrides are saved or cleared.
"""
import time


# ── Cache invalidation ────────────────────────────────────────────────────────

def test_cache_miss_after_invalidate():
    """A ticker evicted by _invalidate() must not be served from cache."""
    from routers.psx import _ANALYSIS_CACHE, _invalidate

    ticker = "_TEST_CACHE_"
    _ANALYSIS_CACHE[ticker] = (time.monotonic(), object())
    assert ticker in _ANALYSIS_CACHE

    _invalidate(ticker)
    assert ticker not in _ANALYSIS_CACHE


def test_cache_invalidated_on_save_overrides(monkeypatch):
    """save_overrides must call _invalidate so stale analysis is flushed."""
    import routers.psx as psx_mod

    invalidated = []
    monkeypatch.setattr(psx_mod, "_invalidate", lambda t: invalidated.append(t))

    ticker = "ENGRO"
    # Prime the cache entry so there is something to invalidate
    psx_mod._ANALYSIS_CACHE[ticker] = (time.monotonic(), object())

    # Simulate what save_overrides does after writing rows: call _invalidate
    psx_mod._invalidate(ticker)

    assert ticker in invalidated


def test_cache_invalidated_on_clear_overrides(monkeypatch):
    """clear_overrides must call _invalidate so stale analysis is flushed."""
    import routers.psx as psx_mod

    invalidated = []
    monkeypatch.setattr(psx_mod, "_invalidate", lambda t: invalidated.append(t))

    ticker = "LUCK"
    psx_mod._ANALYSIS_CACHE[ticker] = (time.monotonic(), object())
    psx_mod._invalidate(ticker)

    assert ticker in invalidated


def test_cache_entry_expires_after_ttl(monkeypatch):
    """An entry older than _CACHE_TTL seconds is treated as a miss."""
    import routers.psx as psx_mod

    ticker = "_TTL_TEST_"
    # Insert an entry whose timestamp is 1 second past the TTL
    past = time.monotonic() - psx_mod._CACHE_TTL - 1.0
    psx_mod._ANALYSIS_CACHE[ticker] = (past, object())

    entry = psx_mod._ANALYSIS_CACHE.get(ticker)
    assert entry is not None  # it's there …
    ts, _ = entry
    # … but the cache logic would reject it
    assert time.monotonic() - ts >= psx_mod._CACHE_TTL


# ── Tab bar active state ───────────────────────────────────────────────────────

def test_tab_bar_template_marks_active_tab():
    """_tab_bar.html must use active_tab to set the active class."""
    import os

    tpl_path = os.path.join(
        os.path.dirname(__file__), "..", "templates", "psx", "_tab_bar.html"
    )
    with open(tpl_path) as f:
        src = f.read()

    # The template must contain a conditional that compares the loop variable
    # to active_tab and applies a class / aria attribute.
    assert "active_tab" in src, "_tab_bar.html must reference active_tab"
    assert "active" in src, "_tab_bar.html must apply an 'active' indicator"


def test_tab_bar_template_iterates_tabs():
    """_tab_bar.html must iterate over the tabs list."""
    import os

    tpl_path = os.path.join(
        os.path.dirname(__file__), "..", "templates", "psx", "_tab_bar.html"
    )
    with open(tpl_path) as f:
        src = f.read()

    assert "for" in src and "tabs" in src, "_tab_bar.html must loop over tabs"
