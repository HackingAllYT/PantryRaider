"""Community barcode corrections: submissions from linked kitchens and the
public feed.

POST /v1/barcodes/corrections takes a small batch of corrections from a
Pantry Raider install whose owner opted into sharing them: for each barcode,
the name (and brand and category) the owner settled on after the lookup got
it wrong or found nothing. It needs a linked kitchen (the instance token),
because corrections are free text: every vote has to belong to an account
that can be weighed, and blocked when it abuses the feed. Validation is
strict and a batch with one bad item is refused whole, the same stance as
the shelf-life endpoint.

GET /api/barcodes/feed publishes the barcodes the community agrees on, after
the trust rules in barcode_trust.py, for every app to download. It is public
and names nobody: a feed row carries the product and a vote count, never who
voted. Like the shelf-life feed it is built on request behind a short
in-process cache; admin decisions reset it so a ruling shows at once.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import barcode_trust, ratelimit
from ..config import settings
from ..deps import current_instance, get_db, utc_now_iso
from ..models import Account, BarcodeCorrection, BarcodeDecision, Instance

router = APIRouter(tags=["barcodes"])

INVALID_BATCH = "The submitted batch is not valid."
RATE_LIMITED = ("Too many barcode corrections from this account. "
                "They will send again later.")
BLOCKED_MESSAGE = ("Barcode corrections from this account are no longer "
                   "accepted. Contact support@pantryraider.app if you think "
                   "this is a mistake.")


class CorrectionBatch(BaseModel):
    # Plain dicts: barcode_trust.clean_correction checks every field itself,
    # so a wrong type is refused with the same message as a bad value.
    corrections: list[dict] = []


# --- Evaluation ----------------------------------------------------------------

def correction_rows(db: Session) -> list[dict]:
    """Every stored correction as the plain dicts barcode_trust reads."""
    query = db.query(BarcodeCorrection.barcode, BarcodeCorrection.account_id,
                     BarcodeCorrection.name_key, BarcodeCorrection.name,
                     BarcodeCorrection.brand, BarcodeCorrection.category,
                     BarcodeCorrection.was_name, BarcodeCorrection.was_brand,
                     BarcodeCorrection.status, BarcodeCorrection.updated_at)
    return [{"barcode": b, "account_id": a, "name_key": k, "name": n,
             "brand": br, "category": c, "was_name": wn, "was_brand": wb,
             "status": st, "updated_at": u}
            for (b, a, k, n, br, c, wn, wb, st, u) in query]


def account_facts(db: Session, account_ids: set[int]) -> dict[int, dict]:
    """What a vote weight is worked out from, for the given accounts."""
    if not account_ids:
        return {}
    now = datetime.now(timezone.utc)
    rows = (db.query(Account.id, Account.barcode_trust, Account.created_at,
                     Account.email_verified, Account.disabled)
            .filter(Account.id.in_(account_ids)))
    return {aid: {
        # A disabled account's votes count for nothing, like a blocked one.
        "trust": "blocked" if disabled else (trust or ""),
        "age_days": barcode_trust.account_age_days(created, now),
        "email_verified": bool(verified),
    } for aid, trust, created, verified, disabled in rows}


def decision_map(db: Session) -> dict[str, dict]:
    return {d.barcode: {"action": d.action, "name": d.name, "brand": d.brand,
                        "category": d.category, "note": d.note,
                        "decided_at": d.decided_at}
            for d in db.query(BarcodeDecision)}


def evaluate_all(db: Session):
    """(rows, verdicts, weights) for the whole table: what the feed and the
    admin queue both read."""
    rows = correction_rows(db)
    facts = account_facts(db, {r["account_id"] for r in rows})
    verdicts, weights = barcode_trust.evaluate(rows, facts, decision_map(db))
    return rows, verdicts, weights


# --- Feed cache ----------------------------------------------------------------

_FEED_TTL_SECONDS = 900
_cache_lock = threading.Lock()
_cached_feed: dict | None = None
_cached_at: float = 0.0


def reset_feed_cache() -> None:
    """Forget the cached feed (tests, and after every admin decision)."""
    global _cached_feed, _cached_at
    with _cache_lock:
        _cached_feed = None
        _cached_at = 0.0


# --- Routes --------------------------------------------------------------------

@router.post("/v1/barcodes/corrections")
def submit_corrections(batch: CorrectionBatch, request: Request,
                       inst: Instance = Depends(current_instance),
                       db: Session = Depends(get_db)):
    """Accept a batch of barcode corrections from a linked kitchen."""
    account = db.get(Account, inst.account_id)
    if account is None:
        raise HTTPException(401, detail="Invalid instance token")
    if account.barcode_trust == "blocked":
        raise HTTPException(403, detail={"error": "barcode_sharing_blocked",
                                         "message": BLOCKED_MESSAGE})
    if not ratelimit.allow(f"barcodes-acct:{account.id}",
                           settings.barcode_rate_per_minute):
        raise HTTPException(429, detail=RATE_LIMITED)
    if not batch.corrections or len(batch.corrections) > barcode_trust.MAX_CORRECTIONS:
        raise HTTPException(422, detail=INVALID_BATCH)

    cleaned: dict[str, dict] = {}
    for item in batch.corrections:
        row = barcode_trust.clean_correction(item) if isinstance(item, dict) else None
        if row is None:
            # A well-behaved app never sends a bad item, so one signals a bad
            # or hostile client; refuse the lot rather than pick through it.
            raise HTTPException(422, detail=INVALID_BATCH)
        # A barcode corrected twice in one batch: the later answer stands.
        cleaned[row["barcode"]] = row

    # The daily cap counts barcodes this account touched today, read from
    # the table itself so it holds across restarts. Re-sending a barcode
    # already corrected today costs nothing extra.
    today = datetime.now(timezone.utc).date().isoformat()
    existing = {c.barcode: c for c in db.query(BarcodeCorrection)
                .filter(BarcodeCorrection.account_id == account.id,
                        BarcodeCorrection.barcode.in_(list(cleaned)))}
    touched_today = {b for (b,) in db.query(BarcodeCorrection.barcode)
                     .filter(BarcodeCorrection.account_id == account.id,
                             BarcodeCorrection.updated_at >= today)}
    new_today = {b for b in cleaned if b not in touched_today}
    cap = settings.barcode_corrections_per_day
    if cap > 0 and len(touched_today) + len(new_today) > cap:
        raise HTTPException(429, detail=RATE_LIMITED)

    now = utc_now_iso()
    for barcode, row in cleaned.items():
        vote = existing.get(barcode)
        if vote is None:
            db.add(BarcodeCorrection(account_id=account.id, instance_id=inst.id,
                                     status="active", created_at=now,
                                     updated_at=now, **row))
            continue
        # One vote per account: the new answer replaces the old one. A vote
        # an admin rejected stays rejected, so re-sending cannot wash away
        # the penalty or slip the answer back into the count.
        for field, value in row.items():
            setattr(vote, field, value)
        vote.instance_id = inst.id
        vote.updated_at = now
    db.commit()
    return {"accepted": len(cleaned)}


@router.get("/api/barcodes/feed")
def barcode_feed(db: Session = Depends(get_db)):
    """The published community barcodes the apps download daily."""
    global _cached_feed, _cached_at
    with _cache_lock:
        if _cached_feed is not None and time.time() - _cached_at < _FEED_TTL_SECONDS:
            return _cached_feed
        _, verdicts, _ = evaluate_all(db)
        feed = {
            "version": 1,
            "generated_date": datetime.now(timezone.utc).date().isoformat(),
            "barcodes": barcode_trust.feed_entries(verdicts),
        }
        _cached_feed = feed
        _cached_at = time.time()
        return feed
