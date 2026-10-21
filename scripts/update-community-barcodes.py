#!/usr/bin/env python3
"""Refresh the community barcode fixes bundled with a release.

Downloads Forager's published list (only barcodes enough trusted kitchens
agree on, see cloud/app/barcode_trust.py) and writes it to
service/app/data/community_barcodes.json, so a new install names those
products correctly before it has ever reached Forager. Run it before a
release and commit the file with the version bump.

Usage:
  scripts/update-community-barcodes.py               # from forager.pantryraider.app
  scripts/update-community-barcodes.py --base URL    # another Forager
  scripts/update-community-barcodes.py --check       # report only, write nothing

Exits non-zero, writing nothing, when the feed cannot be read or would shrink
the bundled list by more than half (pass --allow-shrink when an admin really
did pull that many).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "service" / "app" / "data" / "community_barcodes.json"
DEFAULT_BASE = "https://forager.pantryraider.app"

sys.path.insert(0, str(REPO_ROOT / "service"))
from app.services.community_barcodes import build_index  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--allow-shrink", action="store_true")
    args = ap.parse_args()

    url = f"{args.base.rstrip('/')}/api/barcodes/feed"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            feed = json.load(r)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not read {url}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(feed, dict) or not isinstance(feed.get("barcodes"), list):
        print(f"{url} did not answer with a barcode feed.", file=sys.stderr)
        return 1

    index = build_index(feed)
    try:
        old = build_index(json.loads(TARGET.read_text()))
    except (OSError, ValueError):
        old = {}
    added = len(index.keys() - old.keys())
    removed = len(old.keys() - index.keys())
    changed = sum(1 for k in index.keys() & old.keys() if index[k] != old[k])
    print(f"{len(index)} barcodes ({added} new, {changed} changed, {removed} removed)")
    if old and len(index) < len(old) / 2 and not args.allow_shrink:
        print("The feed is less than half the bundled list; nothing written. "
              "Pass --allow-shrink if that is expected.", file=sys.stderr)
        return 1
    if args.check:
        return 0

    out = {
        "version": 1,
        "generated_date": str(feed.get("generated_date") or ""),
        "barcodes": [{"barcode": code, **index[code]} for code in sorted(index)],
    }
    TARGET.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"Wrote {TARGET.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
