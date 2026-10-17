"""
Regression tests for the FBR revenue scraper's PDF table parsing.

The previous implementation scraped fbr.gov.pk/Newsroom/ for a keyword-adjacent
number - that page is a news/nav homepage with no collection figures on it at all,
so it could never have worked. These tests lock in the real source: the
"MONTH WISE / TAX WISE NET COLLECTION DURING FY <year>" PDF table.
"""

import re

from intelligence.gov_scrapers.fbr_revenue import _MONTH_ROW, _TOTAL_ROW, _FY_TITLE, _last_number

# A real excerpt of the FY2024-25 PDF text (verified against the live source
# 2026-07-28: https://download1.fbr.gov.pk/Docs/.../MONTHWISE-TAXWISENETCOLLECTIONDURINGFY2024-25.pdf)
_SAMPLE_TEXT = """MONTH WISE / TAX WISE NET COLLECTION DURING FY: 2024-25
(Rs. in Billion)
MONTHS DT ST FED CUS TOTAL
JULY 284.1 256.4 37.4 81.9 659.8
AUGUST 332.1 315.5 58.2 90.5 796.3
SEPTEMBER 614.0 332.9 55.4 104.5 1,106.8
Quarter-1 1,230.2 904.8 151.0 276.9 2,562.9
APRIL 351.5 313.3 66.4 115.9 847.1
MAY 421.5 329.4 69.5 113.3 933.7
JUNE 890.9 397.9 93.7 127.9 1,510.4
Quarter-4 1,663.9 1,040.6 229.6 357.1 3,291.2
Total 5,791.7 3,901.4 766.6 1,284.6 11,744.3
(*) The Collection for FY 2024-25 is provisional subject to reconciliation."""


def test_fy_title_extracted():
    m = _FY_TITLE.search(_SAMPLE_TEXT)
    assert m is not None
    assert m.group(1) == "2024-25"


def test_last_reported_month_is_the_last_month_row_not_a_quarter():
    # Walk the lines the way the scraper does: last MONTH match wins, Quarter/Total
    # rows must never overwrite it.
    latest_month, latest_value = None, None
    for line in _SAMPLE_TEXT.split("\n"):
        m = _MONTH_ROW.match(line.strip())
        if m:
            latest_month = m.group(1).upper()
            latest_value = _last_number(m.group(2))
    assert latest_month == "JUNE"
    assert latest_value == 1510.4


def test_fy_total_is_the_total_row_rightmost_column():
    fy_total = None
    for line in _SAMPLE_TEXT.split("\n"):
        t = _TOTAL_ROW.match(line.strip())
        if t:
            fy_total = _last_number(t.group(1))
    assert fy_total == 11_744.3


def test_quarter_row_is_not_matched_as_a_month():
    # "Quarter-1 1,230.2 904.8 151.0 276.9 2,562.9" must not match _MONTH_ROW.
    assert _MONTH_ROW.match("Quarter-1 1,230.2 904.8 151.0 276.9 2,562.9") is None


def test_last_number_takes_rightmost_column_not_first():
    # The TOTAL column is always rightmost; taking the first number would grab the
    # direct-tax (DT) column instead of the total collection figure.
    assert _last_number("284.1 256.4 37.4 81.9 659.8") == 659.8
