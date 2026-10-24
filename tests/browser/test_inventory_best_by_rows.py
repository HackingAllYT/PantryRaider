"""Rows of one best-by date on the Inventory page, in a real browser.

With One row per best-by date on, the default, a place that holds a product
with more than one best-by date gets a row for each date (#73), and every
button on such a row acts on that date's stock alone: Use one, Mark all
consumed and Mark one opened name the date in their request, Edit and Move
send it in their body, and a label printed from the row carries that date, its
added date and its own stock entry. The buttons' names, the question before
Mark all consumed and the Edit and Move windows all say which date they are
for. A place with a single date keeps one plain row, whose buttons ask what
they always have, apart from Edit: it names the row's place and date, so a
changed date leaves the product's other places alone.

The Move window can also take part of a row (#136). Its Amount box starts at
the whole row, so tapping a place straight away moves everything, as dragging
does, and only a smaller amount goes in the body. A row of one has nothing to
split and shows no box.

Grocy cannot always act on one date alone: it keeps some stock together under
one stock id and takes an opened or the soonest one first. The server then
refuses with its reason (409), or says the row's stock is gone (404), and the
page shows why and reads the list again; an action Grocy could only do in part
says how much went.

The dashboard answers with canned rows here, every row action is caught and
answered in the test, so nothing reaches the mock Grocy the whole session
shares, and label printing is turned on in the page the server renders. The
page runs in en-US with today fixed at Oct 21, 2026.
"""
from __future__ import annotations

import copy
from urllib.parse import urlsplit

import pytest

pytestmark = pytest.mark.browser

NOW = "2026-10-21T12:00:00Z"

DESKTOP = {"viewport": {"width": 1280, "height": 900}}
PHONE = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}
KITCHEN_SCREEN = {"viewport": {"width": 800, "height": 480}, "has_touch": True, "kiosk": True}

_YOGURT = {"product_id": 41, "name": "Coconut Yogurt", "unit": "cup", "category": "Dairy",
           "product_group_id": 2, "product_group_name": "Dairy", "product_amount": 10.0,
           "amount_opened": 0.0, "location_id": 1, "location_name": "Kitchen Fridge",
           "storage_bucket": "refrigerated", "by_date": True}
_HONEY = {"product_id": 44, "name": "Honey", "unit": None, "category": "Condiments",
          "product_group_id": 8, "product_group_name": "Condiments", "product_amount": 3.0,
          "amount_opened": 0.0, "location_id": 4, "location_name": "Pantry / Dry Storage",
          "storage_bucket": "pantry", "by_date": True}

DASHBOARD = {
    "refrigerated": [
        # Two yogurts due in 3 days and a new pack of 8 due in a month, in one
        # fridge: a row for each date.
        {**_YOGURT, "amount": 2.0, "best_before_date": "2026-10-24", "days_remaining": 3,
         "urgency": "3d", "added_date": "2026-10-01 08:00:00", "stock_entry_id": "6a1f0c2b9d3e4"},
        # Every pack of butter in the fridge has the same date: one plain row.
        {"product_id": 43, "name": "Butter", "unit": "pack", "category": "Dairy",
         "product_group_id": 2, "product_group_name": "Dairy", "amount": 1.0,
         "product_amount": 1.0, "amount_opened": 0.0, "best_before_date": "2026-10-31",
         "days_remaining": 10, "urgency": "ok", "added_date": None, "location_id": 1,
         "location_name": "Kitchen Fridge", "storage_bucket": "refrigerated"},
        {**_YOGURT, "amount": 8.0, "best_before_date": "2026-11-20", "days_remaining": 30,
         "urgency": "ok", "added_date": "2026-10-18 09:30:00", "stock_entry_id": "6a1f0c2b9d3f7"},
    ],
    "pantry": [
        # Honey that never expires, and a jar with no date at all.
        {**_HONEY, "amount": 2.0, "best_before_date": "2999-12-31", "days_remaining": 355452,
         "urgency": "ok", "added_date": "2026-09-02 10:00:00", "stock_entry_id": "6a1f0c2b9d401"},
        {**_HONEY, "amount": 1.0, "best_before_date": None, "days_remaining": None,
         "urgency": "unknown", "added_date": None},
    ],
    "_product_groups": [{"id": 2, "name": "Dairy"}, {"id": 8, "name": "Condiments"}],
}

# How the server answers each row action unless a test says otherwise.
_DONE = {
    "consume": (200, {"product_id": 41, "consumed": 1.0, "requested": 1.0, "complete": True}),
    "open": (200, {"product_id": 41, "opened": 1.0, "new_best_by": None, "complete": True}),
    "edit": (200, {"product_id": 41, "updated": 1}),
    "move": (200, {"product_id": 41, "moved_amount": 2.0, "location_id": 2,
                   "best_by_updates": [], "new_best_by": None, "best_by_change": None,
                   "left_behind": 0}),
    "label": (200, {"ok": True}),
}

_BUTTON_NAMES = """row => [...row.querySelectorAll('.item-actions button')]
  .map(b => b.getAttribute('aria-label'))"""


@pytest.fixture
def inventory(browser, app_server):
    """``inventory(answers=None, kiosk=False, **context_options)`` opens the
    Inventory page on the canned rows. Each row action lands in ``page.sent``
    as (path with query, JSON body) and is answered from ``answers`` (action
    name to (status, body)) or as done. ``page.asked`` keeps the dashboard
    URLs and ``page.errors`` any script error."""
    contexts = []

    def make(answers=None, kiosk=False, **options):
        ctx = browser.new_context(locale="en-US", timezone_id="UTC", **options)
        contexts.append(ctx)
        if kiosk:
            ctx.add_init_script(
                "try { localStorage.setItem('kioskMode', 'true'); } catch (e) {}")
        page = ctx.new_page()
        page.clock.set_fixed_time(NOW)
        page.asked, page.sent, page.errors = [], [], []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        replies = {**_DONE, **(answers or {})}

        def with_printing(route):
            # The page as the server renders it, with label printing on so
            # every row has its Print label button.
            response = route.fetch()
            html = response.text().replace('"printing_enabled": false',
                                           '"printing_enabled": true')
            headers = {k: v for k, v in response.headers.items()
                       if k.lower() not in ("content-length", "content-encoding")}
            route.fulfill(status=response.status, headers=headers, body=html)

        def dashboard(route):
            page.asked.append(route.request.url)
            route.fulfill(json=DASHBOARD)

        def action(route):
            url = urlsplit(route.request.url)
            page.sent.append((f"{url.path}?{url.query}" if url.query else url.path,
                              route.request.post_data_json))
            status, body = replies[url.path.split("/")[2]]
            route.fulfill(status=status, json=body)

        page.route("**/ui/inventory", with_printing)
        page.route("**/inventory/dashboard**", dashboard)
        for pattern in ("**/inventory/consume/**", "**/inventory/open/**",
                        "**/inventory/edit/**", "**/inventory/move/**",
                        "**/printing/label"):
            page.route(pattern, action)
        page.goto(app_server + "/ui/inventory", wait_until="load")
        page.wait_for_selector("#panel-pantry .item-row")
        return page

    yield make
    for ctx in contexts:
        ctx.close()


def _button(page, name):
    return page.locator(f'button[aria-label="{name}"]')


def _then_reload(page, act):
    """Runs ``act`` and waits until the page has read the list again."""
    with page.expect_response("**/inventory/dashboard**"):
        act()


def _confirm(page, button_name):
    """Presses a Mark all consumed button and returns its question, still open."""
    _button(page, button_name).click()
    dialog = page.locator(".pr-dialog-wrap .modal")
    dialog.wait_for()
    return dialog


def _toast(page):
    return page.inner_text("#toastMsg"), "text-bg-danger" in page.get_attribute(
        "#consumeToast", "class")


def test_each_best_by_date_in_a_place_gets_a_row_of_its_own(inventory):
    page = inventory(**DESKTOP)
    assert page.asked and all(
        "/inventory/dashboard?sort=expiry_asc&dates=auto" in url for url in page.asked)
    rows = page.eval_on_selector_all(
        ".item-row", "rows => rows.map(r => [r.id, r.dataset.bestBy || null])")
    assert rows == [
        ["row-41-1-2026-10-24", "2026-10-24"],
        ["row-43-1", None],
        ["row-41-1-2026-11-20", "2026-11-20"],
        ["row-44-4-2999-12-31", "2999-12-31"],
        ["row-44-4-none", "none"],
    ]
    # A row of one date says how much of the product there is in all.
    assert page.get_attribute("#row-41-1-2026-10-24 .item-qty", "title") == "10 in all"
    assert page.get_attribute("#row-43-1 .item-qty", "title") is None
    assert not page.errors


def test_the_buttons_on_a_row_of_one_date_name_it(inventory):
    page = inventory(**DESKTOP)
    assert page.eval_on_selector("#row-41-1-2026-10-24", _BUTTON_NAMES) == [
        "Edit Coconut Yogurt, best by Oct 24",
        "Move Coconut Yogurt, best by Oct 24, to another location",
        "Mark one Coconut Yogurt, best by Oct 24, opened",
        "Use one Coconut Yogurt, best by Oct 24",
        "Mark Coconut Yogurt, best by Oct 24, consumed",
        "Print a label for Coconut Yogurt, best by Oct 24",
    ]
    # The tooltips that name the item name its date too.
    assert page.eval_on_selector_all(
        "#row-41-1-2026-10-24 .item-actions button[title*='Coconut']",
        "bs => bs.map(b => b.title)") == [
        "Mark one Coconut Yogurt, best by Oct 24, opened",
        "Use one Coconut Yogurt, best by Oct 24",
        "Mark all Coconut Yogurt, best by Oct 24, consumed",
    ]
    assert page.eval_on_selector("#row-44-4-2999-12-31", _BUTTON_NAMES)[3] == \
        "Use one Honey, that never expires"
    assert page.eval_on_selector("#row-44-4-none", _BUTTON_NAMES)[3] == \
        "Use one Honey, with no best-by date"
    # A plain row keeps its names, and Mark one opened has one too now.
    assert page.eval_on_selector("#row-43-1", _BUTTON_NAMES) == [
        "Edit Butter", "Move Butter to another location", "Mark one Butter opened",
        "Use one Butter", "Mark Butter consumed", "Print a label for Butter",
    ]


def test_use_one_and_mark_one_opened_send_the_rows_date(inventory):
    page = inventory(**DESKTOP)
    _then_reload(page, _button(page, "Use one Coconut Yogurt, best by Oct 24").click)
    assert _toast(page) == ("✓ Used one Coconut Yogurt", False)
    _then_reload(page, _button(page, "Mark one Coconut Yogurt, best by Oct 24, opened").click)
    _then_reload(page, _button(page, "Use one Honey, with no best-by date").click)
    assert page.sent == [
        ("/inventory/consume/41?amount=1&location_id=1&best_before_date=2026-10-24", None),
        ("/inventory/open/41?location_id=1&best_before_date=2026-10-24", None),
        ("/inventory/consume/44?amount=1&location_id=4&best_before_date=none", None),
    ]
    assert not page.errors


def test_mark_all_consumed_asks_about_that_date_and_sends_it(inventory):
    page = inventory(**DESKTOP)
    dialog = _confirm(page, "Mark Coconut Yogurt, best by Oct 24, consumed")
    assert dialog.locator(".pr-dialog-text").inner_text() == (
        "Mark all Coconut Yogurt in Kitchen Fridge, best by Oct 24, as consumed? "
        "This removes it from your stock.")
    _then_reload(page, dialog.get_by_role("button", name="Mark consumed").click)
    assert page.sent == [
        ("/inventory/consume/41?amount=2&location_id=1&best_before_date=2026-10-24", None)]
    assert _toast(page) == ("✓ Consumed: Coconut Yogurt", False)

    page.wait_for_selector(".pr-dialog-wrap", state="detached")
    dialog = _confirm(page, "Mark Honey, with no best-by date, consumed")
    assert dialog.locator(".pr-dialog-text").inner_text() == (
        "Mark all Honey, with no best-by date, as consumed? This removes it from your stock.")


def test_edit_on_a_row_of_one_date_changes_that_date_alone(inventory):
    page = inventory(**DESKTOP)
    _button(page, "Edit Coconut Yogurt, best by Oct 24").click()
    page.wait_for_selector("#editModal.show")
    assert page.inner_text("#editModalName") == "Coconut Yogurt, best by Oct 24"
    assert page.input_value("#editBestBy") == "2026-10-24"
    assert page.inner_text("#editBestByHelp") == "Changes the best-by date of just these items."
    assert page.inner_text("#editCategoryHelp") == "Applies to the whole product, on every row."
    page.fill("#editBestBy", "2026-10-26")
    _then_reload(page, lambda: page.click("#editSaveBtn"))
    assert page.sent == [("/inventory/edit/41", {
        "category": "Dairy", "best_before_date": "2026-10-26",
        "from_location_id": 1, "from_best_before_date": "2026-10-24"})]

    # A plain row's window keeps its title. Its stock has one date in one
    # place, so a changed date reaches that stock alone too.
    page.wait_for_selector(".modal-backdrop", state="detached")
    _button(page, "Edit Butter").click()
    page.wait_for_selector("#editModal.show")
    assert page.inner_text("#editModalName") == "Butter"
    assert page.inner_text("#editBestByHelp") == "Changes the best-by date of just these items."


def test_move_on_a_row_of_one_date_moves_that_date_alone(inventory):
    page = inventory(**DESKTOP)
    _button(page, "Move Coconut Yogurt, best by Oct 24, to another location").click()
    page.wait_for_selector("#moveModal.show")
    assert page.inner_text("#moveModal-title") == "Move Coconut Yogurt, best by Oct 24, to…"
    _then_reload(page, lambda: page.click('#moveModal [data-move-to="frozen"]'))
    assert _toast(page) == ("✓ Moved Coconut Yogurt to Frozen", False)

    # Dragging a row to a panel moves its date alone too.
    page.wait_for_selector(".modal-backdrop", state="detached")
    _then_reload(page, lambda: page.drag_and_drop("#row-41-1-2026-11-20",
                                                  "#col-frozen > [data-bucket]"))
    assert page.sent == [
        ("/inventory/move/41", {"bucket": "frozen", "from_location_id": 1,
                                "from_best_before_date": "2026-10-24"}),
        ("/inventory/move/41", {"bucket": "frozen", "from_location_id": 1,
                                "from_best_before_date": "2026-11-20"}),
    ]


def test_move_takes_the_amount_chosen_in_its_window(inventory):
    page = inventory(answers={"move": (200, {
        "product_id": 41, "moved_amount": 7.0, "location_id": 2, "best_by_updates": [],
        "new_best_by": None, "best_by_change": None, "left_behind": 0})}, **DESKTOP)
    _button(page, "Move Coconut Yogurt, best by Nov 20, to another location").click()
    page.wait_for_selector("#moveModal.show")
    assert page.input_value("#moveAmount") == "8"
    assert page.inner_text("#moveAmountOf") == "of 8 cup"
    # Plus stops at the whole row; minus takes one off.
    page.click('#moveModal button[aria-label="Move more"]')
    assert page.input_value("#moveAmount") == "8"
    page.click('#moveModal button[aria-label="Move fewer"]')
    assert page.input_value("#moveAmount") == "7"
    _then_reload(page, lambda: page.click('#moveModal [data-move-to="frozen"]'))
    assert _toast(page) == ("✓ Moved 7 of 8 Coconut Yogurt to Frozen", False)
    assert page.sent == [
        ("/inventory/move/41", {"bucket": "frozen", "from_location_id": 1,
                                "from_best_before_date": "2026-11-20", "amount": 7}),
    ]


def test_a_row_of_one_has_no_amount_to_choose(inventory):
    page = inventory(**DESKTOP)
    _button(page, "Move Butter to another location").click()
    page.wait_for_selector("#moveModal.show")
    assert page.is_hidden("#moveAmountGroup")


@pytest.fixture
def with_flour(monkeypatch):
    """The canned rows plus 1.04 kg of flour, which its row and the Move
    window both show as 1.0."""
    rows = copy.deepcopy(DASHBOARD)
    rows["pantry"].append({
        "product_id": 50, "name": "Flour", "unit": "kg", "category": "Baking",
        "product_group_id": 8, "product_group_name": "Condiments", "amount": 1.04,
        "product_amount": 1.04, "amount_opened": 0.0, "best_before_date": "2027-03-01",
        "days_remaining": 131, "urgency": "ok", "added_date": None, "location_id": 4,
        "location_name": "Pantry / Dry Storage", "storage_bucket": "pantry"})
    monkeypatch.setitem(globals(), "DASHBOARD", rows)


@pytest.mark.parametrize("press_all", [False, True], ids=["straight_away", "after_all"])
def test_a_row_the_window_rounds_still_moves_all_of_it(inventory, with_flour, press_all):
    page = inventory(**DESKTOP)
    _button(page, "Move Flour to another location").click()
    page.wait_for_selector("#moveModal.show")
    assert page.input_value("#moveAmount") == "1.0"
    if press_all:
        page.click('#moveModal button:has-text("All")')
    _then_reload(page, lambda: page.click('#moveModal [data-move-to="frozen"]'))
    assert page.sent == [("/inventory/move/50", {"bucket": "frozen", "from_location_id": 4})]


def test_a_label_from_a_row_of_one_date_carries_that_date(inventory):
    page = inventory(**DESKTOP)
    for name in ("Print a label for Coconut Yogurt, best by Oct 24",
                 "Print a label for Honey, that never expires",
                 "Print a label for Honey, with no best-by date"):
        with page.expect_response("**/printing/label"):
            _button(page, name).click()
    assert [body for _path, body in page.sent] == [
        {"product_id": 41, "name": "Coconut Yogurt", "best_by": "2026-10-24",
         "added": "2026-10-01", "stock_entry_id": "6a1f0c2b9d3e4"},
        {"product_id": 44, "name": "Honey", "best_by": "No expiry", "added": "2026-09-02",
         "stock_entry_id": "6a1f0c2b9d401"},
        # No stock entry of its own on this row, and no added date.
        {"product_id": 44, "name": "Honey", "best_by": "No date", "added": ""},
    ]


def test_a_plain_row_asks_what_it_always_has_and_edits_its_own_place(inventory):
    page = inventory(**DESKTOP)
    _then_reload(page, _button(page, "Use one Butter").click)
    _then_reload(page, _button(page, "Mark one Butter opened").click)

    _button(page, "Edit Butter").click()
    page.wait_for_selector("#editModal.show")
    page.fill("#editBestBy", "2026-11-02")
    _then_reload(page, lambda: page.click("#editSaveBtn"))
    page.wait_for_selector(".modal-backdrop", state="detached")

    _button(page, "Move Butter to another location").click()
    page.wait_for_selector("#moveModal.show")
    _then_reload(page, lambda: page.click('#moveModal [data-move-to="frozen"]'))
    page.wait_for_selector(".modal-backdrop", state="detached")

    with page.expect_response("**/printing/label"):
        _button(page, "Print a label for Butter").click()

    # Mark all consumed takes the row off the page without reading the list.
    asked = len(page.asked)
    dialog = _confirm(page, "Mark Butter consumed")
    assert dialog.locator(".pr-dialog-text").inner_text() == (
        "Mark all Butter in Kitchen Fridge as consumed? This removes it from your stock.")
    dialog.get_by_role("button", name="Mark consumed").click()
    page.wait_for_selector("#row-43-1", state="detached")
    assert len(page.asked) == asked

    assert page.sent == [
        ("/inventory/consume/43?amount=1&location_id=1", None),
        ("/inventory/open/43", None),
        # Edit names the row's place and its one date, so the product's other
        # places keep their dates.
        ("/inventory/edit/43", {"category": "Dairy", "best_before_date": "2026-11-02",
                                "from_location_id": 1, "from_best_before_date": "2026-10-31"}),
        ("/inventory/move/43", {"bucket": "frozen", "from_location_id": 1}),
        ("/printing/label", {"product_id": 43}),
        ("/inventory/consume/43?amount=1&location_id=1", None),
    ]
    assert not page.errors


def test_a_refusal_says_why_and_reads_the_list_again(inventory):
    page = inventory(answers={"consume": (409, {
        "detail": "Grocy would use an opened one first, so nothing was used."})}, **DESKTOP)
    _then_reload(page, _button(page, "Use one Coconut Yogurt, best by Oct 24").click)
    assert _toast(page) == (
        "Could not use one Coconut Yogurt: Grocy would use an opened one first, "
        "so nothing was used.", True)

    # A plain row's refusal leaves the list as it was, as it always has.
    asked = len(page.asked)
    _button(page, "Use one Butter").click()
    page.wait_for_function(
        "() => document.getElementById('toastMsg').textContent.includes('Butter')")
    page.wait_for_timeout(300)
    assert len(page.asked) == asked


def test_an_edit_for_stock_that_is_gone_closes_and_reads_the_list_again(inventory):
    page = inventory(answers={"edit": (404, {"detail": "This row is no longer in stock."})},
                     **DESKTOP)
    _button(page, "Edit Coconut Yogurt, best by Nov 20").click()
    page.wait_for_selector("#editModal.show")
    page.fill("#editBestBy", "2026-11-27")
    _then_reload(page, lambda: page.click("#editSaveBtn"))
    assert _toast(page) == ("Save failed: This row is no longer in stock.", True)
    page.wait_for_selector("#editModal", state="hidden")


def test_an_action_grocy_could_only_do_in_part_says_how_much_went(inventory):
    page = inventory(answers={
        "consume": (200, {"product_id": 41, "consumed": 3.0, "requested": 8.0,
                          "complete": False}),
        "move": (200, {"product_id": 41, "moved_amount": 5.0, "location_id": 2,
                       "best_by_updates": [], "new_best_by": None, "best_by_change": None,
                       "left_behind": 3.0}),
    }, **DESKTOP)
    dialog = _confirm(page, "Mark Coconut Yogurt, best by Nov 20, consumed")
    _then_reload(page, dialog.get_by_role("button", name="Mark consumed").click)
    assert _toast(page) == (
        "Used 3 of 8 Coconut Yogurt. Grocy would use other stock for the rest.", True)

    page.wait_for_selector(".pr-dialog-wrap", state="detached")
    _button(page, "Move Coconut Yogurt, best by Nov 20, to another location").click()
    page.wait_for_selector("#moveModal.show")
    _then_reload(page, lambda: page.click('#moveModal [data-move-to="frozen"]'))
    assert _toast(page) == (
        "Moved 5 of Coconut Yogurt to Frozen. Grocy would move other stock with the rest.",
        True)


def test_marking_one_opened_says_its_new_best_by_date(inventory):
    page = inventory(answers={"open": (200, {"product_id": 41, "opened": 1.0,
                                             "new_best_by": "2026-10-23", "complete": True})},
                     **DESKTOP)
    _then_reload(page, _button(page, "Mark one Coconut Yogurt, best by Nov 20, opened").click)
    assert _toast(page) == ("✓ Marked one Coconut Yogurt opened, now best by Oct 23", False)


def test_an_open_grocy_did_not_do_on_the_row_alone_says_so(inventory):
    # Something changed the row between the read and the open, so Grocy opened
    # stock outside it; the toast must not claim it went as asked.
    page = inventory(answers={"open": (200, {"product_id": 41, "opened": 0.0,
                                             "new_best_by": None, "complete": False})},
                     **DESKTOP)
    _then_reload(page, _button(page, "Mark one Coconut Yogurt, best by Nov 20, opened").click)
    assert _toast(page) == (
        "Grocy did not open exactly one Coconut Yogurt from this row. "
        "The list now shows what it opened.", True)


@pytest.mark.parametrize("device", [PHONE, KITCHEN_SCREEN], ids=["phone", "kitchen_screen"])
def test_the_move_window_and_its_amount_fit_a_phone_and_a_kitchen_screen(inventory, device):
    page = inventory(**device)
    _button(page, "Move Coconut Yogurt, best by Nov 20, to another location").click()
    page.wait_for_selector("#moveAmountGroup", state="visible")
    # Measured once the window has finished sliding in.
    page.wait_for_function(
        "() => getComputedStyle(document.querySelector('#moveModal .modal-dialog'))"
        ".transform === 'none'")
    box = page.eval_on_selector("#moveModal .modal-content", "e => e.getBoundingClientRect().toJSON()")
    assert box["top"] >= 0 and box["bottom"] <= device["viewport"]["height"]
    assert box["right"] <= device["viewport"]["width"]
    assert not page.errors


@pytest.mark.parametrize("device", [PHONE, KITCHEN_SCREEN], ids=["phone", "kitchen_screen"])
def test_rows_of_one_date_fit_a_phone_and_a_kitchen_screen(inventory, device):
    page = inventory(**device)
    width = device["viewport"]["width"]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    rights = page.eval_on_selector_all(
        ".item-row .btn-icon", "bs => bs.map(b => b.getBoundingClientRect().right)")
    assert rights and max(rights) <= width
    assert not page.errors
