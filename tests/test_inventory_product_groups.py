"""Product groups on the Inventory dashboard (FoodAssistant-7xddm, GitHub #124).

Every dashboard row carries its Grocy product group by id and name, and the
payload lists every product group (custom ones too) for the page's category
filter, out of the same reads the dashboard already made. A satellite builds
its dashboard the same way through the main server's Grocy proxy, which must
keep allowing the product group read.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SERVICE = Path(__file__).resolve().parents[1] / "service"
sys.path.insert(0, str(SERVICE))

from app.config import settings  # noqa: E402
from app.services.grocy import GrocyClient  # noqa: E402

_GROUPS = [{"id": 2, "name": "Dairy"}, {"id": 1, "name": "produce"},
           {"id": 9, "name": "Holiday Baking"}]
_STOCK = [
    {"product_id": 1, "amount": 2.0, "location_id": 1,
     "product": {"name": "Milk", "product_group_id": 2, "location_id": 1}},
    {"product_id": 2, "amount": 3.0, "location_id": 1,
     "product": {"name": "Spinach", "product_group_id": "1", "location_id": 1}},
    {"product_id": 3, "amount": 1.0, "location_id": 1,
     "product": {"name": "Mystery Jar", "location_id": 1}},
    {"product_id": 4, "amount": 1.0, "location_id": 1,
     "product": {"name": "Old Thing", "product_group_id": 77, "location_id": 1}},
]


async def _fake_get(self, path):
    return {"/stock": _STOCK}.get(path, [])


async def _fake_cached(self, path):
    return {"/objects/product_groups": _GROUPS,
            "/objects/locations": [{"id": 1, "name": "Fridge"}]}.get(path, [])


def test_full_stock_rows_carry_the_product_group():
    with patch.object(GrocyClient, "_get", _fake_get), \
         patch.object(GrocyClient, "_cached_list", _fake_cached):
        rows = {r["name"]: r for r in asyncio.run(GrocyClient().get_full_stock())}
    assert rows["Milk"]["product_group_id"] == 2
    assert rows["Milk"]["product_group_name"] == "Dairy"
    assert rows["Spinach"]["product_group_id"] == 1      # Grocy's string id
    assert rows["Spinach"]["product_group_name"] == "produce"
    # No group, and a group since deleted, both count as no category.
    assert rows["Mystery Jar"]["product_group_id"] is None
    assert rows["Mystery Jar"]["product_group_name"] == ""
    assert rows["Old Thing"]["product_group_id"] is None
    assert rows["Old Thing"]["product_group_name"] == ""


def test_dashboard_product_groups_reuse_the_stock_read():
    client = GrocyClient()
    assert client.dashboard_product_groups() == []
    with patch.object(GrocyClient, "_get", _fake_get), \
         patch.object(GrocyClient, "_cached_list", _fake_cached):
        asyncio.run(client.get_full_stock())
    assert client.dashboard_product_groups() == [
        {"id": 2, "name": "Dairy"}, {"id": 9, "name": "Holiday Baking"},
        {"id": 1, "name": "produce"}]


@pytest.fixture
def client(monkeypatch, tmp_path):
    cwd = os.getcwd()
    os.chdir(SERVICE)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "auth_required", False, raising=False)
    monkeypatch.setattr(settings, "grocy_base_url", "http://grocy.test", raising=False)
    monkeypatch.setattr(settings, "grocy_api_key", "test-key", raising=False)
    from fastapi.testclient import TestClient
    from app.main import app
    try:
        yield TestClient(app)
    finally:
        os.chdir(cwd)


def test_dashboard_items_include_the_product_group_fields(client):
    with patch.object(GrocyClient, "_get", _fake_get), \
         patch.object(GrocyClient, "_cached_list", _fake_cached):
        r = client.get("/inventory/dashboard?sort=name_asc")
    assert r.status_code == 200
    body = r.json()
    items = [i for k, v in body.items() if not k.startswith("_") for i in v]
    by_name = {i["name"]: i for i in items}
    assert set(by_name) == {"Milk", "Spinach", "Mystery Jar", "Old Thing"}
    for item in items:
        assert "product_group_id" in item and "product_group_name" in item
    assert by_name["Milk"]["product_group_name"] == "Dairy"
    assert by_name["Mystery Jar"]["product_group_id"] is None
    names = [g["name"] for g in body["_product_groups"]]
    assert names == ["Dairy", "Holiday Baking", "produce"]


def test_satellite_grocy_proxy_allows_the_product_group_read():
    from app.services import proxy_policy
    verdict, _ = proxy_policy.classify("grocy", "GET", "api/objects/product_groups")
    assert verdict == proxy_policy.VERDICT_ALLOW
