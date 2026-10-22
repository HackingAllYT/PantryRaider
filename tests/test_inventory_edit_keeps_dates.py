"""The Inventory Edit window's server side leaves best-by dates alone unless
a date is sent.

The window sends ``best_before_date: null`` when only the category changed
(tests/browser/test_inventory_edit_dates.py pins that), because a date is
written onto every stock entry of the product. These tests pin the other half:
the route passes the null on as None, and an edit without a date writes no
stock entry, so a product kept in two places keeps both of its dates.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import settings  # noqa: E402
from app.services import grocy as grocy_svc  # noqa: E402
from app.services.grocy import GrocyClient  # noqa: E402
from test_grocy_stock_edits import FREEZER, FRIDGE, FakeGrocy, _d  # noqa: E402


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


@pytest.fixture
def grocy(monkeypatch):
    fake = FakeGrocy()
    monkeypatch.setattr(settings, "deployment_mode", "server", raising=False)
    monkeypatch.setattr(settings, "grocy_base_url", "http://grocy.test", raising=False)
    monkeypatch.setattr(settings, "grocy_api_key", "test-key", raising=False)
    monkeypatch.setattr(grocy_svc, "_client", fake)
    return fake


def test_a_category_only_edit_reaches_the_client_without_a_date(client):
    seen = {}

    async def fake_edit(self, product_id, category=None, best_before_date=None):
        seen.update(product_id=product_id, category=category,
                    best_before_date=best_before_date)
        return {"product_id": product_id}

    with patch.object(GrocyClient, "edit_product", fake_edit):
        r = client.patch("/inventory/edit/1",
                         json={"category": "Snacks", "best_before_date": None})
    assert r.status_code == 200
    assert seen == {"product_id": 1, "category": "Snacks", "best_before_date": None}


def test_an_edit_without_a_date_writes_no_stock_entry(grocy, monkeypatch):
    grocy.add(1, _d(3), FRIDGE)
    grocy.add(3, _d(90), FREEZER)

    async def group_id(self, name):
        return 7

    monkeypatch.setattr(GrocyClient, "ensure_product_group", group_id)
    asyncio.run(GrocyClient().edit_product(1, category="Snacks"))
    assert grocy.entry_writes() == []
    assert grocy.at(FRIDGE) == [(_d(3), 1)]
    assert grocy.at(FREEZER) == [(_d(90), 3)]
    # The category itself still lands on the product.
    assert grocy.sent("PUT", "/objects/products/1") == [{"product_group_id": 7}]
