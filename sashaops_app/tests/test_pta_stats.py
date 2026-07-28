"""
Regression tests for the PTA mobile-subscriber scraper.

The previous implementation searched page text for a subscriber number near
keywords - the real figures are embedded as Highcharts JS chart configs (JSON
literals in inline <script> blocks), not plain text or an HTML <table>. These
tests lock in extracting the right chart's data, scoped correctly among the many
charts the page embeds.
"""

from intelligence.gov_scrapers.pta_stats import _extract_latest_total, _period_from_label

# A trimmed but structurally real excerpt: the target chart's categories + a
# "Total" series, followed by a DIFFERENT chart (Annual) with its own "Total"
# series that must NOT be picked up instead.
_HTML = """
<script>
title: {"text": "Monthly Mobile Cellular Subscribers"},
xAxis: {"type":"category","categories":["Jul-25","Aug-25","Sep-25","Oct-25","Nov-25","Dec-25","Jan-26","Feb-26","Mar-26","Apr-26","May-26","Jun-26"]},
series: [{"name":"Jazz","data":[73370431,73028300,72582384,72649532,72915202,73861653,74191312,74905106,74858177,75081575,75021742,75421212]},
{"name":"Total","data":[197514683,196860398,196194645,196153769,196584913,200555485,202575669,204771173,204755596,205689662,206008479,208057255]}],
Highcharts.chart("monthly-cellular-subscribers-chart", {});
</script>
<script>
title: {"text": "Annual Mobile Cellular Subscribers"},
xAxis: {"type":"category","categories":["2021-22","2022-23","2023-24","2024-25"]},
series: [{"name":"Total","data":[1,2,3,999999999]}],
Highcharts.chart("annual-cellular-subscribers-chart", {});
</script>
"""


def test_latest_total_and_period_extracted():
    result = _extract_latest_total(_HTML)
    assert result is not None
    assert result["subscribers"] == 208057255
    assert result["period_label"] == "Jun-26"
    assert result["period"] == "2026-06"


def test_annual_chart_total_is_not_picked_up_instead():
    # The bogus 999999999 belongs to the Annual chart's Total, not Monthly's.
    result = _extract_latest_total(_HTML)
    assert result["subscribers"] != 999999999


def test_period_label_parsing():
    assert _period_from_label("Jun-26") == "2026-06"
    assert _period_from_label("Jan-25") == "2025-01"


def test_period_label_parsing_rejects_garbage():
    assert _period_from_label("not a period") is None


def test_missing_chart_title_returns_none():
    assert _extract_latest_total("<html>no relevant chart here</html>") is None


def test_mismatched_categories_and_data_length_returns_none():
    bad_html = (
        'title: {"text": "Monthly Mobile Cellular Subscribers"},'
        'xAxis: {"categories":["Jun-26","Jul-26"]},'
        'series: [{"name":"Total","data":[1]}],'
        'Highcharts.chart("x", {});'
    )
    assert _extract_latest_total(bad_html) is None
