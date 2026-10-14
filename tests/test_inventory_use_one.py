"""Use one unit from the Inventory page.

The row's Use one button posts /inventory/consume/{id}?amount=1, next to the
Mark one opened button, so one item can leave stock without scanning it while
the ✓ button still consumes the whole amount.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SERVICE = Path(__file__).resolve().parents[1] / "service"
sys.path.insert(0, str(SERVICE))

from app.config import settings  # noqa: E402
from app.services.grocy import GrocyClient, GrocyError  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    cwd = os.getcwd()
    os.chdir(SERVICE)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "auth_required", False, raising=False)
    # Without an inventory backend the setup redirect answers every request
    # with the wizard page. Grocy itself is faked per test.
    monkeypatch.setattr(settings, "grocy_base_url", "http://grocy.test", raising=False)
    monkeypatch.setattr(settings, "grocy_api_key", "test-key", raising=False)
    from fastapi.testclient import TestClient
    from app.main import app
    try:
        yield TestClient(app)
    finally:
        os.chdir(cwd)


def test_consume_endpoint_uses_exactly_one(client):
    seen = {}

    async def fake_consume(self, product_id, amount=1.0, **kwargs):
        seen["args"] = (product_id, amount)
        return [{"id": "1"}]

    with patch.object(GrocyClient, "consume_stock", fake_consume):
        r = client.post("/inventory/consume/42?amount=1")
    assert r.status_code == 200
    assert seen["args"] == (42, 1.0)


def test_consume_endpoint_surfaces_grocy_errors(client):
    async def fake_consume(self, product_id, amount=1.0, **kwargs):
        raise GrocyError("Grocy 400 on /stock/products/42/consume: no stock")

    with patch.object(GrocyClient, "consume_stock", fake_consume):
        r = client.post("/inventory/consume/42?amount=1")
    assert r.status_code == 500
    assert "no stock" in r.json()["detail"]


def test_inventory_page_ships_use_one_next_to_opened(client):
    with patch.object(type(settings), "is_configured", lambda self: True):
        r = client.get("/ui/inventory")
    assert r.status_code == 200
    html = r.text
    assert "useOne(" in html
    assert "/inventory/consume/${productId}?amount=1" in html
    # Same zero-stock gate as Mark one opened, and placed right after it.
    assert html.index("markOpened(${item.product_id}") < html.index("useOne(${item.product_id}") \
        < html.index("consume(${item.product_id}, ${item.amount}")


def test_use_one_takes_stock_from_the_rows_location(client):
    # A product kept in two Grocy locations is listed once per location (#73),
    # so Use one sends the row's location and only that stock is touched.
    seen = {}

    async def fake_consume(self, product_id, amount=1.0, **kwargs):
        seen["args"] = (product_id, amount, kwargs.get("location_id"))
        return [{"id": "1"}]

    with patch.object(GrocyClient, "consume_stock", fake_consume):
        r = client.post("/inventory/consume/42?amount=1&location_id=3")
    assert r.status_code == 200
    assert seen["args"] == (42, 1.0, 3)

    with patch.object(type(settings), "is_configured", lambda self: True):
        html = client.get("/ui/inventory").text
    assert "useOne(${item.product_id}, '${esc(item.name)}', '${loc}')" in html
    assert "&location_id=${locationId}" in html
