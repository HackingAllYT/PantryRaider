"""Community barcode corrections: submission validation and limits, the
one-vote rule, the pure trust and consensus rules (including the sybil
cases), the public feed, the admin review queue, and the trust control."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import barcode_trust as bt
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import Account, AdminAction, BarcodeCorrection, BarcodeDecision, Instance
from app.routers import barcodes
from app.routers.admin import barcode_in_filter
from app.security import token_hash
from tests.conftest import make_admin_ready

CODE = "0012345678905"
OLD = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat(timespec="seconds")
NEW = datetime.now(timezone.utc).isoformat(timespec="seconds")


@pytest.fixture(autouse=True)
def _fresh_feed():
    barcodes.reset_feed_cache()
    yield
    barcodes.reset_feed_cache()


def _kitchen(email, created_at=OLD, verified=1, trust=""):
    """An account with one linked kitchen; returns (account id, bearer)."""
    db = SessionLocal()
    try:
        account = Account(email=email, password_hash="x", created_at=created_at,
                          email_verified=verified, barcode_trust=trust)
        db.add(account)
        db.commit()
        token = f"tok-{email}"
        db.add(Instance(token_hash=token_hash(token), account_id=account.id,
                        name="Kitchen", created_at=created_at))
        db.commit()
        return account.id, token
    finally:
        db.close()


def _send(client, token, *items):
    return client.post("/v1/barcodes/corrections",
                       json={"corrections": list(items)},
                       headers={"Authorization": f"Bearer {token}"})


def _fix(barcode=CODE, name="Heinz Tomato Ketchup", **extra):
    return {"barcode": barcode, "name": name, "brand": "Heinz",
            "category": "Condiments", **extra}


def _feed(client):
    barcodes.reset_feed_cache()
    return client.get("/api/barcodes/feed").json()


# --- Submission -------------------------------------------------------------

def test_needs_a_linked_kitchen(client):
    resp = client.post("/v1/barcodes/corrections", json={"corrections": [_fix()]})
    assert resp.status_code == 401


def test_accepts_and_stores_a_correction(client):
    aid, token = _kitchen("a@example.com")
    resp = _send(client, token, _fix(was_name="Tomato Soup", was_brand="Campbell's"))
    assert resp.status_code == 200 and resp.json() == {"accepted": 1}
    db = SessionLocal()
    try:
        row = db.query(BarcodeCorrection).one()
        assert (row.account_id, row.name, row.brand, row.category) == (
            aid, "Heinz Tomato Ketchup", "Heinz", "Condiments")
        assert row.name_key == "tomato ketchup"
        assert row.was_name == "Tomato Soup" and row.status == "active"
    finally:
        db.close()


@pytest.mark.parametrize("item", [
    _fix(barcode="12345"),                         # too short
    _fix(barcode="12345678901a"),                  # not digits
    _fix(name="x"),                                # too short
    _fix(name="y" * 121),                          # too long
    _fix(name="12345 678"),                        # no letters
    _fix(name="Buy at cheapketchup.com"),          # bare domain
    _fix(name="see https://example.org"),          # link
    _fix(name="www.example"),                      # www.
    _fix(name="mail me@example.com"),              # email
    _fix(name="<b>Ketchup</b>"),                   # markup
    _fix(name="Ketchup\x07"),                      # control character
    _fix(brand="B" * 81),                          # brand too long
    _fix(brand="spam.com"),                        # link in the brand
    _fix(category="Candy"),                        # not a category
])
def test_rejects_a_bad_item_and_the_whole_batch(client, item):
    _, token = _kitchen("a@example.com")
    resp = _send(client, token, _fix(barcode="0099999999990"), item)
    assert resp.status_code == 422
    assert resp.json()["detail"] == "The submitted batch is not valid."
    db = SessionLocal()
    try:
        assert db.query(BarcodeCorrection).count() == 0
    finally:
        db.close()


def test_empty_and_oversized_batches_are_refused(client):
    _, token = _kitchen("a@example.com")
    assert _send(client, token).status_code == 422
    many = [_fix(barcode=f"{i:013d}") for i in range(1, 52)]
    assert _send(client, token, *many).status_code == 422


def test_bad_reviewer_context_is_dropped_not_refused(client):
    _, token = _kitchen("a@example.com")
    assert _send(client, token, _fix(was_name="<script>", was_brand="x.com")).status_code == 200
    db = SessionLocal()
    try:
        row = db.query(BarcodeCorrection).one()
        assert row.was_name is None and row.was_brand is None
    finally:
        db.close()


def test_one_vote_per_account_even_across_kitchens(client):
    aid, token = _kitchen("a@example.com")
    db = SessionLocal()
    try:
        db.add(Instance(token_hash=token_hash("second"), account_id=aid,
                        name="Cabin", created_at=OLD))
        db.commit()
    finally:
        db.close()
    _send(client, token, _fix(name="Ketchup"))
    # Same barcode twice in one batch: the later answer stands.
    _send(client, "second", _fix(name="Mustard"), _fix(name="Heinz Ketchup 20 oz"))
    db = SessionLocal()
    try:
        rows = db.query(BarcodeCorrection).all()
        assert len(rows) == 1 and rows[0].name == "Heinz Ketchup 20 oz"
    finally:
        db.close()


def test_resending_does_not_clear_a_rejection(client):
    aid, token = _kitchen("a@example.com")
    _send(client, token, _fix())
    db = SessionLocal()
    try:
        db.query(BarcodeCorrection).update({"status": "rejected"})
        db.commit()
    finally:
        db.close()
    _send(client, token, _fix(name="Something Else"))
    db = SessionLocal()
    try:
        assert db.query(BarcodeCorrection).one().status == "rejected"
    finally:
        db.close()


def test_blocked_account_is_refused(client):
    _, token = _kitchen("a@example.com", trust="blocked")
    resp = _send(client, token, _fix())
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "barcode_sharing_blocked"


def test_requests_are_rate_limited_per_account(client, monkeypatch):
    monkeypatch.setattr(settings, "barcode_rate_per_minute", 2)
    _, token = _kitchen("a@example.com")
    for i in range(2):
        assert _send(client, token, _fix(barcode=f"{i + 1:013d}")).status_code == 200
    assert _send(client, token, _fix()).status_code == 429


def test_daily_cap_counts_barcodes_not_requests(client, monkeypatch):
    monkeypatch.setattr(settings, "barcode_corrections_per_day", 2)
    _, token = _kitchen("a@example.com")
    assert _send(client, token, _fix(barcode="0000000000017"),
                 _fix(barcode="0000000000024")).status_code == 200
    # Re-sending a barcode already corrected today costs nothing more.
    assert _send(client, token, _fix(barcode="0000000000017",
                                     name="Better Name")).status_code == 200
    assert _send(client, token, _fix(barcode="0000000000031")).status_code == 429


# --- Pure rules -------------------------------------------------------------

def test_name_key_folds_case_punctuation_and_a_leading_brand():
    assert bt.name_key("Heinz Ketchup!", "Heinz") == "ketchup"
    assert bt.name_key("ketchup", "Heinz") == "ketchup"
    assert bt.name_key("  HEINZ   ketchup ", "heinz") == "ketchup"
    # A name that is only the brand keeps it.
    assert bt.name_key("Heinz", "Heinz") == "heinz"


def test_vote_weight_rules():
    assert bt.vote_weight("blocked", 400, True, agree=50) == 0
    assert bt.vote_weight("trusted", 0, False, rejected=5) == bt.TRUSTED_WEIGHT
    assert bt.vote_weight("", 400, True) == 1.0
    assert bt.vote_weight("", 3, True) == 0.5
    assert bt.vote_weight("", 3, False) == 0.25
    # A good record raises the weight, toward 2 and never past it, even with
    # a long record behind it.
    assert 1.0 < bt.vote_weight("", 400, True, agree=3) < 2.0
    assert 1.99 < bt.vote_weight("", 400, True, agree=1000) <= 2.0
    # A rejection costs three disagreements.
    assert (bt.vote_weight("", 400, True, rejected=1)
            == bt.vote_weight("", 400, True, disagree=3))


def _vote(aid, name, weight=1.0, trusted=False, brand="Heinz"):
    return {"account_id": aid, "name_key": bt.name_key(name, brand), "name": name,
            "brand": brand, "category": "Condiments", "updated_at": NEW,
            "weight": weight, "trusted": trusted}


def test_one_account_cannot_publish_alone():
    verdict = bt.consensus([_vote(1, "Ketchup", weight=2.0)])
    assert not verdict["published"]


def test_two_established_accounts_publish():
    verdict = bt.consensus([_vote(1, "Heinz Ketchup"), _vote(2, "Ketchup")])
    assert verdict["published"]
    assert verdict["result"]["votes"] == 2
    assert verdict["result"]["brand"] == "Heinz"


def test_a_trusted_account_can_publish_alone():
    verdict = bt.consensus([_vote(1, "Ketchup", weight=3.0, trusted=True)])
    assert verdict["published"]


def test_two_fresh_unverified_accounts_do_not_publish():
    verdict = bt.consensus([_vote(1, "Ketchup", 0.25), _vote(2, "Ketchup", 0.25)])
    assert not verdict["published"]


def test_without_a_clear_majority_nothing_publishes():
    verdict = bt.consensus([_vote(1, "Ketchup"), _vote(2, "Ketchup"),
                            _vote(3, "Mustard"), _vote(4, "Mustard", 0.5)])
    assert not verdict["published"]


def test_published_values_are_the_most_common_spelling():
    votes = [_vote(1, "Heinz Tomato Ketchup"), _vote(2, "Tomato Ketchup"),
             _vote(3, "Tomato Ketchup")]
    assert bt.consensus(votes)["result"]["name"] == "Tomato Ketchup"


def _row(barcode, aid, name, brand="Heinz", status="active"):
    return {"barcode": barcode, "account_id": aid, "name": name,
            "name_key": bt.name_key(name, brand), "brand": brand,
            "category": "Condiments", "status": status, "updated_at": NEW}


def _facts(*ids, age=400, verified=True, trust=""):
    return {i: {"trust": trust, "age_days": age, "email_verified": verified}
            for i in ids}


def test_many_fresh_accounts_cannot_outvote_two_established_ones():
    # Twelve throwaway accounts (a quarter vote each, 3.0 in all) against two
    # established ones (2.0): the throwaways lead on weight but never reach a
    # two-thirds majority, so their answer does not publish.
    rows = ([_row(CODE, 1, "Ketchup"), _row(CODE, 2, "Ketchup")]
            + [_row(CODE, 10 + i, "Free Money Ketchup") for i in range(12)])
    accounts = {**_facts(1, 2), **_facts(*range(10, 22), age=1, verified=False)}
    verdicts, _ = bt.evaluate(rows, accounts, {})
    assert not verdicts[CODE]["published"]
    assert bt.feed_entries(verdicts) == []


def test_established_majority_wins_over_a_few_fresh_accounts():
    rows = ([_row(CODE, 1, "Ketchup"), _row(CODE, 2, "Ketchup"), _row(CODE, 3, "Ketchup")]
            + [_row(CODE, 10 + i, "Spam Sauce") for i in range(4)])
    accounts = {**_facts(1, 2, 3), **_facts(*range(10, 14), age=1, verified=False)}
    verdicts, weights = bt.evaluate(rows, accounts, {})
    assert verdicts[CODE]["published"]
    assert verdicts[CODE]["result"]["name_key"] == "ketchup"
    # The dissenters lost weight in the second pass; the majority gained.
    assert weights[10] < 0.25 and weights[1] > 1.0


def test_admin_approval_publishes_and_counts_against_dissenters():
    rows = [_row(CODE, 1, "Mustard"), _row("0099999999990", 1, "Relish"),
            _row("0099999999990", 2, "Relish")]
    decisions = {CODE: {"action": "approved", "name": "Heinz Ketchup",
                        "brand": "Heinz", "category": "Condiments"}}
    verdicts, weights = bt.evaluate(rows, _facts(1, 2), decisions)
    assert verdicts[CODE]["published"]
    assert verdicts[CODE]["result"]["name"] == "Heinz Ketchup"
    entry = {e["barcode"]: e for e in bt.feed_entries(verdicts)}[CODE]
    assert entry == {"barcode": CODE, "name": "Heinz Ketchup", "brand": "Heinz",
                     "category": "Condiments", "votes": 0}
    # Account 1 agreed once (Relish) and disagreed once (Mustard): neutral.
    assert weights[1] == pytest.approx(1.0)


def test_admin_rejection_never_publishes_and_costs_trust():
    rows = [_row(CODE, 1, "Ketchup", status="rejected"),
            _row(CODE, 2, "Ketchup", status="rejected"),
            _row("0099999999990", 1, "Relish"), _row("0099999999990", 3, "Relish")]
    decisions = {CODE: {"action": "rejected"}}
    verdicts, weights = bt.evaluate(rows, _facts(1, 2, 3), decisions)
    assert not verdicts[CODE]["published"]
    assert weights[2] < weights[3]
    assert weights[1] < weights[3]


# --- Feed -------------------------------------------------------------------

def test_feed_publishes_agreed_barcodes_only(client):
    _, a = _kitchen("a@example.com")
    _, b = _kitchen("b@example.com")
    _send(client, a, _fix(name="Heinz Ketchup"), _fix(barcode="0099999999990", name="Lonely"))
    _send(client, b, _fix(name="Ketchup"))
    feed = _feed(client)
    assert feed["version"] == 1 and len(feed["generated_date"]) == 10
    assert feed["barcodes"] == [{"barcode": CODE, "name": "Heinz Ketchup",
                                 "brand": "Heinz", "category": "Condiments",
                                 "votes": 2}]


def test_feed_ignores_disabled_accounts(client):
    aid, a = _kitchen("a@example.com")
    _, b = _kitchen("b@example.com")
    _send(client, a, _fix())
    _send(client, b, _fix())
    db = SessionLocal()
    try:
        db.get(Account, aid).disabled = 1
        db.commit()
    finally:
        db.close()
    assert _feed(client)["barcodes"] == []


def test_feed_is_cached_until_reset(client):
    _, a = _kitchen("a@example.com")
    _, b = _kitchen("b@example.com")
    assert _feed(client)["barcodes"] == []
    _send(client, a, _fix())
    _send(client, b, _fix())
    assert client.get("/api/barcodes/feed").json()["barcodes"] == []
    assert len(_feed(client)["barcodes"]) == 1


# --- Admin ------------------------------------------------------------------

ADMIN = {"email": "dan@example.com", "password": "hunter2222",
         "confirm_password": "hunter2222"}


@pytest.fixture
def admin_client(monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", "dan@example.com")
    c = TestClient(app)
    assert c.post("/signup", data=ADMIN, follow_redirects=False).status_code == 303
    make_admin_ready()
    return c


def test_queue_is_admin_only(client):
    assert client.get("/admin/barcodes").status_code == 404


def test_queue_lists_contested_barcodes_with_voters(admin_client):
    _, a = _kitchen("a@example.com")
    _send(admin_client, a, _fix(was_name="Tomato Soup"))
    page = admin_client.get("/admin/barcodes")
    assert page.status_code == 200
    assert CODE in page.text and "a@example.com" in page.text
    assert "Tomato Soup" in page.text
    assert CODE not in admin_client.get("/admin/barcodes?status=published").text


def test_approve_reject_and_clear(admin_client):
    _, a = _kitchen("a@example.com")
    _send(admin_client, a, _fix(name="Mustard"))
    resp = admin_client.post(f"/admin/barcodes/{CODE}/approve",
                             data={"name": "Heinz Ketchup", "brand": "Heinz",
                                   "category": "Condiments"},
                             follow_redirects=False)
    assert resp.status_code == 303
    # The decision shows at once: the cache is reset by the ruling.
    assert admin_client.get("/api/barcodes/feed").json()["barcodes"][0]["name"] == "Heinz Ketchup"

    admin_client.post(f"/admin/barcodes/{CODE}/reject", follow_redirects=False)
    assert admin_client.get("/api/barcodes/feed").json()["barcodes"] == []
    db = SessionLocal()
    try:
        assert db.query(BarcodeCorrection).one().status == "rejected"
        assert db.query(BarcodeDecision).one().action == "rejected"
    finally:
        db.close()
    assert CODE in admin_client.get("/admin/barcodes?status=decided").text

    admin_client.post(f"/admin/barcodes/{CODE}/clear", follow_redirects=False)
    db = SessionLocal()
    try:
        assert db.query(BarcodeDecision).count() == 0
        assert db.query(BarcodeCorrection).one().status == "active"
        actions = [a.action for a in db.query(AdminAction).order_by(AdminAction.id)]
        assert actions == ["barcode-approve", "barcode-reject", "barcode-clear"]
    finally:
        db.close()


def test_approve_refuses_unsafe_text(admin_client):
    resp = admin_client.post(f"/admin/barcodes/{CODE}/approve",
                             data={"name": "visit spam.com"})
    assert resp.status_code == 400


def test_barcode_trust_control(admin_client):
    aid, token = _kitchen("a@example.com")
    page = admin_client.get(f"/admin/accounts/{aid}")
    assert "Trust barcode votes" in page.text
    admin_client.post(f"/admin/accounts/{aid}/barcode-trust",
                      data={"mode": "trusted"}, follow_redirects=False)
    _send(admin_client, token, _fix())
    # A trusted account publishes on its own.
    assert len(admin_client.get("/api/barcodes/feed").json()["barcodes"]) == 1
    admin_client.post(f"/admin/accounts/{aid}/barcode-trust",
                      data={"mode": "blocked"}, follow_redirects=False)
    assert admin_client.get("/api/barcodes/feed").json()["barcodes"] == []
    assert _send(admin_client, token, _fix()).status_code == 403
    assert admin_client.post(f"/admin/accounts/{aid}/barcode-trust",
                             data={"mode": "nonsense"}).status_code == 400
    db = SessionLocal()
    try:
        assert db.get(Account, aid).barcode_trust == "blocked"
    finally:
        db.close()


def test_filter_views():
    contested = {"groups": [{}], "published": False, "share": 0.5, "decision": None}
    published = {"groups": [{}], "published": True, "share": 1.0, "decision": None}
    decided = {"groups": [], "published": True, "share": 0.0,
               "decision": {"action": "approved"}}
    assert barcode_in_filter(contested, "contested")
    assert not barcode_in_filter(published, "contested")
    assert barcode_in_filter(published, "published")
    assert barcode_in_filter(decided, "decided")
    assert not barcode_in_filter(decided, "contested")
    assert all(barcode_in_filter(v, "all") for v in (contested, published, decided))
