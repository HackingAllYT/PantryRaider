"""Stock Up scans check Grocy's own barcodes before Open Food Facts (issue #65).

The issue's example: Pure Leaf tea stocked in Bottles, with the bottle barcode
registered in Bottles and the case barcode registered in Packs, where Grocy
converts 1 Pack to 18 Bottles. Scanning either code must fill the pending row
from that Grocy product, and committing the case must add 18 bottles to it.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

SERVICE = Path(__file__).resolve().parents[1] / "service"
sys.path.insert(0, str(SERVICE))

from app.models.food import FoodItem  # noqa: E402
from app.services.grocy import (GrocyClient, GrocyError,  # noqa: E402
                                qu_conversion_factor, unit_matches)

BOTTLE = "012000173219"
CASE = "012000182839"
SIXPACK = "012000999996"

UNITS = [
    {"id": 1, "name": "Bottle", "name_plural": "Bottles"},
    {"id": 2, "name": "Pack", "name_plural": "Packs"},
]
PRODUCT = {
    "id": 5, "name": "Pure Leaf Unsweetened Tea", "qu_id_stock": 1,
    "location_id": 3, "product_group_id": 4, "default_best_before_days": 180,
}
BARCODES = [
    {"id": 1, "product_id": 5, "barcode": BOTTLE, "qu_id": 1, "amount": None},
    {"id": 2, "product_id": 5, "barcode": CASE, "qu_id": 2, "amount": 1},
    {"id": 3, "product_id": 5, "barcode": SIXPACK, "qu_id": 1, "amount": 6},
]
CONVERSIONS = [
    {"id": 1, "product_id": 5, "from_qu_id": 2, "to_qu_id": 1, "factor": 18},
]


@pytest.fixture
def grocy(monkeypatch):
    """A fake Grocy holding the issue's product. Records stock adds."""
    state = {"adds": [], "imports": [], "resolved_view": True}

    async def fake_get(self, path):
        path = unquote(path)
        if path.startswith("/objects/product_barcodes?query[]=barcode="):
            code = path.rsplit("=", 1)[1]
            return [b for b in BARCODES if b["barcode"] == code]
        if path == "/objects/products/5":
            return PRODUCT
        if path == "/objects/quantity_units":
            return UNITS
        if path == "/objects/locations":
            return [{"id": 3, "name": "Refrigerator"}]
        if path == "/objects/product_groups":
            return [{"id": 4, "name": "Beverages"}]
        if path.startswith("/objects/quantity_unit_conversions_resolved"):
            if not state["resolved_view"]:
                raise GrocyError("Grocy 400 on /objects/...: entity not found")
            return CONVERSIONS
        if path == "/objects/quantity_unit_conversions":
            return CONVERSIONS
        return []

    async def fake_add_stock(self, product_id, item, location_id=None):
        state["adds"].append((product_id, item.quantity))
        state.setdefault("locations", []).append(location_id)
        return {}

    async def fake_import(self, item):
        state["imports"].append(item.name)
        return {"product_id": 77, "name": item.name}

    async def fake_stock(self):
        return []

    monkeypatch.setattr(GrocyClient, "_get", fake_get)
    monkeypatch.setattr(GrocyClient, "add_stock", fake_add_stock)
    monkeypatch.setattr(GrocyClient, "import_item", fake_import)
    monkeypatch.setattr(GrocyClient, "get_stock", fake_stock)
    return state


# -- pure helpers ---------------------------------------------------------------

def test_conversion_factor_direct_and_inverted():
    assert qu_conversion_factor(CONVERSIONS, 5, 2, 1) == 18
    assert qu_conversion_factor(CONVERSIONS, 5, 1, 2) == pytest.approx(1 / 18)
    assert qu_conversion_factor(CONVERSIONS, 5, 1, 1) == 1.0


def test_conversion_factor_product_beats_global_and_unlinked_is_none():
    convs = [{"product_id": None, "from_qu_id": 2, "to_qu_id": 1, "factor": 12},
             *CONVERSIONS]
    assert qu_conversion_factor(convs, 5, 2, 1) == 18
    # Another product only has the global conversion.
    assert qu_conversion_factor(convs, 9, 2, 1) == 12
    assert qu_conversion_factor(CONVERSIONS, 9, 2, 1) is None


def test_unit_matches_singular_and_plural():
    assert unit_matches(UNITS[1], "pack")
    assert unit_matches(UNITS[1], "Packs ")
    assert not unit_matches(UNITS[1], "")
    assert not unit_matches(UNITS[1], "Bottle")


# -- GrocyClient ----------------------------------------------------------------

def test_find_barcode_case_reads_its_unit_and_conversion(grocy):
    match = asyncio.run(GrocyClient().find_barcode(CASE))
    assert match["product_id"] == 5
    assert match["name"] == "Pure Leaf Unsweetened Tea"
    assert match["unit"] == "Pack"
    assert match["amount"] == 1
    assert match["factor"] == 18
    assert match["location"] == "Refrigerator"
    assert match["product_group"] == "Beverages"


def test_find_barcode_bottle_is_in_stock_units(grocy):
    match = asyncio.run(GrocyClient().find_barcode(BOTTLE))
    assert (match["unit"], match["amount"], match["factor"]) == ("Bottle", 1.0, 1.0)


def test_find_barcode_unknown_is_none(grocy):
    assert asyncio.run(GrocyClient().find_barcode("0000000000000")) is None


def test_find_barcode_without_resolved_view_reads_plain_table(grocy):
    grocy["resolved_view"] = False
    assert asyncio.run(GrocyClient().find_barcode(CASE))["factor"] == 18


def test_stock_amount_converts_known_units_only(grocy):
    c = GrocyClient()
    assert asyncio.run(c.stock_amount(5, 2, "Packs")) == 36
    assert asyncio.run(c.stock_amount(5, 3, "Bottle")) == 3
    # A unit Grocy does not know is taken as the stock unit, as before.
    assert asyncio.run(c.stock_amount(5, 3, "item")) == 3


def test_add_to_product_stocks_converted_amount(grocy):
    item = FoodItem(name="Pure Leaf Unsweetened Tea", quantity=1, unit="Pack")
    result = asyncio.run(GrocyClient().add_to_product(5, item))
    assert result["product_id"] == 5
    assert grocy["adds"] == [(5, 18)]
    # Kept in the product's own storage area: Grocy files it under the
    # product's default location.
    assert grocy["locations"] == [None]


def test_add_to_product_follows_a_storage_area_changed_on_review(grocy, monkeypatch):
    made = []

    async def fake_ensure_location(self, name):
        made.append(name)
        return 9

    monkeypatch.setattr(GrocyClient, "ensure_location", fake_ensure_location)
    item = FoodItem(name="Pure Leaf Unsweetened Tea", quantity=1, unit="Bottle",
                    storage_type="frozen")
    asyncio.run(GrocyClient().add_to_product(5, item))
    assert len(made) == 1
    assert grocy["locations"] == [9]


# -- the Stock Up scan flow -----------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path, grocy):
    cwd = os.getcwd()
    os.chdir(SERVICE)
    from app.config import settings
    from app.services import scan_session, scanner_mode
    monkeypatch.setattr(settings, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "auth_password", "", raising=False)
    monkeypatch.setattr(settings, "auth_required", False, raising=False)
    monkeypatch.setattr(settings, "deployment_mode", "server", raising=False)
    monkeypatch.setattr(settings, "grocy_base_url", "http://grocy.test", raising=False)
    monkeypatch.setattr(settings, "grocy_api_key", "k", raising=False)
    monkeypatch.setattr(settings, "barcode_autocheck_shopping", False, raising=False)
    scanner_mode.reset()
    scan_session.reset()
    scanner_mode.set_mode("inventory")
    from app.routers import pending as pending_router
    monkeypatch.setattr(pending_router, "_spawn_enrichment", lambda *a: None)
    from fastapi.testclient import TestClient
    from app.main import app
    try:
        yield TestClient(app)
    finally:
        os.chdir(cwd)
        scanner_mode.reset()
        scan_session.reset()


@pytest.fixture
def off_calls(monkeypatch):
    """Records Open Food Facts lookups; answers with a generic OFF product."""
    from app.routers import pending as pending_router
    calls = []

    async def _lookup(barcode, db):
        calls.append(barcode)
        return FoodItem(name="Some OFF Product")
    monkeypatch.setattr(pending_router, "lookup_barcode", _lookup)
    return calls


def _clear(barcode):
    from app.database import SessionLocal
    from app.models.db_models import PendingItem
    db = SessionLocal()
    try:
        db.query(PendingItem).filter(PendingItem.barcode == barcode).delete()
        db.commit()
    finally:
        db.close()


def _scan(client, barcode):
    return client.post("/pending/scan", json={"barcode": barcode}).json()["item"]["id"]


def _enrich(item_id, barcode):
    from app.routers import pending as pending_router
    asyncio.run(pending_router.enrich_pending_item(item_id, barcode))


def _row(client, item_id):
    return next(r for r in client.get("/pending/").json()["items"]
                if r["id"] == item_id)


def test_case_scan_fills_row_from_grocy_not_off(client, grocy, off_calls):
    _clear(CASE)
    item_id = _scan(client, CASE)
    _enrich(item_id, CASE)
    row = _row(client, item_id)
    assert off_calls == []
    assert row["name"] == "Pure Leaf Unsweetened Tea"
    assert (row["quantity"], row["unit"]) == (1, "Pack")
    assert row["category"] == "Beverages"
    assert row["storage_type"] == "refrigerated"
    assert row["best_by_source"] == "default"
    assert row["lookup_failed"] is False and row["enriching"] is False

    r = client.post("/pending/commit", json={"ids": [item_id]}).json()
    assert r["imported"] == 1
    # One case = 18 bottles, on the existing product; nothing made by name.
    assert grocy["adds"] == [(5, 18)]
    assert grocy["imports"] == []


def test_amount_barcode_counts_every_scan(client, grocy, off_calls):
    _clear(SIXPACK)
    item_id = _scan(client, SIXPACK)
    _scan(client, SIXPACK)            # merged before the lookup lands
    _enrich(item_id, SIXPACK)
    _scan(client, SIXPACK)            # merged after it
    row = _row(client, item_id)
    assert (row["quantity"], row["unit"]) == (18, "Bottle")


def test_unknown_barcode_still_uses_open_food_facts(client, grocy, off_calls):
    code = "0111000000017"
    _clear(code)
    item_id = _scan(client, code)
    _enrich(item_id, code)
    assert off_calls == [code]
    assert _row(client, item_id)["name"] == "Some OFF Product"

    client.post("/pending/commit", json={"ids": [item_id]})
    assert grocy["imports"] == ["Some OFF Product"]


def test_renamed_row_is_imported_by_name(client, grocy, off_calls):
    _clear(BOTTLE)
    item_id = _scan(client, BOTTLE)
    _enrich(item_id, BOTTLE)
    client.patch(f"/pending/{item_id}", json={"name": "Pure Leaf Lemon"})
    client.post("/pending/commit", json={"ids": [item_id]})
    assert grocy["adds"] == []
    assert grocy["imports"] == ["Pure Leaf Lemon"]


def test_grocy_down_falls_back_to_open_food_facts(client, monkeypatch, off_calls):
    async def _down(self, barcode):
        raise GrocyError("Grocy is not reachable")
    monkeypatch.setattr(GrocyClient, "find_barcode", _down)
    _clear(CASE)
    item_id = _scan(client, CASE)
    _enrich(item_id, CASE)
    assert off_calls == [CASE]
    assert _row(client, item_id)["name"] == "Some OFF Product"
