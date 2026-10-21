"""Community barcode corrections: text safety, vote weights and consensus.

Kitchens that opt in send what a barcode really is after the lookup got it
wrong or found nothing (routers/barcodes.py). Every vote is tied to an
account, and this module decides how much each one counts and when a barcode
has enough agreement to publish. Everything here is pure (plain dicts in,
plain dicts out) so the rules are unit-tested directly.

The defenses, in the order a bad correction meets them:

- Text safety (``clean_correction``): no links, email addresses, markup or
  control characters, a real name with letters in it, sane lengths. A batch
  with one bad item is refused whole.
- One vote per account per barcode, enforced by the table's unique pair.
- Vote weight (``vote_weight``): a brand-new or unconfirmed account counts
  for little, a blocked one for nothing, and an account's weight follows its
  record: votes that agreed with what was published raise it, votes that
  disagreed lower it, and every vote an admin rejected costs three
  disagreements.
- Consensus (``consensus``): the heaviest answer publishes only with enough
  total weight, a clear two-thirds majority of the barcode's weight, and at
  least two separate accounts behind it (or an admin-trusted one).
- Admin decisions override all of the above.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

# The category vocabulary, mirrored from the app's FoodCategory values
# (duplicated on purpose: the two apps share nothing at import time).
CATEGORIES = ("Poultry", "Meat", "Seafood", "Dairy", "Produce", "Grains",
              "Condiments", "Beverages", "Snacks", "Frozen", "Canned", "Other")

MAX_CORRECTIONS = 50
MIN_NAME_LEN = 2
MAX_NAME_LEN = 120
MAX_BRAND_LEN = 80

# Vote weights.
TRUSTED_WEIGHT = 3.0
MAX_WEIGHT = 2.0
NEW_ACCOUNT_DAYS = 14
REJECTED_PENALTY = 3

# Publishing thresholds.
PUBLISH_WEIGHT = 2.0
PUBLISH_SHARE = 2 / 3
PUBLISH_MIN_ACCOUNTS = 2

_WS = re.compile(r"\s+")
_URL = re.compile(
    r"https?:|www\.|\b[a-z0-9-]+\.(?:com|net|org|io|co|app|ly|me|info|biz|"
    r"xyz|ru|cn|gg|tv|link|site|online|shop|store|us|uk|de)\b",
    re.IGNORECASE)
_EMAIL = re.compile(r"\S+@\S+\.\S+")
_PUNCT = re.compile(r"[^\w\s]|_")


# --- Text ---------------------------------------------------------------------

def collapse(value) -> str:
    """Whitespace collapsed and trimmed; anything that is not a string is ""."""
    return _WS.sub(" ", value).strip() if isinstance(value, str) else ""


def unsafe_text(value: str) -> bool:
    """Whether a piece of submitted text must be refused. Pure.

    Control characters and markup are refused outright, and so is anything
    that could advertise: a link (a scheme, www., or a bare domain such as
    shop.com) or an email address. A product name never needs any of them."""
    if any(unicodedata.category(c) == "Cc" for c in value):
        return True
    if "<" in value or ">" in value:
        return True
    return bool(_URL.search(value) or _EMAIL.search(value))


def _optional_text(value, max_len: int) -> tuple[bool, str | None]:
    """(ok, cleaned) for an optional field: empty is fine and becomes None."""
    if value is not None and not isinstance(value, str):
        return False, None
    if value is not None and unsafe_text(value):
        return False, None
    text = collapse(value)
    if not text:
        return True, None
    if len(text) > max_len:
        return False, None
    return True, text


def clean_correction(item: dict) -> dict | None:
    """A validated correction ready to store, or None when it must be refused.

    The barcode must be plain digits of product length; the name is required,
    2 to 120 characters with at least one letter; brand and category are
    optional but must be valid when given. was_name and was_brand are only
    reviewer context, so a bad one is dropped to None rather than costing the
    whole batch."""
    barcode = item.get("barcode")
    if not isinstance(barcode, str):
        return None
    barcode = barcode.strip()
    if not (barcode.isascii() and barcode.isdigit() and 6 <= len(barcode) <= 24):
        return None
    raw_name = item.get("name")
    if not isinstance(raw_name, str) or unsafe_text(raw_name):
        return None
    name = collapse(raw_name)
    if not (MIN_NAME_LEN <= len(name) <= MAX_NAME_LEN):
        return None
    if not any(c.isalpha() for c in name):
        return None
    ok, brand = _optional_text(item.get("brand"), MAX_BRAND_LEN)
    if not ok:
        return None
    category = item.get("category")
    if category is not None and not isinstance(category, str):
        return None
    category = collapse(category) or None
    if category is not None and category not in CATEGORIES:
        return None
    ok, was_name = _optional_text(item.get("was_name"), MAX_NAME_LEN)
    was_name = was_name if ok else None
    ok, was_brand = _optional_text(item.get("was_brand"), MAX_BRAND_LEN)
    was_brand = was_brand if ok else None
    return {
        "barcode": barcode,
        "name": name,
        "name_key": name_key(name, brand),
        "brand": brand,
        "category": category,
        "was_name": was_name,
        "was_brand": was_brand,
    }


def _fold(value: str) -> str:
    return collapse(_PUNCT.sub(" ", (value or "").lower()))


def name_key(name: str, brand: str | None = None) -> str:
    """The grouping key for a name. Pure.

    Lowercased, punctuation stripped and whitespace collapsed, and a leading
    brand dropped when the brand is given, so "Heinz Ketchup" (brand Heinz),
    "heinz ketchup!" and "Ketchup" (brand Heinz) are all the same answer. A
    name that is nothing but the brand keeps it, so the key is never empty."""
    key = _fold(name)
    brand_key = _fold(brand or "")
    if brand_key and key.startswith(brand_key + " "):
        key = key[len(brand_key) + 1:]
    return key[:MAX_NAME_LEN]


# --- Weights ------------------------------------------------------------------

def account_age_days(created_at: str, now: datetime | None = None) -> int:
    """Whole days since an ISO timestamp; 0 when it cannot be read."""
    try:
        created = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return 0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return max(0, ((now or datetime.now(timezone.utc)) - created).days)


def reputation(agree: int, disagree: int, rejected: int) -> float:
    """The record multiplier, 2*(a+1)/(a+d+2) with each rejection counting as
    REJECTED_PENALTY disagreements. Pure. An account with no record sits at
    1.0; one that keeps agreeing approaches 2; one that keeps disagreeing,
    or gets rejected, falls toward 0."""
    d = disagree + REJECTED_PENALTY * rejected
    return 2.0 * (agree + 1) / (agree + d + 2)


def vote_weight(trust: str, age_days: int, email_verified: bool,
                agree: int = 0, disagree: int = 0, rejected: int = 0) -> float:
    """How much one account's vote counts. Pure.

    Blocked counts for nothing and trusted for TRUSTED_WEIGHT, whatever the
    record. Everyone else starts at 1, halved for an account under
    NEW_ACCOUNT_DAYS old and halved again for an unconfirmed address (so a
    fresh throwaway account is a quarter vote), then scaled by its record and
    capped at MAX_WEIGHT. Many fresh accounts are therefore needed to match
    one established contributor, and each one still has to be signed up and
    linked to a kitchen."""
    if trust == "blocked":
        return 0.0
    if trust == "trusted":
        return TRUSTED_WEIGHT
    base = 1.0
    if age_days < NEW_ACCOUNT_DAYS:
        base *= 0.5
    if not email_verified:
        base *= 0.5
    return max(0.0, min(MAX_WEIGHT, base * reputation(agree, disagree, rejected)))


# --- Consensus ----------------------------------------------------------------

def _pick(values: list[tuple]) -> str | None:
    """The most common value, ties to the heaviest voter, then most recent.

    ``values`` holds (value, weight, updated_at); empty values are skipped."""
    tally: dict[str, list] = {}
    for value, weight, updated in values:
        if not value:
            continue
        entry = tally.setdefault(value, [0, 0.0, ""])
        entry[0] += 1
        entry[1] = max(entry[1], weight)
        entry[2] = max(entry[2], updated or "")
    if not tally:
        return None
    return max(tally.items(), key=lambda kv: (kv[1][0], kv[1][1], kv[1][2]))[0]


def consensus(votes: list[dict]) -> dict:
    """The verdict on one barcode from its active votes. Pure.

    Each vote is a dict with account_id, name_key, name, brand, category,
    updated_at, weight and trusted. Votes are grouped by name_key and each
    group weighs the sum of its voters' weights. The heaviest group wins, and
    the barcode publishes when that group:

    - weighs at least PUBLISH_WEIGHT,
    - holds at least PUBLISH_SHARE of the barcode's total weight, and
    - has PUBLISH_MIN_ACCOUNTS separate accounts or an admin-trusted one.

    Returns {"groups", "total", "winner", "share", "published", "result"}
    where groups are sorted heaviest first and result is the published
    {name, brand, category, votes, name_key} (None when not published)."""
    groups: dict[str, dict] = {}
    for vote in votes:
        group = groups.setdefault(vote["name_key"], {
            "name_key": vote["name_key"], "weight": 0.0, "votes": []})
        group["weight"] += vote["weight"]
        group["votes"].append(vote)
    ordered = sorted(groups.values(),
                     key=lambda g: (-g["weight"], -len(g["votes"]), g["name_key"]))
    total = sum(g["weight"] for g in ordered)
    verdict = {"groups": ordered, "total": total, "winner": None, "share": 0.0,
               "published": False, "result": None}
    if not ordered:
        return verdict
    winner = ordered[0]
    share = winner["weight"] / total if total > 0 else 0.0
    verdict["winner"] = winner
    verdict["share"] = share
    accounts = {v["account_id"] for v in winner["votes"]}
    trusted = any(v.get("trusted") for v in winner["votes"])
    # A hair of tolerance so two half-weight votes reaching exactly the
    # threshold are not lost to float rounding.
    if (winner["weight"] + 1e-9 >= PUBLISH_WEIGHT
            and share + 1e-9 >= PUBLISH_SHARE
            and (len(accounts) >= PUBLISH_MIN_ACCOUNTS or trusted)):
        verdict["published"] = True
        verdict["result"] = _result(winner)
    return verdict


def _result(group: dict) -> dict:
    def values(field):
        return [(v.get(field), v["weight"], v.get("updated_at")) for v in group["votes"]]
    return {
        "name": _pick(values("name")),
        "brand": _pick(values("brand")),
        "category": _pick(values("category")),
        "votes": len({v["account_id"] for v in group["votes"]}),
        "name_key": group["name_key"],
    }


def _decided_result(decision: dict, votes: list[dict]) -> dict:
    key = name_key(decision.get("name") or "", decision.get("brand"))
    return {
        "name": decision.get("name") or "",
        "brand": decision.get("brand") or None,
        "category": decision.get("category") or None,
        "votes": len({v["account_id"] for v in votes if v["name_key"] == key}),
        "name_key": key,
    }


def _verdicts(by_barcode: dict[str, list[dict]], weights: dict[int, float],
              trusted: set[int], decisions: dict[str, dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for barcode, rows in by_barcode.items():
        active = [{**r, "weight": weights.get(r["account_id"], 0.0),
                   "trusted": r["account_id"] in trusted}
                  for r in rows if r.get("status", "active") == "active"]
        verdict = consensus(active)
        decision = decisions.get(barcode)
        verdict["decision"] = decision
        if decision and decision.get("action") == "approved":
            verdict["published"] = True
            verdict["result"] = _decided_result(decision, active)
        elif decision and decision.get("action") == "rejected":
            verdict["published"] = False
            verdict["result"] = None
        out[barcode] = verdict
    return out


def evaluate(corrections: list[dict], accounts: dict[int, dict],
             decisions: dict[str, dict]) -> tuple[dict[str, dict], dict[int, float]]:
    """Every barcode's verdict and every account's final weight. Pure.

    ``corrections`` are stored rows as dicts (barcode, account_id, name_key,
    name, brand, category, status, updated_at, ...); ``accounts`` maps an
    account id to {"trust", "age_days", "email_verified"}; ``decisions``
    maps a barcode to an admin decision dict (action, name, brand, category).

    Weights depend on each account's record against published results, and
    published results depend on weights, so this runs twice. Pass one weighs
    every account with a neutral record (reputation 1) to find what the
    community agrees on. Each account's record is then counted against those
    results (plus admin approvals, and its admin-rejected votes), and pass
    two re-runs consensus with the real weights. Two passes are enough to
    reward a steady contributor and sink a habitual dissenter without
    iterating to a fixed point, which could oscillate on a contested barcode.
    """
    by_barcode: dict[str, list[dict]] = {}
    for row in corrections:
        by_barcode.setdefault(row["barcode"], []).append(row)
    trusted = {aid for aid, a in accounts.items() if a.get("trust") == "trusted"}

    def weigh(agree=None, disagree=None, rejected=None):
        return {aid: vote_weight(a.get("trust", ""), a.get("age_days", 0),
                                 bool(a.get("email_verified")),
                                 (agree or {}).get(aid, 0),
                                 (disagree or {}).get(aid, 0),
                                 (rejected or {}).get(aid, 0))
                for aid, a in accounts.items()}

    first = _verdicts(by_barcode, weigh(), trusted, decisions)
    agree: dict[int, int] = {}
    disagree: dict[int, int] = {}
    rejected: dict[int, int] = {}
    for barcode, rows in by_barcode.items():
        result = first[barcode]["result"]
        for row in rows:
            aid = row["account_id"]
            if row.get("status") == "rejected":
                rejected[aid] = rejected.get(aid, 0) + 1
            elif result is not None:
                bucket = agree if row["name_key"] == result["name_key"] else disagree
                bucket[aid] = bucket.get(aid, 0) + 1
    weights = weigh(agree, disagree, rejected)
    return _verdicts(by_barcode, weights, trusted, decisions), weights


def feed_entries(verdicts: dict[str, dict]) -> list[dict]:
    """The published feed rows from evaluate()'s verdicts, by barcode. Pure."""
    entries = []
    for barcode in sorted(verdicts):
        result = verdicts[barcode]["result"]
        if not verdicts[barcode]["published"] or not result:
            continue
        entries.append({"barcode": barcode, "name": result["name"],
                        "brand": result["brand"], "category": result["category"],
                        "votes": result["votes"]})
    return entries
