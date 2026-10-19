"""Inventory search and category filter in a real browser (FoodAssistant-7xddm,
GitHub #124).

The mock kitchen (tests/browser/_mockstack.py) stocks the Refrigerator,
Freezer, Counter and Pantry, every item in a product group, plus one custom
group with nothing in it. Filtering hides rows in place: the panels stay put,
their counts read "N of M", and a dashboard reload (a sort change) keeps the
filter.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.browser

VISIBLE_ROWS = """bucket => [...document.querySelectorAll(`#panel-${bucket} .item-row`)]
    .filter(r => r.offsetParent).map(r => r.dataset.name)"""


def _ready(page):
    page.wait_for_selector("#panel-pantry .item-row", state="attached")
    page.wait_for_function("document.querySelectorAll('#invCategory option').length > 1")


def _visible(page, bucket):
    return page.evaluate(VISIBLE_ROWS, bucket)


def _count(page, bucket):
    return page.inner_text(f"#count-{bucket}").strip()


def test_typing_filters_rows_across_every_panel(new_page):
    page = new_page("/ui/inventory")
    _ready(page)
    page.fill("#invSearch", "an")
    assert _visible(page, "room_temp") == ["Bananas"]
    assert sorted(_visible(page, "pantry")) == ["Black Beans", "Canned Tomatoes"]
    assert _visible(page, "refrigerated") == []
    assert _count(page, "room_temp") == "1 of 2"
    assert _count(page, "pantry") == "2 of 5"
    assert _count(page, "refrigerated") == "0 of 6"
    # A panel with no matches stays in place with one short line.
    assert page.is_visible("#panel-refrigerated .panel-nomatch")
    assert page.is_visible("#col-refrigerated")
    assert not page.is_visible("#invNoMatch")
    # Case and accents do not matter.
    page.fill("#invSearch", "BANÁNAS")
    assert _visible(page, "room_temp") == ["Bananas"]


def test_clear_button_and_escape_bring_every_row_back(new_page):
    page = new_page("/ui/inventory")
    _ready(page)
    assert not page.is_visible("#invSearchClear")
    page.fill("#invSearch", "shrimp")
    assert _visible(page, "frozen") == ["Shrimp"]
    page.click("#invSearchClear")
    assert page.input_value("#invSearch") == ""
    assert len(_visible(page, "frozen")) == 3
    assert _count(page, "frozen") == "3"
    assert not page.is_visible("#invSearchClear")
    assert page.locator("#panel-frozen .panel-nomatch").count() == 0

    page.fill("#invSearch", "shrimp")
    page.press("#invSearch", "Escape")
    assert page.input_value("#invSearch") == ""
    assert len(_visible(page, "refrigerated")) == 6


def test_category_lists_every_group_and_combines_with_search(new_page):
    page = new_page("/ui/inventory")
    _ready(page)
    options = page.eval_on_selector_all("#invCategory option", "os => os.map(o => o.textContent)")
    assert options[0] == "All categories"
    assert "Holiday Baking" in options          # a custom group, nothing stocked
    assert "No category" not in options        # every stocked item has a group

    page.select_option("#invCategory", label="Produce")
    assert _visible(page, "refrigerated") == ["Baby Spinach"]
    assert _visible(page, "frozen") == ["Frozen Corn"]
    assert sorted(_visible(page, "room_temp")) == ["Avocados", "Bananas"]
    page.fill("#invSearch", "ban")
    assert _visible(page, "room_temp") == ["Bananas"]
    assert _visible(page, "frozen") == []

    # Nothing matches anywhere: one message, with a way out.
    page.select_option("#invCategory", label="Dairy")
    assert page.is_visible("#invNoMatch")
    assert "No matching items" in page.inner_text("#invNoMatch")
    page.click("#invClearFilters")
    assert not page.is_visible("#invNoMatch")
    assert page.input_value("#invSearch") == ""
    assert page.input_value("#invCategory") == ""
    assert len(_visible(page, "pantry")) == 5


def test_filter_survives_a_sort_change_and_category_is_remembered(new_page):
    page = new_page("/ui/inventory")
    _ready(page)
    page.select_option("#invCategory", label="Canned")
    page.fill("#invSearch", "bean")
    assert _visible(page, "pantry") == ["Black Beans"]
    with page.expect_response(lambda r: "inventory/dashboard?sort=name_asc" in r.url):
        page.select_option("#sortSelect", "name_asc")
    page.wait_for_timeout(200)
    assert page.input_value("#invSearch") == "bean"
    assert page.input_value("#invCategory") != ""
    assert _visible(page, "pantry") == ["Black Beans"]
    assert _count(page, "pantry") == "1 of 5"

    # A fresh visit keeps the category, not the search text.
    page.reload(wait_until="load")
    _ready(page)
    assert page.input_value("#invSearch") == ""
    assert page.eval_on_selector("#invCategory", "s => s.selectedOptions[0].textContent") == "Canned"
    assert sorted(_visible(page, "pantry")) == ["Black Beans", "Canned Tomatoes"]
    page.evaluate("localStorage.removeItem('inv_category')")


def test_filter_fits_and_types_on_a_kitchen_screen(new_page):
    page = new_page("/ui/inventory", kiosk=True, viewport=(800, 480))
    _ready(page)
    for sel in ("#invSearch", "#invCategory"):
        assert page.eval_on_selector(sel, "e => e.getBoundingClientRect().height") >= 44
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")

    # The on-screen keyboard types into the box and the rows follow.
    page.click("#invSearch")
    page.wait_for_selector("#pr-osk.pr-osk-open", timeout=5000)
    for ch in ("e", "g", "g"):
        page.locator(f'#pr-osk button[data-osk-char="{ch}"]').click()
    assert page.input_value("#invSearch") == "egg"
    assert _visible(page, "refrigerated") == ["Eggs"]
    clear = page.locator("#invSearchClear")
    assert clear.bounding_box()["height"] >= 44


def test_filter_fits_a_phone(new_page):
    page = new_page("/ui/inventory", viewport=(375, 740))
    _ready(page)
    page.fill("#invSearch", "rice")
    assert _visible(page, "pantry") == ["Jasmine Rice"]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


def test_edit_dialog_offers_every_grocy_group(new_page):
    # GitHub #124: a custom group the filter lists must be choosable in Edit too.
    page = new_page("/ui/inventory")
    _ready(page)
    page.click("#panel-pantry .item-row [data-on-click^='openEditModal']")
    page.wait_for_selector("#editModal.show")
    options = page.eval_on_selector_all("#editCategory option", "os => os.map(o => o.textContent)")
    assert options[0] == "(unchanged)"
    assert "Holiday Baking" in options          # custom group, nothing stocked
    assert "Poultry" in options                 # built-ins stay on offer
    lowered = [o.lower() for o in options]
    assert len(lowered) == len(set(lowered))    # no name twice
