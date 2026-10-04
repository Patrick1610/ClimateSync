"""Tests for downloadable ClimateSync diagnostics."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Mock the small Home Assistant import surface used by diagnostics.
_mock_ha = MagicMock()
_mock_ha.core.callback = lambda fn: fn
_mock_dt_util = MagicMock()
_mock_ha.util.dt = _mock_dt_util
_modules = {
    "homeassistant": _mock_ha,
    "homeassistant.config_entries": _mock_ha.config_entries,
    "homeassistant.core": _mock_ha.core,
    "homeassistant.helpers": _mock_ha.helpers,
    "homeassistant.helpers.device_registry": _mock_ha.helpers.device_registry,
    "homeassistant.helpers.event": _mock_ha.helpers.event,
    "homeassistant.util": _mock_ha.util,
    "homeassistant.util.dt": _mock_dt_util,
}
for mod_name, mod_obj in _modules.items():
    sys.modules.setdefault(mod_name, mod_obj)

from custom_components.climatesync.const import DOMAIN  # noqa: E402
from custom_components.climatesync.diagnostics import (  # noqa: E402
    async_get_config_entry_diagnostics,
)


@pytest.mark.asyncio
async def test_config_entry_diagnostics_are_complete_and_serialisable() -> None:
    """Diagnostics expose support data without depending on entity objects."""
    now = datetime(2026, 10, 4, 7, 30, tzinfo=timezone.utc)
    coordinator = SimpleNamespace(
        status="degraded_source_data",
        source_entities=["climate.living_room", "climate.hallway"],
        primary_source_entities=["climate.living_room"],
        destination_entity="climate.emma",
        destination_target="target_temp_low",
        idle_temperature=5.0,
        max_setpoint=35.0,
        rounding_mode="half_step",
        rounding_direction="ceiling",
        resync_interval=60,
        min_change_threshold=0.5,
        min_send_interval=10,
        demand_activation_threshold=0.5,
        demand_deactivation_threshold=0.1,
        room_deltas={
            "climate.living_room": {
                "delta": 0.8,
                "current": 20.2,
                "target": 21.0,
                "raw_delta": 0.8,
                "source_entity_id": "climate.living_room",
                "source_status": "active",
            },
            "climate.hallway": {
                "delta": 0.0,
                "current": None,
                "target": None,
                "raw_delta": 0.0,
                "source_entity_id": "climate.hallway",
                "source_status": "unavailable",
            },
        },
        delta_max=0.8,
        leading_room="climate.living_room",
        demand_active=True,
        usable_source_count=1,
        degraded_source_entities=["climate.hallway"],
        inactive_source_entities=[],
        primary_delta_max=0.8,
        primary_leading_room="climate.living_room",
        primary_demand_active=True,
        primary_usable_source_count=1,
        primary_degraded_source_entities=[],
        primary_inactive_source_entities=[],
        destination_current_temperature=18.0,
        destination_current_target=18.5,
        destination_paired_target=27.0,
        raw_setpoint=18.8,
        rounded_setpoint=19.0,
        computed_setpoint=19.0,
        last_desired_setpoint=19.0,
        last_applied_setpoint=19.0,
        last_update_time=now,
        last_service_call_time=now,
        mismatch_since=None,
        mismatch_seconds=0.0,
        resync_count=3,
        evaluation_count=12,
        apply_attempts=4,
        apply_failures=1,
        skipped_anti_flap=7,
        skipped_rate_limit=1,
        last_error="example failure",
    )
    entry = SimpleNamespace(entry_id="entry-id", version=2, minor_version=1)
    hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: coordinator}})

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["config_entry"] == {"version": 2, "minor_version": 1}
    diagnostics = result["coordinator"]
    assert diagnostics["configuration"]["destination_entity"] == "climate.emma"
    assert diagnostics["configuration"]["resync_interval_seconds"] == 60
    assert diagnostics["sources"]["demand_active"] is True
    assert diagnostics["sources"]["degraded_source_entities"] == ["climate.hallway"]
    assert diagnostics["primary_sources"]["demand_active"] is True
    assert diagnostics["setpoint"]["final"] == 19.0
    assert diagnostics["timing"]["last_update"] == "2026-10-04T07:30:00+00:00"
    assert diagnostics["counters"]["apply_failures"] == 1
    assert diagnostics["last_error"] == "example failure"
    json.dumps(result)
