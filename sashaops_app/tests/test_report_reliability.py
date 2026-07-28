"""
Regression tests for the accuracy gate on annual-report data.

The tool's integrity depends on every digit being right, so an OCR/scanned report
whose figures cannot be independently verified must NEVER reach an analysis. These
tests lock in that rule: OCR is trusted only when its balance sheet reconciles;
otherwise the figure stays missing (surfaced as "Required: X") rather than wrong.
"""

from intelligence.psx_financials import _report_is_reliable


def test_ocr_report_that_does_not_reconcile_is_rejected():
    # A real scanned LUCK booklet parsed net income (48bn) ABOVE revenue (0.31bn):
    # internally impossible OCR misreads. It must not be trusted.
    bad = {"revenue": 0.31e9, "net_income": 48e9, "total_assets": 789e9,
           "_ocr": True, "_balance_ok": None}
    assert _report_is_reliable(bad) is False


def test_ocr_report_is_rejected_when_balance_fails():
    bad = {"revenue": 80e9, "total_assets": 350e9, "_ocr": True, "_balance_ok": False}
    assert _report_is_reliable(bad) is False


def test_ocr_report_is_trusted_only_when_balance_reconciles():
    # Reconciliation (Assets == Liabilities + Equity) is an arithmetic cross-check
    # that near-random OCR digit errors cannot pass by chance.
    good = {"revenue": 80e9, "net_income": 15e9, "total_assets": 350e9,
            "_ocr": True, "_balance_ok": True}
    assert _report_is_reliable(good) is True


def test_digital_report_is_trusted():
    # A digital text-layer parse is protected by the parser's anchoring / sign /
    # plausibility guards, so it is trusted without requiring full reconciliation.
    digital = {"revenue": 275e9, "net_income": 66e9, "total_assets": 7708e9}
    assert _report_is_reliable(digital) is True


# ── Behavioural: the gate actually changes what reaches the analysis ───────────
# These call the REAL fetch_psx_doc_financials with the two data sources stubbed, so
# they fail if anyone removes the reliability branch - not just if a comment changes.

import intelligence.psx_financials as pf

_SUMMARY = {
    "annual_financials": {
        "Net Sales": {"2025": 80_000_000},          # authoritative income statement
        "Profit after taxation": {"2025": 15_000_000},
    },
    "ratios": {},
}


def _stub_sources(monkeypatch, report):
    # PSX structured summary (clean HTML) - stubbed so no network is needed.
    monkeypatch.setattr(pf, "fetch_psx_company", lambda *a, **k: dict(_SUMMARY))
    # The annual-report parse under test.
    import intelligence.psx_report_parser as rp
    monkeypatch.setattr(rp, "fetch_psx_report_financials", lambda *a, **k: report)
    # Keep everything else off the network/DB.
    monkeypatch.setattr(pf, "get_cached_financials", lambda *a, **k: [])
    monkeypatch.setattr(pf, "upsert_psx_financials", lambda *a, **k: None)


def test_unreliable_ocr_balance_sheet_is_NOT_merged(monkeypatch):
    # OCR report with a huge (wrong) total_assets that does not reconcile.
    bad_report = {"revenue": 80_000_000, "net_income": 15_000_000,
                  "total_assets": 999_999_999, "current_assets": 500_000_000,
                  "_ocr": True, "_balance_ok": None}
    _stub_sources(monkeypatch, bad_report)
    doc, _ = pf.fetch_psx_doc_financials("TEST")
    # The wrong OCR figures must NOT have entered the analysis dict.
    assert doc.get("total_assets") is None
    assert doc.get("current_assets") is None
    assert "report_rejected_unreliable_ocr" in doc.get("_source", "")
    # The authoritative summary income statement is still present.
    assert doc.get("revenue") == 80_000_000


def test_reconciling_ocr_balance_sheet_IS_merged(monkeypatch):
    # Same shape, but the balance sheet reconciles -> trusted, so it fills the gap.
    good_report = {"revenue": 80_000_000, "net_income": 15_000_000,
                   "total_assets": 350_000_000, "total_liabilities": 200_000_000,
                   "shareholders_equity": 150_000_000, "_ocr": True, "_balance_ok": True}
    _stub_sources(monkeypatch, good_report)
    doc, _ = pf.fetch_psx_doc_financials("TEST")
    assert doc.get("total_assets") == 350_000_000
