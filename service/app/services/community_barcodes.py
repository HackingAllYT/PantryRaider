"""Community barcode fixes from Forager (FoodAssistant-mypf0).

Forager publishes the barcodes whose corrections enough trusted kitchens
agree on (see services/barcode_corrections.py for the sending side and
cloud/app/barcode_trust.py for the rules). This module serves that list to
the barcode lookup, from two places:

- the feed cached under data_dir, fetched once a day, and
- the copy bundled with each release (app/data/community_barcodes.json,
  written by scripts/update-community-barcodes.py), so a fresh install, or
  one that never reaches Forager, starts with every fix known at release.

The cached feed replaces the bundled copy whenever it is at least as new: it
is the whole published list, so a barcode an admin has since pulled drops out
of it too. Lookup order in services/barcode.py: the user's own Grocy product,
then this list, then Open Food Facts, then an AI guess.

Reading the feed sends nothing and needs no account. It is gated by
``use_community_barcodes`` (on by default). Reads are mtime cached and never
raise; anything malformed is skipped.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from ..plain_digits import is_plain_digits
from .barcode_corrections import CATEGORIES

logger = logging.getLogger(__name__)

_MAX_AGE_SECONDS = 24 * 3600
_BUNDLED = Path(__file__).resolve().parent.parent / "data" / "community_barcodes.json"

_lock = threading.Lock()
_index: dict[str, dict] = {}
_stamp: tuple | None = None


def _cache_file() -> Path:
    from ..config import settings
    return Path(settings.data_dir) / "community_barcodes.json"


def build_index(feed: dict) -> dict[str, dict]:
    """Barcode -> {name, brand, category} from a raw feed. Pure and
    defensive: malformed entries are skipped."""
    index: dict[str, dict] = {}
    entries = feed.get("barcodes") if isinstance(feed, dict) else None
    if not isinstance(entries, list):
        return index
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("barcode") or "").strip()
        name = str(entry.get("name") or "").strip()
        if not is_plain_digits(code) or not (6 <= len(code) <= 24):
            continue
        if not name or len(name) > 120:
            continue
        brand = str(entry.get("brand") or "").strip()[:80] or None
        category = entry.get("category")
        index[code] = {
            "name": name,
            "brand": brand,
            "category": category if category in CATEGORIES else None,
        }
    return index


def _read(path: Path) -> Optional[dict]:
    try:
        feed = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return feed if isinstance(feed, dict) else None


def choose_feed(cached: Optional[dict], bundled: Optional[dict]) -> Optional[dict]:
    """The feed to serve: the cached one unless the bundled copy is newer
    (a release shipped after the last successful fetch). Pure."""
    if cached is None:
        return bundled
    if bundled is None:
        return cached
    if str(bundled.get("generated_date") or "") > str(cached.get("generated_date") or ""):
        return bundled
    return cached


def _mtime(path: Path) -> Optional[int]:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _load_locked() -> dict[str, dict]:
    global _index, _stamp
    cf = _cache_file()
    stamp = (_mtime(cf), _mtime(_BUNDLED))
    if stamp != _stamp:
        feed = choose_feed(_read(cf) if stamp[0] else None,
                           _read(_BUNDLED) if stamp[1] else None)
        _index = build_index(feed or {})
        _stamp = stamp
    return _index


def lookup(barcode: Optional[str]) -> Optional[dict]:
    """The community fix for ``barcode``, or None. Local only, never raises."""
    try:
        from ..config import settings
        if not settings.use_community_barcodes:
            return None
        code = (barcode or "").strip()
        if not code:
            return None
        with _lock:
            entry = _load_locked().get(code)
        return dict(entry) if entry else None
    except Exception:
        return None


def is_stale(max_age_seconds: int = _MAX_AGE_SECONDS) -> bool:
    try:
        return time.time() - _cache_file().stat().st_mtime > max_age_seconds
    except OSError:
        return True


async def fetch_feed(base: str, transport: httpx.AsyncBaseTransport | None = None
                     ) -> Optional[dict]:
    """GET the published feed from a Forager base URL, or None."""
    kwargs: dict = {"timeout": 20.0}
    if transport is not None:
        kwargs["transport"] = transport
    try:
        async with httpx.AsyncClient(**kwargs) as client:
            r = await client.get(f"{base.rstrip('/')}/api/barcodes/feed")
        if r.status_code != 200:
            return None
        feed = r.json()
    except Exception:
        return None
    if not isinstance(feed, dict) or not isinstance(feed.get("barcodes"), list):
        return None
    return feed


async def refresh(transport: httpx.AsyncBaseTransport | None = None) -> bool:
    """Fetch the feed from Forager and cache it. Calm on failure: keeps
    whatever cache exists. Never raises."""
    try:
        from ..config import settings
        base = (settings.cloud_base_url or "").strip()
        if not base:
            return False
        feed = await fetch_feed(base, transport)
        if feed is None:
            return False
        from .. import durable
        durable.write_json(_cache_file(), feed, keep_last_good=False)
        return True
    except Exception:
        logger.debug("community barcode refresh failed", exc_info=True)
        return False


def clear_cache() -> None:
    """Drop the cached feed and the in-process index (test cleanup)."""
    global _index, _stamp
    with _lock:
        _index = {}
        _stamp = None
        try:
            _cache_file().unlink(missing_ok=True)
        except OSError:
            pass
