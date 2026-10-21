"""Community barcode fixes on the app side (FoodAssistant-mypf0).

Covers what a correction may carry (build_correction), the opt-in and link
gates, the upload, the feed and the bundled release copy, the lookup order in
services/barcode.py, and the capture on the Pending review path with Grocy
mocked (mirroring tests/test_expiry_learning_pending.py).
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "service"))

from app.models.food import FoodCategory, FoodItem  # noqa: E402
from app.services import barcode_corrections, community_barcodes  # noqa: E402
from app.services import barcode as barcode_svc  # noqa: E402

_SERVICE_DIR = Path(__file__).parent.parent / "service"
_REPO = Path(__file__).parent.parent
CODE = "078000082401"


def _bc(**kw):
    args = dict(barcode=CODE, name="Dr Pepper Zero Sugar", brand="Dr Pepper",
                category="Beverages", was_name="Zero sugar", was_brand=None,
                was_category="Beverages")
    args.update(kw)
    return barcode_corrections.build_correction(**args)


# --- What a correction carries ------------------------------------------------

def test_a_fix_carries_only_the_product_fields():
    c = _bc()
    assert c == {"barcode": CODE, "name": "Dr Pepper Zero Sugar",
                 "brand": "Dr Pepper", "category": "Beverages",
                 "was_name": "Zero sugar", "was_brand": None}


def test_an_untouched_row_is_not_a_fix():
    assert _bc(name="Zero  Sugar", brand=None, was_name="zero sugar") is None


def test_a_brand_or_category_change_alone_is_a_fix():
    assert _bc(name="Zero sugar", brand="Dr Pepper", was_brand=None) is not None
    assert _bc(name="Zero sugar", brand=None, category="Snacks") is not None


def test_a_name_typed_for_an_unknown_scan_is_shared():
    c = _bc(was_name=None, was_category=None)
    assert c["was_name"] is None and c["name"] == "Dr Pepper Zero Sugar"


@pytest.mark.parametrize("code", ["", "abc123456", "12345", "212345678901",
                                  "2001234567890"])
def test_no_fix_without_a_shareable_barcode(code):
    # Missing, non-numeric, too short, and store-assigned codes.
    assert _bc(barcode=code) is None


@pytest.mark.parametrize("name", ["Unknown (078000082401)",
                                  "Store barcode (2123) - take a photo instead",
                                  "12345", "x", "Buy at cheap.com",
                                  "<b>Soda</b>", "Soda www.spam", "a@b.co soda"])
def test_placeholder_or_unsafe_names_are_not_shared(name):
    assert _bc(name=name) is None


def test_ai_guess_suffix_and_odd_category_are_cleaned():
    c = _bc(name="Root Beer (unverified guess)", category="Sodas",
            was_name="Unknown (078000082401)")
    assert c["name"] == "Root Beer" and c["category"] is None
    assert c["was_name"] is None


# --- Opt-in, link and queue ------------------------------------------------------

@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "share_barcode_corrections", True, raising=False)
    monkeypatch.setattr(settings, "cloud_instance_token", "inst-token", raising=False)
    monkeypatch.setattr(settings, "use_community_barcodes", True, raising=False)
    monkeypatch.setattr(settings, "cloud_base_url", "https://forager.test", raising=False)
    community_barcodes.clear_cache()
    yield settings
    community_barcodes.clear_cache()


def _record(**kw):
    args = dict(barcode=CODE, name="Dr Pepper Zero Sugar", brand="Dr Pepper",
                category="Beverages", was_name="Zero sugar", was_brand=None,
                was_category="Beverages")
    args.update(kw)
    return barcode_corrections.record(**args)


def test_nothing_is_captured_with_sharing_off(data_dir, monkeypatch):
    monkeypatch.setattr(data_dir, "share_barcode_corrections", False)
    assert _record() is False
    assert barcode_corrections.queued() == []


def test_nothing_is_captured_without_a_linked_kitchen(data_dir, monkeypatch):
    monkeypatch.setattr(data_dir, "cloud_instance_token", "")
    assert _record() is False
    assert barcode_corrections.queued() == []


def test_a_newer_fix_replaces_the_queued_one(data_dir):
    assert _record(name="Dr Pepper Zero") is True
    assert _record(name="Dr Pepper Zero Sugar") is True
    assert [c["name"] for c in barcode_corrections.queued()] == ["Dr Pepper Zero Sugar"]


def _transport(status, seen):
    def handler(request):
        seen.append(request)
        return httpx.Response(status, json={"accepted": 1})
    return httpx.MockTransport(handler)


def test_flush_sends_with_the_instance_token(data_dir):
    _record()
    seen = []
    result = asyncio.run(barcode_corrections.flush(_transport(200, seen)))
    assert result == {"sent": 1}
    req = seen[0]
    assert str(req.url) == "https://forager.test/v1/barcodes/corrections"
    assert req.headers["Authorization"] == "Bearer inst-token"
    assert json.loads(req.content)["corrections"][0]["barcode"] == CODE
    assert barcode_corrections.queued() == []


@pytest.mark.parametrize("status,kept", [(500, 1), (429, 1), (404, 1), (422, 0), (403, 0)])
def test_flush_retries_only_what_can_succeed(data_dir, status, kept):
    _record()
    asyncio.run(barcode_corrections.flush(_transport(status, [])))
    assert len(barcode_corrections.queued()) == kept


def test_flush_discards_the_queue_once_sharing_is_off(data_dir, monkeypatch):
    _record()
    monkeypatch.setattr(data_dir, "share_barcode_corrections", False)
    seen = []
    asyncio.run(barcode_corrections.flush(_transport(200, seen)))
    assert seen == [] and barcode_corrections.queued() == []


# --- The feed and the bundled copy --------------------------------------------------

FEED = {"version": 1, "generated_date": "2026-10-20", "barcodes": [
    {"barcode": CODE, "name": "Dr Pepper Zero Sugar", "brand": "Dr Pepper",
     "category": "Beverages", "votes": 4},
    {"barcode": "nope", "name": "Bad"},
    {"barcode": "0123456789012", "name": ""},
    {"barcode": "0123456789013", "name": "Odd", "category": "Sodas"},
]}


def test_build_index_skips_malformed_entries():
    index = community_barcodes.build_index(FEED)
    assert set(index) == {CODE, "0123456789013"}
    assert index["0123456789013"]["category"] is None


def test_newer_bundled_copy_beats_an_old_cache():
    old = {"generated_date": "2026-09-01", "barcodes": []}
    new = {"generated_date": "2026-10-20", "barcodes": []}
    assert community_barcodes.choose_feed(old, new) is new
    assert community_barcodes.choose_feed(new, old) is new
    assert community_barcodes.choose_feed(None, old) is old


def test_refresh_caches_the_feed_for_lookups(data_dir):
    def handler(request):
        assert request.url.path == "/api/barcodes/feed"
        return httpx.Response(200, json=FEED)
    assert asyncio.run(community_barcodes.refresh(httpx.MockTransport(handler)))
    assert community_barcodes.lookup(CODE)["name"] == "Dr Pepper Zero Sugar"
    assert community_barcodes.lookup("999999999999") is None


def test_lookup_honors_the_setting(data_dir, monkeypatch):
    asyncio.run(community_barcodes.refresh(
        httpx.MockTransport(lambda r: httpx.Response(200, json=FEED))))
    monkeypatch.setattr(data_dir, "use_community_barcodes", False)
    assert community_barcodes.lookup(CODE) is None


def test_bundled_release_copy_is_well_formed():
    feed = json.loads((_REPO / "service/app/data/community_barcodes.json").read_text())
    assert feed["version"] == 1 and isinstance(feed["barcodes"], list)
    assert len(community_barcodes.build_index(feed)) == len(feed["barcodes"])


# --- Lookup order ------------------------------------------------------------------

FIX = {"name": "Dr Pepper Zero Sugar", "brand": "Dr Pepper", "category": "Beverages"}


class _FakeClient:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        if self.error:
            raise self.error
        return self.response


def _lookup(monkeypatch, client, fix=FIX):
    monkeypatch.setattr(barcode_svc.httpx, "AsyncClient", client)
    monkeypatch.setattr(barcode_svc, "_community_fix", lambda code: fix)

    async def _no_enrich(*a):
        return False
    monkeypatch.setattr(barcode_svc, "_llm_enrich", _no_enrich)
    monkeypatch.setattr(barcode_svc, "apply_defaults", lambda item, db, **kw: item)
    return asyncio.run(barcode_svc.lookup_barcode(CODE, None))


def test_fix_replaces_the_open_food_facts_name(monkeypatch):
    off = httpx.Response(200, json={"status": "success", "product": {
        "product_name": "zero sugar", "brands": "", "categories_tags": ["en:sodas"]}})
    item = _lookup(monkeypatch, _FakeClient(off))
    assert (item.name, item.brand, item.category) == (
        "Dr Pepper Zero Sugar", "Dr Pepper", FoodCategory.beverages)


def test_fix_answers_a_barcode_open_food_facts_lacks(monkeypatch):
    missing = httpx.Response(404, json={"status": "failure"})
    item = _lookup(monkeypatch, _FakeClient(missing))
    assert item.name == "Dr Pepper Zero Sugar"


def test_fix_answers_while_open_food_facts_is_down(monkeypatch):
    item = _lookup(monkeypatch, _FakeClient(error=httpx.ConnectError("down")))
    assert item.name == "Dr Pepper Zero Sugar"
    with pytest.raises(barcode_svc.BarcodeServiceError):
        _lookup(monkeypatch, _FakeClient(error=httpx.ConnectError("down")), fix=None)


def test_store_local_codes_never_take_a_fix(data_dir):
    assert barcode_svc._community_fix("212345678901") is None


# --- Capture on the Pending review path -------------------------------------------

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    cwd = os.getcwd()
    os.chdir(_SERVICE_DIR)
    try:
        from app.config import settings
        settings.data_dir = str(tmp_path_factory.mktemp("data"))
        from app.main import app
        settings.grocy_base_url = "http://grocy.test"
        settings.grocy_api_key = "test-grocy-key"
        settings.auth_required = False
        settings.auth_password = ""
        with TestClient(app) as c:
            yield c
    finally:
        os.chdir(cwd)


@pytest.fixture
def pending(client, monkeypatch):
    from app.config import settings
    from app.routers import pending as pending_router
    from app.services.grocy import GrocyClient

    async def _empty(self, *a, **kw):
        return []

    async def _import_ok(self, item):
        return {"product_id": 900, "name": item.name}

    async def _no_flush(*a, **kw):
        return {"sent": 0}

    async def _not_in_grocy(barcode, db):
        return None

    async def _off(barcode, db):
        return FoodItem(name="Zero sugar", category=FoodCategory.beverages)

    monkeypatch.setattr(GrocyClient, "get_stock", _empty)
    monkeypatch.setattr(GrocyClient, "import_item", _import_ok)
    monkeypatch.setattr(barcode_corrections, "flush", _no_flush)
    monkeypatch.setattr(pending_router, "grocy_barcode_item", _not_in_grocy)
    monkeypatch.setattr(pending_router, "lookup_barcode", _off)
    monkeypatch.setattr(pending_router, "_spawn_enrichment", lambda *a: None)
    monkeypatch.setattr(settings, "share_barcode_corrections", True, raising=False)
    monkeypatch.setattr(settings, "cloud_instance_token", "inst-token", raising=False)
    for row in client.get("pending/").json()["items"]:
        client.delete(f"pending/{row['id']}")
    barcode_corrections.clear_queue()
    yield pending_router
    barcode_corrections.clear_queue()


def _scan(client, router):
    item = client.post("pending/scan", json={"barcode": CODE}).json()["item"]
    asyncio.run(router.enrich_pending_item(item["id"], CODE))
    return item["id"]


def test_a_fix_on_review_is_queued_on_commit(client, pending):
    item_id = _scan(client, pending)
    client.patch(f"pending/{item_id}", json={"name": "Dr Pepper Zero Sugar",
                                             "brand": "Dr Pepper"})
    assert client.post("pending/commit", json={"ids": [item_id]}).json()["imported"] == 1
    assert barcode_corrections.queued() == [{
        "barcode": CODE, "name": "Dr Pepper Zero Sugar", "brand": "Dr Pepper",
        "category": "Beverages", "was_name": "Zero sugar", "was_brand": None}]


def test_an_unchanged_row_sends_nothing(client, pending):
    item_id = _scan(client, pending)
    client.post("pending/commit", json={"ids": [item_id]})
    assert barcode_corrections.queued() == []


def test_a_product_from_the_users_own_grocy_is_never_sent(client, pending, monkeypatch):
    from app.services.grocy import GrocyClient

    async def _known(barcode, db):
        return (FoodItem(name="Tea, my own name", category=FoodCategory.beverages),
                {"amount": 1, "product_id": 5, "location_id": 3,
                 "location": "Refrigerator"})

    async def _add(self, *a, **kw):
        return {"product_id": 5, "name": "Tea"}
    monkeypatch.setattr(pending, "grocy_barcode_item", _known)
    monkeypatch.setattr(GrocyClient, "add_to_product", _add)
    item_id = _scan(client, pending)
    # A rename on review drops the Grocy link, but it is still the user's own
    # naming, not a fix to the public catalog.
    client.patch(f"pending/{item_id}", json={"name": "Pure Leaf Tea"})
    client.post("pending/commit", json={"ids": [item_id]})
    assert barcode_corrections.queued() == []
