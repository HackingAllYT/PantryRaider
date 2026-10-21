"""Barcode corrections shared with Forager (FoodAssistant-mypf0).

When a scanned item comes up with the wrong name, brand or category and the
user fixes it on the Pending review screen before committing, that fix is
useful to every other kitchen that scans the same barcode. With the OPT-IN
``share_barcode_corrections`` setting on (off by default) and a linked Forager
kitchen, each such commit queues one correction, and a background task uploads
batches to Forager. Forager weighs every vote by its account's trust and only
publishes a barcode once enough trusted kitchens agree (cloud/app/
barcode_trust.py); the published list comes back through
services/community_barcodes.py.

Unlike the anonymous shelf-life points (services/expiry_learning.py), a
correction carries free text, so it is sent with the kitchen's instance token:
an accountable vote is what lets Forager weigh contributors, and block one who
sends junk. What a correction carries, and nothing more: the barcode, the
name, brand and category the user chose, and what the lookup had said.

Not shared, ever: products the user set up in Grocy themselves (their own
naming, not a fix to a public catalog), store-assigned codes, and rows whose
name the lookup and the user agree on. With sharing off nothing is captured,
not even locally, and a queue left from before is discarded on the next flush.

The queue is a small JSON state file under data_dir with the same atomic write
as services/expiry_learning.py, so a failed upload just waits for the next pass.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Optional

import httpx

from .state_lock import state_write_lock

logger = logging.getLogger(__name__)

CATEGORIES = ("Poultry", "Meat", "Seafood", "Dairy", "Produce", "Grains",
              "Condiments", "Beverages", "Snacks", "Frozen", "Canned", "Other")

# Length caps, matched by Forager's validation.
_MAX_NAME = 120
_MAX_BRAND = 80

# Queue cap (oldest dropped first) and upload batch size (Forager takes 50).
_MAX_QUEUE = 300
_BATCH_SIZE = 50

# Names the app makes up when it could not identify a scan. Shared as the
# "was" value they would only mislead a reviewer, so they become None.
_PLACEHOLDER = re.compile(r"^(unknown|store barcode) \(", re.IGNORECASE)
_GUESS_SUFFIX = " (unverified guess)"

# Anything Forager would refuse outright: a link, an address, markup. Checked
# here too so one bad row never gets its whole batch rejected.
_UNSAFE = re.compile(
    r"[<>\x00-\x1f\x7f]|https?:|www\.|@|\b[a-z0-9-]+\.(?:com|net|org|io|co|app|"
    r"ly|me|info|biz|xyz|ru|cn|gg|tv|link|site|online|shop|store|us|uk|de)\b",
    re.IGNORECASE)
_WS = re.compile(r"\s+")
_FOLD = re.compile(r"[^a-z0-9]+")

_lock = threading.Lock()


def _queue_file() -> Path:
    # Resolved per call (not at import) so tests that repoint data_dir work.
    from ..config import settings
    return Path(settings.data_dir) / "barcode_corrections_queue.json"


def _clean(value: Optional[str], cap: int) -> Optional[str]:
    """Collapsed, capped text, or None when empty or unsafe. Pure."""
    text = _WS.sub(" ", (value or "").strip())
    if text.endswith(_GUESS_SUFFIX):
        text = text[: -len(_GUESS_SUFFIX)].strip()
    if not text or len(text) > cap or _UNSAFE.search(text):
        return None
    return text


def _fold(value: Optional[str]) -> str:
    return _FOLD.sub(" ", (value or "").lower()).strip()


def build_correction(barcode: Optional[str], name: Optional[str],
                     brand: Optional[str], category: Optional[str],
                     was_name: Optional[str], was_brand: Optional[str],
                     was_category: Optional[str]) -> Optional[dict]:
    """One shareable correction from a committed row, or None. Pure.

    ``was_*`` are what the barcode lookup filled in before the user touched
    the row (all None when the lookup found nothing). Returns None when there
    is nothing honest to share: no usable barcode, no usable name, or a row
    the user left as the lookup had it."""
    from .expiry_learning import _clean_barcode
    code = _clean_barcode(barcode)
    clean_name = _clean(name, _MAX_NAME)
    if not code or not clean_name or len(clean_name) < 2:
        return None
    if _PLACEHOLDER.match(clean_name) or not re.search(r"[^\W\d_]", clean_name):
        return None
    clean_brand = _clean(brand, _MAX_BRAND)
    cat = category if category in CATEGORIES else None
    prev_name = None if _PLACEHOLDER.match(was_name or "") else _clean(was_name, _MAX_NAME)
    prev_brand = _clean(was_brand, _MAX_BRAND)
    unchanged = (prev_name is not None
                 and _fold(prev_name) == _fold(clean_name)
                 and _fold(prev_brand) == _fold(clean_brand)
                 and (was_category or None) == cat)
    if unchanged:
        return None
    return {
        "barcode": code,
        "name": clean_name,
        "brand": clean_brand,
        "category": cat,
        "was_name": prev_name,
        "was_brand": prev_brand,
    }


def sharing_on() -> bool:
    """Whether corrections are captured at all: the opt-in plus a link."""
    from ..config import settings
    return bool(settings.share_barcode_corrections and settings.cloud_instance_token)


def _load_locked() -> list[dict]:
    try:
        data = json.loads(_queue_file().read_text())
        items = data.get("corrections", [])
        return items if isinstance(items, list) else []
    except (OSError, ValueError, AttributeError):
        return []


def _save_locked(items: list[dict]) -> None:
    try:
        from .. import durable
        durable.write_json(_queue_file(), {"corrections": items[-_MAX_QUEUE:]},
                           keep_last_good=False)
    except OSError:
        pass  # data_dir not writable: the correction is simply not queued


def queued() -> list[dict]:
    """The corrections waiting to upload (for tests and the UI)."""
    with _lock:
        return list(_load_locked())


def clear_queue() -> None:
    with _lock:
        try:
            _queue_file().unlink(missing_ok=True)
        except OSError:
            pass


def record(barcode: Optional[str], name: Optional[str], brand: Optional[str],
           category: Optional[str], was_name: Optional[str],
           was_brand: Optional[str], was_category: Optional[str]) -> bool:
    """Queue one correction for upload when sharing is on and it is valid.
    A newer fix for the same barcode replaces a queued one. Returns True when
    queued. Never raises."""
    try:
        if not sharing_on():
            return False
        item = build_correction(barcode, name, brand, category,
                                was_name, was_brand, was_category)
        if item is None:
            return False
        with _lock, state_write_lock(_queue_file()):
            items = [c for c in _load_locked() if c.get("barcode") != item["barcode"]]
            items.append(item)
            _save_locked(items)
        return True
    except Exception:
        logger.debug("barcode correction capture skipped", exc_info=True)
        return False


async def flush(transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """Upload one batch of queued corrections to Forager, best effort.

    POSTs {cloud_base_url}/v1/barcodes/corrections with the instance token.
    Sent corrections leave the queue; on a network error or a 5xx/429 they
    stay for the next pass, and so do they on a 404 (a Forager that has not
    been updated to take corrections yet). Any other 4xx will never succeed on
    retry (an invalid batch, an unlinked kitchen, sharing blocked by Forager),
    so that batch is dropped. Sharing turned off discards the queue. Never
    raises."""
    from ..config import APP_VERSION, settings
    if not sharing_on():
        clear_queue()
        return {"sent": 0}
    base = (settings.cloud_base_url or "").rstrip("/")
    if not base:
        return {"sent": 0}
    with _lock:
        batch = _load_locked()[:_BATCH_SIZE]
    if not batch:
        return {"sent": 0}
    headers = {"Authorization": f"Bearer {settings.cloud_instance_token}",
               "X-Device-Version": APP_VERSION}
    kwargs: dict = {"timeout": 15.0}
    if transport is not None:
        kwargs["transport"] = transport
    try:
        async with httpx.AsyncClient(**kwargs) as client:
            r = await client.post(f"{base}/v1/barcodes/corrections",
                                  json={"corrections": batch}, headers=headers)
    except Exception:
        return {"sent": 0}
    if r.status_code >= 300:
        if 400 <= r.status_code < 500 and r.status_code not in (404, 429):
            logger.info("Forager refused %d barcode corrections (HTTP %s)",
                        len(batch), r.status_code)
            _remove(batch)
        return {"sent": 0, "status": r.status_code}
    _remove(batch)
    return {"sent": len(batch)}


def _remove(sent: list[dict]) -> None:
    with _lock, state_write_lock(_queue_file()):
        items = _load_locked()
        for c in sent:
            try:
                items.remove(c)
            except ValueError:
                pass
        _save_locked(items)
