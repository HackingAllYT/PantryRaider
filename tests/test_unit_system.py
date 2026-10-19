"""Measurement units setting (public issue #92).

unit_system ("us" | "metric") decides which units the Kitchen Guide and the
converter lead with, and asks the AI to write generated recipes in metric. It
syncs across the fleet with the clock format, so every screen in one kitchen
agrees. The pages and the recipe prompt are checked in test_smoke_routes.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1] / "service"
sys.path.insert(0, str(SERVICE))

from app.config import (  # noqa: E402
    UNIT_SYSTEMS, _DEFAULT_UNIT_SYSTEM, _SAVEABLE, SATELLITE_PULL_FIELDS,
    settings, units_clause,
)


def test_default_is_us_and_values_are_pinned():
    assert type(settings)().unit_system == "us"
    assert _DEFAULT_UNIT_SYSTEM == "us"
    assert UNIT_SYSTEMS == ("us", "metric")


def test_unit_system_persists_and_syncs_with_the_clock_format():
    assert "unit_system" in _SAVEABLE
    assert "unit_system" in SATELLITE_PULL_FIELDS
    assert "clock_format" in SATELLITE_PULL_FIELDS


def test_setup_payload_round_trips_unit_system():
    from app.routers.setup import SetupPayload

    assert SetupPayload(unit_system="metric").unit_system == "metric"
    # Absent from a partial save = the stored value is left alone.
    assert "unit_system" not in SetupPayload().model_dump(exclude_unset=True)


def test_units_clause_only_speaks_up_for_metric():
    assert units_clause("us") == ""
    assert units_clause("") == ""
    assert units_clause("imperial") == ""
    clause = units_clause("metric")
    assert "grams" in clause and "milliliters" in clause and "Celsius" in clause
    assert "Do not use cups" in clause
