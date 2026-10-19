"""Inventory row actions in a real browser: the Use one button sends one unit
from the row's own Grocy location, the same place the row's amount comes from."""
from __future__ import annotations


def test_use_one_takes_a_unit_from_the_rows_location(new_page):
    page = new_page("/ui/inventory")
    sent = []

    def capture(route):
        sent.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body="[]")

    page.route("**/inventory/consume/**", capture)
    button = page.locator('button[aria-label="Use one Chicken Breast"]')
    button.wait_for()
    button.click()
    page.wait_for_function("() => document.querySelector('.toast.show')")
    assert len(sent) == 1, sent
    assert "/inventory/consume/1?amount=1&location_id=1" in sent[0]
