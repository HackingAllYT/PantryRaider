"""The line of best-by dates under an Inventory row's name, in a real browser.

A product's stock in one place is one row with the earliest best-by date. A
row whose stock has more than one date lists them under the name, earliest
first, such as "2 by Oct 24 · 8 by Nov 20": three at most, then how many more,
with every date in the title. Like the location above it, the line keeps to
one line and is cut short, except on a phone, where it may wrap; at neither
width may it push the page sideways. Stock that never expires says No expiry
on its badge instead of a day count of several hundred thousand.

The dashboard answers with canned rows here, and the page runs in en-US with
today fixed at Oct 21, 2026, so the dates read the same on any machine and on
any day.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.browser

NOW = "2026-10-21T12:00:00Z"

PHONE = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}
# Two panels side by side, each with a narrow name column: the width where a
# wrapping line made rows several lines tall.
TABLET = {"viewport": {"width": 1024, "height": 768}, "has_touch": True}
DESKTOP = {"viewport": {"width": 1280, "height": 800}}

_ROW = {"unit": None, "category": "Dairy", "product_group_id": 2,
        "product_group_name": "Dairy", "added_date": None, "amount_opened": 0.0,
        "location_id": 1, "location_name": "Kitchen Fridge",
        "storage_bucket": "refrigerated"}


def _dates(*pairs):
    return [{"best_before_date": d, "amount": a} for d, a in pairs]


DASHBOARD = {
    "refrigerated": [
        {**_ROW, "product_id": 41, "name": "Coconut Yogurt", "amount": 10.0,
         "product_amount": 10.0, "best_before_date": "2026-10-24",
         "days_remaining": 3, "urgency": "3d",
         "best_by_dates": _dates(("2026-10-24", 2.0), ("2026-11-20", 8.0))},
        # Five dates, one of them more than 300 days away and some stock with
        # no date at all.
        {**_ROW, "product_id": 42, "name": "Greek Yogurt", "amount": 5.5,
         "product_amount": 5.5, "best_before_date": "2026-10-23",
         "days_remaining": 2, "urgency": "3d",
         "best_by_dates": _dates(("2026-10-23", 1.0), ("2026-10-30", 1.0),
                                 ("2026-11-06", 2.0), ("2027-12-01", 1.0),
                                 (None, 0.5))},
        # One date, which the row's badge already shows.
        {**_ROW, "product_id": 43, "name": "Butter", "amount": 1.0,
         "product_amount": 1.0, "best_before_date": "2026-10-31",
         "days_remaining": 10, "urgency": "ok"},
    ],
    "pantry": [
        {**_ROW, "product_id": 44, "name": "Honey", "amount": 3.0,
         "product_amount": 3.0, "best_before_date": "2026-11-01",
         "days_remaining": 11, "urgency": "ok", "location_id": 4,
         "location_name": "Pantry", "storage_bucket": "pantry",
         "best_by_dates": _dates(("2026-11-01", 1.0), ("2999-12-31", 2.0))},
        {**_ROW, "product_id": 45, "name": "Salt", "amount": 1.0,
         "product_amount": 1.0, "best_before_date": "2999-12-31",
         "days_remaining": 355452, "urgency": "ok", "location_id": 4,
         "location_name": "Pantry", "storage_bucket": "pantry"},
    ],
    "_product_groups": [{"id": 2, "name": "Dairy"}],
}

# The line as it reads on screen, and as a screen reader hears it.
_SEEN = """el => { const c = el.cloneNode(true);
  c.querySelectorAll('.visually-hidden').forEach(e => e.remove());
  return c.textContent; }"""
_HEARD = """el => { const c = el.cloneNode(true);
  c.querySelectorAll('[aria-hidden="true"]').forEach(e => e.remove());
  return c.textContent; }"""


@pytest.fixture
def inventory(browser, app_server):
    """``inventory(**context_options)`` opens the Inventory page on the canned
    rows. The page keeps the dashboard URLs it asked for in ``page.asked`` and
    any script error in ``page.errors``."""
    contexts = []

    def make(**options):
        ctx = browser.new_context(locale="en-US", timezone_id="UTC", **options)
        contexts.append(ctx)
        page = ctx.new_page()
        page.clock.set_fixed_time(NOW)
        page.asked, page.errors = [], []
        page.on("pageerror", lambda e: page.errors.append(str(e)))

        def dashboard(route):
            page.asked.append(route.request.url)
            route.fulfill(json=DASHBOARD)

        page.route("**/inventory/dashboard**", dashboard)
        page.goto(app_server + "/ui/inventory", wait_until="load")
        page.wait_for_selector("#panel-pantry .item-row")
        return page

    yield make
    for ctx in contexts:
        ctx.close()


def _line(page, name):
    return page.locator(f'.item-row[data-name="{name}"] .item-name .item-dates')


def test_a_row_lists_its_best_by_dates_earliest_first(inventory):
    page = inventory(**DESKTOP)
    assert page.asked and all(
        "/inventory/dashboard?sort=expiry_asc&dates=breakdown" in url for url in page.asked)
    line = _line(page, "Coconut Yogurt")
    assert line.evaluate(_SEEN) == "2 by Oct 24 · 8 by Nov 20"
    assert line.evaluate(_HEARD) == "Best-by dates: 2 by Oct 24, 8 by Nov 20"
    assert line.get_attribute("title") == "2 by Oct 24 · 8 by Nov 20"
    assert _line(page, "Honey").evaluate(_SEEN) == "1 by Nov 1 · 2 with no expiry"
    assert _line(page, "Butter").count() == 0
    assert not page.errors


def test_past_three_dates_the_line_counts_the_rest(inventory):
    page = inventory(**DESKTOP)
    line = _line(page, "Greek Yogurt")
    assert line.evaluate(_SEEN) == "1 by Oct 23 · 1 by Oct 30 · 2 by Nov 6 +2 more"
    # The title has every date, with the year on the one more than 300 days
    # away.
    assert line.get_attribute("title") == (
        "1 by Oct 23 · 1 by Oct 30 · 2 by Nov 6 · 1 by Dec 1, 2027 · 0.5 with no date")


def test_stock_that_never_expires_says_so_on_its_badge(inventory):
    page = inventory(**DESKTOP)
    badge = page.locator('.item-row[data-name="Salt"] .expiry-badge')
    assert badge.inner_text() == "No expiry"
    assert "badge-unknown" in badge.get_attribute("class")


@pytest.mark.parametrize("device", [PHONE, TABLET], ids=["phone", "tablet"])
def test_the_line_never_pushes_the_page_sideways(inventory, device):
    page = inventory(**device)
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    shape = _line(page, "Greek Yogurt").evaluate("""el => {
        const s = getComputedStyle(el);
        return {wrap: s.whiteSpace, height: el.getBoundingClientRect().height,
                lineHeight: parseFloat(s.lineHeight), cut: el.scrollWidth > el.clientWidth}; }""")
    if device is PHONE:
        # The buttons have a line of their own there, so the dates may wrap
        # instead of being cut short.
        assert shape["wrap"] == "normal" and not shape["cut"], shape
    else:
        # One line, like the location above it, so the row stays short.
        assert shape["wrap"] == "nowrap", shape
        assert shape["height"] < 1.5 * shape["lineHeight"], shape
    assert not page.errors
