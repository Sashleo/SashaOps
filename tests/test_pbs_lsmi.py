"""
Regression tests for the PBS LSM (QIM) + CPI scraper.

The previous implementation had no CPI extraction at all (despite
database/macro_data.py's get_macro_summary_text already expecting a "cpi"
indicator) and searched free HTML text for an LSM growth number that only ever
lives in downloadable monthly Excel/PDF releases. These tests lock in the real
month/year selection logic and CPI parsing.
"""

import re

from intelligence.gov_scrapers.pbs_lsmi import _latest_link, _month_num, _MONTH_YEAR_RE

_TABLE1_PAT = re.compile(r"^Table\s*1\b", re.I)
_CPI_PAT = re.compile(r"consumer price index.*inflation", re.I)


def test_latest_table1_link_picked_by_date_not_page_order():
    # Real titles from pbs.gov.pk/industry-2/; deliberately out of date order to
    # prove selection is date-based, not "first in the DOM".
    links = [
        ("Table 1 Feb-26", "https://pbs.gov.pk/uploads/Table-1-Feb-2026.xlsx"),
        ("Table 1 May-26", "https://pbs.gov.pk/uploads/Table-1-May-2026.xlsx"),
        ("Table 1 Jan-26", "https://pbs.gov.pk/uploads/Table-1-Jan-2026.xlsx"),
        ("Table 2 May 2026", "https://pbs.gov.pk/uploads/Table-2-may-2026.xlsx"),  # wrong table
    ]
    best = _latest_link(links, _TABLE1_PAT, ".xlsx")
    assert best is not None
    assert "May" in best[0]
    assert best[2:] == (2026, 5)


def test_latest_cpi_release_picked_by_date():
    links = [
        ("Press Release on Consumer Price Index (CPI) Inflation for the Month of May, 2026",
         "https://pbs.gov.pk/uploads/Press-Release-May-2026.pdf"),
        ("Press Release on Consumer Price Index (CPI) Inflation for the Month of June, 2026",
         "https://pbs.gov.pk/uploads/Press-Release-June-2026.pdf"),
        ("PRESS RELEASE (2nd April 2026) NATIONAL ACCOUNTS COMMITTEE MEETING",
         "https://pbs.gov.pk/uploads/Press-release-for-116th-NAC-Meeting.pdf"),  # wrong doc type
    ]
    best = _latest_link(links, _CPI_PAT, ".pdf")
    assert best is not None
    assert "June" in best[0]
    assert best[2:] == (2026, 6)


def test_pbs_typo_febuary_is_handled():
    # A real filename on pbs.gov.pk: "Press-Release-Febuary-2026.pdf" (missing the
    # first "r"). Must still parse as February, not silently fail to match.
    m = _MONTH_YEAR_RE.search("Press-Release-Febuary-2026.pdf")
    assert m is not None
    assert _month_num(m.group(1)) == 2


def test_month_num_handles_standard_names():
    assert _month_num("May") == 5
    assert _month_num("june") == 6


def test_no_match_returns_none_not_a_guess():
    links = [("Some unrelated report", "https://pbs.gov.pk/uploads/other.xlsx")]
    assert _latest_link(links, _TABLE1_PAT, ".xlsx") is None
