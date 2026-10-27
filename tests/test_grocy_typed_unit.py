"""The unit typed for an item reaches Grocy.

Adding "2 kg Basmati Rice", "1 gallon Milk" or "1 bag Peas" from the Manual
tab (or a receipt, photo or import) created every product in Pieces, so the
Inventory showed "2 Piece" rice. A new product now takes the typed unit; a
product Grocy already has keeps its own unit and converts when Grocy can,
and says so when it cannot instead of booking kilograms as pieces quietly.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.models.food import FoodItem
from app.services import grocy as grocy_mod
from app.services.grocy import GrocyClient, find_unit, unit_label, unit_names

PIECE = {"id": 2, "name": "Piece", "name_plural": "Pieces"}
KILOGRAM = {"id": 4, "name": "Kilogram", "name_plural": "Kilograms"}
GRAM = {"id": 7, "name": "Gram", "name_plural": "Grams"}


@pytest.mark.parametrize("typed, expected", [
    ("kg", ("Kilogram", "Kilograms")),
    ("Kg", ("Kilogram", "Kilograms")),
    ("kilograms", ("Kilogram", "Kilograms")),
    ("lbs", ("Pound", "Pounds")),
    ("gallon", ("Gallon", "Gallons")),
    ("fl  oz", ("Fluid ounce", "Fluid ounces")),
    ("item", ("Piece", "Pieces")),
    ("pcs", ("Piece", "Pieces")),
    ("", ("Piece", "Pieces")),
    ("bag", ("Bag", "Bags")),
    ("bags", ("Bag", "Bags")),
    ("boxes", ("Box", "Boxes")),
    ("loaves", ("Loaf", "Loaves")),
    ("glass", ("Glass", "Glasses")),
    ("500g", ("Piece", "Pieces")),
])
def test_unit_names_reads_what_people_type(typed, expected):
    assert unit_names(typed)[:2] == expected


def test_find_unit_prefers_the_unit_named_as_typed():
    units = [KILOGRAM, {"id": 9, "name": "kg", "name_plural": "kg"}]
    assert find_unit(units, "kg")["id"] == 9
    assert find_unit(units, "kilos")["id"] == 4
    assert find_unit([PIECE], "item")["id"] == 2
    assert find_unit([PIECE], "bag") is None


def test_unit_label_is_plural_unless_exactly_one():
    assert unit_label(PIECE, 1) == "Piece"
    assert unit_label(PIECE, 12) == "Pieces"
    assert unit_label(PIECE, 0.5) == "Pieces"
    assert unit_label({"name": "Dash"}, 2) == "Dash"


def _mock_grocy(monkeypatch, products, units, conversions=()):
    sent = []

    def handler(request: httpx.Request):
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        sent.append((request.method, path, body))
        if path.endswith("/objects/locations") or path.endswith("/objects/product_groups"):
            if request.method == "GET":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json={"created_object_id": 3})
        if path.endswith("/objects/quantity_units"):
            if request.method == "GET":
                return httpx.Response(200, json=units)
            return httpx.Response(200, json={"created_object_id": 50})
        if path.endswith("/objects/products"):
            if request.method == "GET":
                return httpx.Response(200, json=products)
            return httpx.Response(200, json={"created_object_id": 777})
        if "quantity_unit_conversions" in path:
            return httpx.Response(200, json=list(conversions))
        if "/stock/products/" in path and path.endswith("/add"):
            return httpx.Response(200, json=[{"id": 1}])
        return httpx.Response(404, json={"error_message": "unexpected " + path})

    monkeypatch.setattr(grocy_mod, "_client",
                        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(grocy_mod.settings, "grocy_base_url", "http://grocy.test",
                        raising=False)
    return sent


def _posts(sent, suffix):
    return [b for m, p, b in sent if m == "POST" and p.endswith(suffix)]


def _added(sent, product_id):
    return next(b["amount"] for m, p, b in sent
                if p.endswith(f"/stock/products/{product_id}/add"))


def _rice(quantity=2.0, unit="kg"):
    return FoodItem(name="Basmati Rice", quantity=quantity, unit=unit)


def test_a_new_product_is_stocked_in_the_unit_typed(monkeypatch):
    sent = _mock_grocy(monkeypatch, [], [PIECE])
    result = asyncio.run(GrocyClient().import_item(_rice()))
    assert _posts(sent, "/objects/quantity_units") == [
        {"name": "Kilogram", "name_plural": "Kilograms"}]
    product = _posts(sent, "/objects/products")[0]
    assert product["qu_id_stock"] == product["qu_id_purchase"] == 50
    assert _added(sent, 777) == 2.0
    assert "unit_note" not in result


def test_a_unit_grocy_already_has_is_reused_under_any_spelling(monkeypatch):
    sent = _mock_grocy(monkeypatch, [], [PIECE, KILOGRAM])
    asyncio.run(GrocyClient().import_item(_rice(unit="kilograms")))
    assert _posts(sent, "/objects/quantity_units") == []
    assert _posts(sent, "/objects/products")[0]["qu_id_stock"] == 4


def test_no_unit_typed_still_means_piece(monkeypatch):
    sent = _mock_grocy(monkeypatch, [], [PIECE])
    asyncio.run(GrocyClient().import_item(_rice(quantity=12, unit="item")))
    assert _posts(sent, "/objects/quantity_units") == []
    assert _posts(sent, "/objects/products")[0]["qu_id_stock"] == 2


def test_an_existing_product_converts_when_grocy_can(monkeypatch):
    rice = {"id": 321, "name": "Basmati Rice", "qu_id_stock": 4}
    conversions = [{"product_id": None, "from_qu_id": 7, "to_qu_id": 4,
                    "factor": 0.001}]
    sent = _mock_grocy(monkeypatch, [rice], [PIECE, KILOGRAM, GRAM], conversions)
    result = asyncio.run(GrocyClient().import_item(_rice(quantity=500, unit="g")))
    assert _added(sent, 321) == pytest.approx(0.5)
    assert "unit_note" not in result
    assert _posts(sent, "/objects/products") == []


def test_an_existing_product_in_another_unit_is_not_changed_and_says_so(monkeypatch):
    rice = {"id": 321, "name": "Basmati Rice", "qu_id_stock": 2}
    sent = _mock_grocy(monkeypatch, [rice], [PIECE])
    result = asyncio.run(GrocyClient().import_item(_rice()))
    assert _added(sent, 321) == 2.0
    assert _posts(sent, "/objects/quantity_units") == []
    assert _posts(sent, "/objects/products") == []
    assert result["unit_note"] == (
        "Basmati Rice is kept in Pieces in Grocy, which has no conversion "
        "from kg, so it was added as 2 Pieces.")


def test_an_existing_product_with_no_unit_typed_adds_quietly(monkeypatch):
    rice = {"id": 321, "name": "Basmati Rice", "qu_id_stock": 4}
    sent = _mock_grocy(monkeypatch, [rice], [PIECE, KILOGRAM])
    result = asyncio.run(GrocyClient().import_item(_rice(quantity=1, unit="item")))
    assert _added(sent, 321) == 1.0
    assert "unit_note" not in result


def test_a_product_made_earlier_in_the_same_batch_keeps_its_unit(monkeypatch):
    sent = _mock_grocy(monkeypatch, [], [PIECE])
    client = GrocyClient()
    asyncio.run(client.import_item(_rice()))
    result = asyncio.run(client.import_item(_rice(quantity=1, unit="Kg")))
    assert len(_posts(sent, "/objects/products")) == 1
    assert len(_posts(sent, "/objects/quantity_units")) == 1
    assert "unit_note" not in result
