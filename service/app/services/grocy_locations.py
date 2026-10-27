"""Opt-in tidy-ups of the Grocy locations older installs were left with.

Pantry Raider used to file fridge stock in a "Refrigerator" of its own next to
the "Fridge" a fresh Grocy ships with, and created its "Freezer" without
Grocy's freezer flag. New installs no longer do either (see
GrocyClient.ensure_location). An install that already has them gets one inbox
item for each, and nothing changes in Grocy until its button is pressed:

* Two fridges: move everything in Fridge to Refrigerator, point the products
  that live in Fridge at Refrigerator, then hide Fridge (GrocyClient.
  merge_location). Only Grocy's own name for a fridge counts (the
  _LOCATION_ALIASES of grocy.py), never a place a custom storage category
  owns, so a "Garage Fridge" is left alone.
* Freezer flag: mark Freezer as a freezer in Grocy.

Each item is raised once. Done or archived, it stays closed, and an open one
closes itself once its condition no longer holds (fixed by hand in Grocy, say).
All of it runs on the server that owns the Grocy connection: a Bandit's inbox
is its main server's, and the buttons refuse to run on a Bandit.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from ..config import settings
from ..models.db_models import ActionItem
from ..storage_categories import custom_categories, location_for
from . import action_items

logger = logging.getLogger(__name__)

KIND_TWO_FRIDGES = "grocy_two_fridges"
KIND_FREEZER_FLAG = "grocy_freezer_flag"
DEDUPE_TWO_FRIDGES = "grocy-two-fridges-v1"
DEDUPE_FREEZER_FLAG = "grocy-freezer-flag-v1"

SATELLITE_MSG = "Grocy is managed on the main server."

# One merge or flag at a time: a second press (another screen, a double tap)
# waits and then finds the work done.
_lock = asyncio.Lock()


def _flag(row: dict, key: str, default: int = 0) -> bool:
    value = row.get(key)
    return str(default if value is None else value) not in ("0", "", "False", "false")


def _active(row: dict) -> bool:
    return _flag(row, "active", 1)


def _named(locations: list[dict], name: str | None) -> dict | None:
    low = (name or "").strip().lower()
    if not low:
        return None
    return next((r for r in locations or []
                 if str(r.get("name") or "").strip().lower() == low), None)


def two_fridges(locations: list[dict]) -> dict | None:
    """The fridge to merge and where to, or None. Pure.

    Pantry Raider's refrigerated place and Grocy's own fridge (the alias
    ensure_location would have used), both active and neither a freezer in
    Grocy. A place a custom storage category files stock in is never merged.
    """
    from .grocy import _LOCATION_ALIASES
    to_name = location_for("refrigerated")
    to = _named(locations, to_name)
    if to is None or not _active(to) or _flag(to, "is_freezer"):
        return None
    owned = {c["location"].strip().lower() for c in custom_categories()}
    for alias in _LOCATION_ALIASES.get((to_name or "").lower(), ()):
        other = _named(locations, alias)
        if (other is None or str(other.get("id")) == str(to.get("id"))
                or not _active(other) or _flag(other, "is_freezer")
                or str(other.get("name")).strip().lower() in owned):
            continue
        return {"from_id": int(other["id"]), "from_name": str(other["name"]),
                "to_id": int(to["id"]), "to_name": str(to["name"])}
    return None


def unflagged_freezer(locations: list[dict]) -> dict | None:
    """Pantry Raider's freezer when Grocy does not know it is one, or None. Pure."""
    row = _named(locations, location_for("frozen"))
    if row is None or not _active(row) or _flag(row, "is_freezer"):
        return None
    return {"id": int(row["id"]), "name": str(row["name"])}


def merge_text(found: dict) -> tuple[str, str]:
    a, b = found["from_name"], found["to_name"]
    return (f"Your Grocy has two fridges, {a} and {b}. Merge them?",
            f"Pantry Raider keeps fridge food in {b}, and Grocy came with its own "
            f"{a}. Merging moves everything in {a} to {b} with the same amounts, "
            f"best-by dates and opened items, sets products that belong in {a} to "
            f"{b}, then hides {a} in Grocy. Nothing is deleted.")


def freezer_text(found: dict) -> tuple[str, str]:
    name = found["name"]
    return (f"Mark {name} as a freezer in Grocy?",
            f"Grocy does not know {name} is a freezer. Once it does, Grocy sets its "
            f"own best-by date when food goes into or comes out of {name}, but only "
            "for products where you set freezing or thawing days in Grocy. Other "
            "products keep the dates Pantry Raider gives them.")


def _rows(db: Session, key: str) -> list[ActionItem]:
    return db.query(ActionItem).filter(ActionItem.dedupe_key == key).all()


def settled(db: Session) -> bool:
    """True once both items were raised and closed: Grocy need not be asked again."""
    for key in (DEDUPE_TWO_FRIDGES, DEDUPE_FREEZER_FLAG):
        rows = _rows(db, key)
        if not rows or any(r.status in ("open", "snoozed") for r in rows):
            return False
    return True


def _close(db: Session, key: str) -> int:
    """Mark the item done. The dedupe key stays, so it never comes back."""
    n = 0
    for row in _rows(db, key):
        if row.status in ("open", "snoozed"):
            row.status = "done"
            row.updated_at = action_items._iso(action_items._now())
            n += 1
    if n:
        db.commit()
    return n


def sync(db: Session, locations: list[dict]) -> int:
    """Raise, refresh or close the two items for Grocy's ``locations``.
    Returns how many apply now. The caller does the fetch."""
    count = 0
    fridges = two_fridges(locations)
    if fridges:
        title, body = merge_text(fridges)
        action_items.create(db, KIND_TWO_FRIDGES, title, body=body,
                            dedupe_key=DEDUPE_TWO_FRIDGES, level="info", payload=fridges)
        count += 1
    else:
        _close(db, DEDUPE_TWO_FRIDGES)
    freezer = unflagged_freezer(locations)
    if freezer:
        title, body = freezer_text(freezer)
        action_items.create(db, KIND_FREEZER_FLAG, title, body=body,
                            dedupe_key=DEDUPE_FREEZER_FLAG, level="info", payload=freezer)
        count += 1
    else:
        _close(db, DEDUPE_FREEZER_FLAG)
    return count


async def refresh(db: Session) -> int:
    """Read Grocy's locations and sync the items. Best-effort: on a Bandit,
    once both items are closed, or when Grocy does not answer, nothing changes
    (returns -1)."""
    from fastapi.concurrency import run_in_threadpool
    from .grocy import GrocyClient
    if settings.is_satellite():
        return -1
    if await run_in_threadpool(settled, db):
        return -1
    try:
        locations = await GrocyClient().get_locations()
    except Exception:  # noqa: BLE001 - an outage leaves the inbox as it is
        return -1
    return await run_in_threadpool(sync, db, locations)


def _summary(result: dict, found: dict) -> str:
    a, b = found["from_name"], found["to_name"]
    rows = result.get("moved_rows", 0)
    moved = "Nothing needed moving" if not rows else (
        f"Moved {rows} stock {'entry' if rows == 1 else 'entries'} to {b}")
    return f"{moved}, and {a} is now hidden in Grocy."


def _left_text(result: dict, found: dict) -> str:
    a = found["from_name"]
    left = result.get("left") or []
    parts = []
    if left:
        parts.append(f"{a} was not hidden. Still in {a}: {', '.join(left)}.")
    else:
        parts.append(f"{a} was not hidden: some products still belong in {a} in "
                     "Grocy.")
    if result.get("weighed"):
        parts.append("Grocy cannot transfer a product weighed with its container ("
                     f"{', '.join(result['weighed'])}), so change the location on "
                     "its stock entries in Grocy.")
    if result.get("errors"):
        parts.append("Grocy said: " + "; ".join(result["errors"]))
    parts.append("Then press Merge again.")
    return " ".join(parts)


async def merge_fridges(db: Session) -> dict:
    """The Merge button. Returns {"ok", "message"}, or {"ok": False, "error",
    "status"} with the HTTP status the route answers with."""
    from fastapi.concurrency import run_in_threadpool
    from .grocy import GrocyClient, GrocyError
    if settings.is_satellite():
        return {"ok": False, "error": SATELLITE_MSG, "status": 403}
    async with _lock:
        client = GrocyClient()
        try:
            found = two_fridges(await client.get_locations())
            if found is None:
                await run_in_threadpool(_close, db, DEDUPE_TWO_FRIDGES)
                return {"ok": True, "message": "Grocy already has just one fridge."}
            result = await client.merge_location(found["from_id"], found["to_id"])
        except GrocyError as e:
            return {"ok": False, "status": 502,
                    "error": f"The merge stopped: {e} Press Merge again to carry on."}
        if not result.get("hidden"):
            return {"ok": False, "error": _left_text(result, found), "status": 409,
                    "result": result}
        await run_in_threadpool(_close, db, DEDUPE_TWO_FRIDGES)
        return {"ok": True, "message": _summary(result, found), "result": result}


async def mark_freezer(db: Session) -> dict:
    """The Mark as freezer button. Answers as merge_fridges does."""
    from fastapi.concurrency import run_in_threadpool
    from .grocy import GrocyClient, GrocyError
    if settings.is_satellite():
        return {"ok": False, "error": SATELLITE_MSG, "status": 403}
    async with _lock:
        client = GrocyClient()
        try:
            found = unflagged_freezer(await client.get_locations())
            if found is not None:
                await client.mark_location_freezer(found["id"])
        except GrocyError as e:
            return {"ok": False, "error": f"Grocy did not take the change: {e}", "status": 502}
        await run_in_threadpool(_close, db, DEDUPE_FREEZER_FLAG)
        name = found["name"] if found else location_for("frozen")
        return {"ok": True, "message": f"{name} is now a freezer in Grocy."}
