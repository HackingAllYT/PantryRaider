"""The Inventory Edit window in a real browser: it opens with the clicked row's
own best-by date and sends a date only when it was changed.

A product kept in two places has a row for each (#73), and each row has its
own best-by date. The window used to take its values from whichever of the
product's rows was stored last, so the fridge row's Edit showed the freezer
row's date. Save also sent the date the window opened with when only the
category changed, and the edit writes a date onto every stock entry of the
product, so the product's other dates were lost (Grocy then merges the rows
for good).

The dashboard answers with canned rows here (butter in the fridge and in the
freezer, each with its own date), and the edit request is caught and answered
in the test, so nothing reaches the mock Grocy the whole session shares.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.browser

PRODUCT_ID = 42  # not one of the mock kitchen's products


def _d(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


FRIDGE_BEST_BY = _d(3)
FREEZER_BEST_BY = _d(90)

_BUTTER = {
    "product_id": PRODUCT_ID, "name": "Butter", "unit": "pack",
    "category": "Dairy", "product_group_id": 2, "product_group_name": "Dairy",
    "added_date": None, "product_amount": 4.0, "amount_opened": 0.0,
}
# One pack in the fridge, due in a few days, and three in the freezer, due
# months later. The freezer panel comes after the fridge's, so a lookup by
# product alone finds the freezer row.
DASHBOARD = {
    "refrigerated": [{**_BUTTER, "amount": 1.0, "best_before_date": FRIDGE_BEST_BY,
                      "days_remaining": 3, "urgency": "3d", "location_id": 1,
                      "location_name": "Refrigerator", "storage_bucket": "refrigerated"}],
    "frozen": [{**_BUTTER, "amount": 3.0, "best_before_date": FREEZER_BEST_BY,
                "days_remaining": 90, "urgency": "ok", "location_id": 2,
                "location_name": "Freezer", "storage_bucket": "frozen"}],
    "_product_groups": [{"id": 2, "name": "Dairy"}],
}


@pytest.fixture
def inventory_page(browser, app_server):
    """The Inventory page showing the canned rows. Each edit request lands in
    ``page.edits`` as (url, body) and is answered here."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    try:
        page = ctx.new_page()
        page.edits = []

        def edit(route):
            page.edits.append((route.request.url, route.request.post_data_json))
            route.fulfill(json={"product_id": PRODUCT_ID})

        page.route("**/inventory/dashboard**", lambda route: route.fulfill(json=DASHBOARD))
        page.route("**/inventory/edit/**", edit)
        page.goto(app_server + "/ui/inventory", wait_until="load")
        page.wait_for_selector("#panel-frozen .item-row")
        yield page
    finally:
        ctx.close()


def _open_edit(page, bucket):
    page.click(f'#panel-{bucket} button[aria-label="Edit Butter"]')
    page.wait_for_selector("#editModal.show")


def _save(page):
    page.click("#editSaveBtn")
    page.wait_for_function("() => document.querySelector('.toast.show')")
    assert "Item updated" in page.inner_text("#toastMsg")
    assert len(page.edits) == 1, page.edits
    url, body = page.edits[0]
    assert url.endswith(f"/inventory/edit/{PRODUCT_ID}"), url
    return body


def test_edit_opens_with_the_clicked_rows_own_best_by_date(inventory_page):
    page = inventory_page
    _open_edit(page, "refrigerated")
    assert page.input_value("#editBestBy") == FRIDGE_BEST_BY
    page.locator("#editModal").get_by_role("button", name="Cancel").click()
    page.wait_for_selector(".modal-backdrop", state="detached")

    _open_edit(page, "frozen")
    assert page.input_value("#editBestBy") == FREEZER_BEST_BY


def test_a_category_change_alone_sends_no_best_by_date(inventory_page):
    page = inventory_page
    _open_edit(page, "refrigerated")
    page.select_option("#editCategory", "Snacks")
    body = _save(page)
    # Null leaves every date as it is, so the freezer packs keep theirs. The
    # row names its place and its one date, so a changed date would reach the
    # fridge pack alone.
    assert body == {"category": "Snacks", "best_before_date": None,
                    "from_location_id": 1, "from_best_before_date": FRIDGE_BEST_BY}


def test_a_changed_best_by_date_is_sent(inventory_page):
    page = inventory_page
    new_date = _d(5)
    _open_edit(page, "refrigerated")
    page.fill("#editBestBy", new_date)
    body = _save(page)
    assert body == {"category": "Dairy", "best_before_date": new_date,
                    "from_location_id": 1, "from_best_before_date": FRIDGE_BEST_BY}


def test_save_can_be_tried_again_after_it_failed(inventory_page):
    page = inventory_page
    answers = [(500, {"detail": "Grocy is busy"}), (200, {"product_id": PRODUCT_ID})]

    def edit(route):
        page.edits.append((route.request.url, route.request.post_data_json))
        status, body = answers.pop(0)
        route.fulfill(status=status, json=body)

    page.unroute("**/inventory/edit/**")
    page.route("**/inventory/edit/**", edit)
    _open_edit(page, "refrigerated")
    page.select_option("#editCategory", "Snacks")
    page.click("#editSaveBtn")
    page.wait_for_function(
        "() => document.getElementById('toastMsg').textContent.includes('Save failed')")
    # The window stays open on its row, and the second Save goes out.
    assert page.is_visible("#editModal.show")
    page.click("#editSaveBtn")
    page.wait_for_function(
        "() => document.getElementById('toastMsg').textContent.includes('Item updated')")
    assert [body for _url, body in page.edits] == \
        [{"category": "Snacks", "best_before_date": None,
          "from_location_id": 1, "from_best_before_date": FRIDGE_BEST_BY}] * 2
