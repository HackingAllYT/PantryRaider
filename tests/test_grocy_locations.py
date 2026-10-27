"""Older installs can tidy up their Grocy locations from the inbox.

An install from before ensure_location reused Grocy's own "Fridge" has two
fridges (Grocy's Fridge and Pantry Raider's Refrigerator), and a Freezer Grocy
does not know is a freezer. Each gets one inbox item, and nothing changes in
Grocy until its button is pressed: the merge moves every stock row whole (same
amount, best-by date and opened state), points the products at Refrigerator
and only then hides Fridge, never deleting it; the flag marks the one Freezer.
A merge cut short carries on when pressed again.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.db_models import ActionItem  # noqa: E402
from app.services import action_items as ai  # noqa: E402
from app.services import grocy as grocy_svc  # noqa: E402
from app.services import grocy_locations as gl  # noqa: E402
from app.services.grocy import GrocyClient  # noqa: E402
from test_grocy_best_by_groups import FakeGrocy471  # noqa: E402
from test_grocy_stock_edits import FREEZER, FRIDGE, _d  # noqa: E402

SERVICE = Path(__file__).resolve().parents[1] / "service"
GROCY_FRIDGE = 5


@pytest.fixture(autouse=True)
def _clean_table():
    db = SessionLocal()
    db.query(ActionItem).delete()
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.query(ActionItem).delete()
    db.commit()
    db.close()


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _loc(id_, name, freezer=0, active=1):
    return {"id": id_, "name": name, "is_freezer": freezer, "active": active}


OLD_INSTALL = [_loc(GROCY_FRIDGE, "Fridge"), _loc(FRIDGE, "Refrigerator"),
               _loc(FREEZER, "Freezer")]


# --- What counts ------------------------------------------------------------------

def test_an_old_install_has_two_fridges_and_an_unflagged_freezer():
    assert gl.two_fridges(OLD_INSTALL) == {
        "from_id": GROCY_FRIDGE, "from_name": "Fridge",
        "to_id": FRIDGE, "to_name": "Refrigerator"}
    assert gl.unflagged_freezer(OLD_INSTALL) == {"id": FREEZER, "name": "Freezer"}


def test_a_fresh_install_needs_neither():
    # The branch's ensure_location: Grocy's Fridge, a Freezer marked as one.
    fresh = [_loc(GROCY_FRIDGE, "Fridge"), _loc(FREEZER, "Freezer", freezer=1)]
    assert gl.two_fridges(fresh) is None
    assert gl.unflagged_freezer(fresh) is None


@pytest.mark.parametrize("locations", [
    [_loc(FRIDGE, "Refrigerator")],
    [_loc(GROCY_FRIDGE, "Fridge", active=0), _loc(FRIDGE, "Refrigerator")],
    [_loc(GROCY_FRIDGE, "Fridge"), _loc(FRIDGE, "Refrigerator", active=0)],
    [_loc(GROCY_FRIDGE, "Fridge", freezer=1), _loc(FRIDGE, "Refrigerator")],
    [_loc(GROCY_FRIDGE, "Fridge"), _loc(FRIDGE, "Refrigerator", freezer=1)],
    # Only Grocy's own name counts, never a fridge of the user's own.
    [_loc(GROCY_FRIDGE, "Garage Fridge"), _loc(FRIDGE, "Refrigerator")],
])
def test_no_merge_is_offered_unless_both_are_active_plain_fridges(locations):
    assert gl.two_fridges(locations) is None


def test_a_fridge_a_custom_category_owns_is_never_merged(monkeypatch):
    monkeypatch.setattr(settings, "custom_storage_categories",
                        [{"label": "Drinks", "location": "Fridge"}], raising=False)
    assert gl.two_fridges(OLD_INSTALL) is None


def test_an_inactive_or_missing_freezer_is_left_alone():
    assert gl.unflagged_freezer([_loc(FREEZER, "Freezer", active=0)]) is None
    assert gl.unflagged_freezer([_loc(GROCY_FRIDGE, "Fridge")]) is None


# --- The inbox items --------------------------------------------------------------

def _items(db):
    return {i["kind"]: i for i in ai.list_active(db)
            if i["kind"] in (gl.KIND_TWO_FRIDGES, gl.KIND_FREEZER_FLAG)}


def test_one_item_each_however_often_it_runs(db):
    for _ in range(3):
        assert gl.sync(db, OLD_INSTALL) == 2
    items = _items(db)
    assert len(ai.list_active(db)) == 2
    fridges = items[gl.KIND_TWO_FRIDGES]
    assert fridges["title"] == "Your Grocy has two fridges, Fridge and Refrigerator. Merge them?"
    assert "Nothing is deleted" in fridges["body"]
    assert fridges["payload"]["from_id"] == GROCY_FRIDGE
    assert items[gl.KIND_FREEZER_FLAG]["title"] == "Mark Freezer as a freezer in Grocy?"
    assert "freezing or thawing days" in items[gl.KIND_FREEZER_FLAG]["body"]


def test_a_dismissed_item_does_not_come_back(db):
    gl.sync(db, OLD_INSTALL)
    for item in _items(db).values():
        ai.archive(db, item["id"])
    gl.sync(db, OLD_INSTALL)
    assert _items(db) == {}
    assert gl.settled(db)


def test_an_item_goes_once_its_condition_is_gone_and_stays_gone(db):
    gl.sync(db, OLD_INSTALL)
    fixed = [_loc(GROCY_FRIDGE, "Fridge", active=0), _loc(FRIDGE, "Refrigerator"),
             _loc(FREEZER, "Freezer", freezer=1)]
    assert gl.sync(db, fixed) == 0
    assert _items(db) == {}
    # Fridge switched back on by hand later: the offer was answered already.
    gl.sync(db, OLD_INSTALL)
    assert _items(db) == {}


def test_refresh_reads_grocy_only_while_an_item_can_still_change(db, monkeypatch):
    calls = []

    async def locations(self):
        calls.append(1)
        return OLD_INSTALL

    monkeypatch.setattr(settings, "deployment_mode", "server", raising=False)
    monkeypatch.setattr(GrocyClient, "get_locations", locations)
    assert asyncio.run(gl.refresh(db)) == 2
    for item in _items(db).values():
        ai.resolve(db, item["id"])
    assert asyncio.run(gl.refresh(db)) == -1
    assert calls == [1]


def test_a_bandit_raises_nothing(db, monkeypatch):
    async def locations(self):
        raise AssertionError("a Bandit must not ask")

    monkeypatch.setattr(settings, "deployment_mode", "pi_remote", raising=False)
    monkeypatch.setattr(GrocyClient, "get_locations", locations)
    assert asyncio.run(gl.refresh(db)) == -1
    assert asyncio.run(gl.merge_fridges(db))["status"] == 403
    assert asyncio.run(gl.mark_freezer(db))["status"] == 403
    assert _items(db) == {}


def test_grocy_down_leaves_the_inbox_as_it_is(db, monkeypatch):
    gl.sync(db, OLD_INSTALL)

    async def locations(self):
        raise grocy_svc.GrocyError("down", kind="unreachable")

    monkeypatch.setattr(settings, "deployment_mode", "server", raising=False)
    monkeypatch.setattr(GrocyClient, "get_locations", locations)
    assert asyncio.run(gl.refresh(db)) == -1
    assert len(_items(db)) == 2


# --- The merge on a Grocy 4.7.1 ---------------------------------------------------

class FakeKitchen(FakeGrocy471):
    """FakeGrocy471 with an old install's three places, more products, the
    per-location stock list, product and location edits and the new-product
    preset location of the API key's user."""

    def __init__(self):
        super().__init__()
        self.locations = [_loc(GROCY_FRIDGE, "Fridge"), _loc(FRIDGE, "Refrigerator"),
                          _loc(FREEZER, "Freezer")]
        self.product.update(location_id=GROCY_FRIDGE,
                            default_consume_location_id=GROCY_FRIDGE,
                            default_best_before_days_after_freezing=60)
        self.others = [{"id": 2, "name": "Butter", "location_id": GROCY_FRIDGE,
                        "default_consume_location_id": None},
                       {"id": 3, "name": "Jam", "location_id": FRIDGE,
                        "default_consume_location_id": None}]
        self.preset = GROCY_FRIDGE
        self.refuse_location_edits = False

    def products(self):
        return [self.product, *self.others]

    def _answer(self, method, path, body):
        if path == "/objects/products" and method == "GET":
            return 200, [dict(p) for p in self.products()]
        m = re.fullmatch(r"/stock/locations/(\d+)/entries", path)
        if m and method == "GET":
            return 200, [dict(r) for r in self.rows.values()
                         if r["location_id"] == int(m[1]) and r["amount"] > 0]
        m = re.fullmatch(r"/objects/(products|locations)/(\d+)", path)
        if m and method == "PUT":
            if m[1] == "locations" and self.refuse_location_edits:
                return 400, {"error_message": "The database is read-only"}
            rows = self.products() if m[1] == "products" else self.locations
            next(r for r in rows if r["id"] == int(m[2])).update(body)
            return 204, None
        if path == "/user/settings/product_presets_location_id":
            if method == "PUT":
                self.preset = body["value"]
                return 204, None
            return 200, {"value": self.preset}
        return super()._answer(method, path, body)

    def stock(self):
        """Every row as the user sees it, wherever it is."""
        return sorted((r["best_before_date"] or "", r["amount"], r["open"],
                       r["opened_date"] or "", r["price"] or 0, r["purchased_date"])
                      for r in self.rows.values() if r["amount"] > 0)


@pytest.fixture
def kitchen(monkeypatch):
    fake = FakeKitchen()
    monkeypatch.setattr(settings, "deployment_mode", "server", raising=False)
    monkeypatch.setattr(settings, "grocy_base_url", "http://grocy.test", raising=False)
    monkeypatch.setattr(settings, "grocy_api_key", "test-key", raising=False)
    monkeypatch.setattr(grocy_svc, "_client", fake)
    fake.add(2, _d(5), GROCY_FRIDGE)
    first = fake.add(1, _d(9), GROCY_FRIDGE, opened=True)
    fake.add(1, _d(5), GROCY_FRIDGE, stock_id=first["stock_id"], price=2.5)
    fake.add(3, None, GROCY_FRIDGE)
    fake.add(4, _d(2), FRIDGE)
    return fake


def test_the_merge_moves_every_row_as_it_is_and_hides_fridge(kitchen, db):
    before = kitchen.stock()
    gl.sync(db, kitchen.locations)
    answer = asyncio.run(gl.merge_fridges(db))
    assert answer["ok"], answer
    assert kitchen.stock() == before
    assert {r["location_id"] for r in kitchen.rows.values()} == {FRIDGE}
    # Whole rows only, so nothing was split and no row merged.
    assert len(kitchen.rows) == 5
    assert answer["result"]["moved_rows"] == 4
    fridge = next(loc for loc in kitchen.locations if loc["id"] == GROCY_FRIDGE)
    assert fridge["active"] == 0 and fridge["name"] == "Fridge"
    assert (kitchen.product["location_id"], kitchen.product["default_consume_location_id"]) \
        == (FRIDGE, FRIDGE)
    assert [p["location_id"] for p in kitchen.others] == [FRIDGE, FRIDGE]
    assert kitchen.preset == FRIDGE
    # Never a delete, never an edit of a stock row or of the journal.
    assert not any(m == "DELETE" for m, _p in kitchen.writes())
    assert kitchen.entry_writes() == []
    assert list(_items(db)) == [gl.KIND_FREEZER_FLAG]
    assert answer["message"] == ("Moved 4 stock entries to Refrigerator, and Fridge is "
                                 "now hidden in Grocy.")


def test_a_merge_cut_short_carries_on_when_pressed_again(kitchen, db):
    before = kitchen.stock()
    gl.sync(db, kitchen.locations)
    transfers = []

    def power_cut(method, path, body):
        if path.endswith("/transfer"):
            transfers.append(path)
            if len(transfers) == 2:
                raise RuntimeError("power cut")

    kitchen.after_write = power_cut
    with pytest.raises(RuntimeError):
        asyncio.run(gl.merge_fridges(db))
    assert kitchen.stock() == before
    assert kitchen.at(GROCY_FRIDGE) and gl.KIND_TWO_FRIDGES in _items(db)
    kitchen.after_write = None
    gl.sync(db, kitchen.locations)   # the inbox still offers it
    assert gl.KIND_TWO_FRIDGES in _items(db)
    answer = asyncio.run(gl.merge_fridges(db))
    assert answer["ok"], answer
    assert kitchen.stock() == before and kitchen.at(GROCY_FRIDGE) == []
    # Pressed again once done: nothing left to do, nothing written.
    writes = len(kitchen.writes())
    assert asyncio.run(gl.merge_fridges(db))["ok"]
    assert len(kitchen.writes()) == writes


def test_stock_grocy_cannot_move_keeps_fridge_visible_and_says_why(kitchen, db):
    kitchen.product["enable_tare_weight_handling"] = 1
    gl.sync(db, kitchen.locations)
    answer = asyncio.run(gl.merge_fridges(db))
    assert not answer["ok"] and answer["status"] == 409
    assert answer["error"] == (
        "Fridge was not hidden. Still in Fridge: Coconut Yogurt. Grocy cannot transfer "
        "a product weighed with its container (Coconut Yogurt), so change the location "
        "on its stock entries in Grocy. Then press Merge again.")
    assert next(loc for loc in kitchen.locations if loc["id"] == GROCY_FRIDGE)["active"] == 1
    assert gl.KIND_TWO_FRIDGES in _items(db)


def test_mark_freezer_flags_only_the_freezer(kitchen, db):
    gl.sync(db, kitchen.locations)
    answer = asyncio.run(gl.mark_freezer(db))
    assert answer == {"ok": True, "message": "Freezer is now a freezer in Grocy."}
    assert kitchen.writes() == [("PUT", f"/objects/locations/{FREEZER}")]
    assert kitchen.sent("PUT", f"/objects/locations/{FREEZER}") == [{"is_freezer": 1}]
    assert [i["kind"] for i in _items(db).values()] == [gl.KIND_TWO_FRIDGES]


def test_grocy_refusing_the_flag_is_shown_and_the_item_stays(kitchen, db):
    gl.sync(db, kitchen.locations)
    kitchen.refuse_location_edits = True
    answer = asyncio.run(gl.mark_freezer(db))
    assert answer["ok"] is False and answer["status"] == 502
    assert answer["error"].startswith("Grocy did not take the change")
    assert "read-only" in answer["error"]
    assert len(_items(db)) == 2


# --- The buttons ------------------------------------------------------------------

@pytest.fixture
def client(kitchen, monkeypatch, tmp_path):
    monkeypatch.chdir(SERVICE)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "auth_required", False, raising=False)
    monkeypatch.setattr(settings, "auth_password", "", raising=False)
    monkeypatch.setattr(type(settings), "is_configured", lambda self: True, raising=False)
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_the_buttons_are_admin_routes():
    from app.services import access
    for path in ("/setup/grocy/merge-fridges", "/setup/grocy/mark-freezer"):
        assert access.route_family("POST", path) == "admin"


def test_the_buttons_answer_over_http(client, kitchen):
    r = client.post("/setup/grocy/mark-freezer", headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200 and r.json()["ok"]
    r = client.post("/setup/grocy/merge-fridges", headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200 and r.json()["ok"]
    assert kitchen.at(GROCY_FRIDGE) == []


def test_the_inbox_script_wires_both_items_to_their_routes():
    js = (SERVICE / "app" / "static" / "js" / "pages" / "pending.js").read_text()
    assert "grocy_two_fridges: { label: 'Merge'" in js
    assert "grocy_freezer_flag: { label: 'Mark as freezer'" in js
    assert "path: 'setup/grocy/merge-fridges'" in js
    assert "path: 'setup/grocy/mark-freezer'" in js
    assert "section.dataset.managesSettings === '1'" in js
