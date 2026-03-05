"""Tests for ClimateSync sensor entities."""
from __future__ import annotations

import enum
import sys
from datetime import datetime
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock the homeassistant package tree before importing sensor module.
# We need real (non-MagicMock) base classes so that sensor subclasses can
# be instantiated without MagicMock __init__ side-effects.
# ---------------------------------------------------------------------------
_mock_ha = MagicMock()

# homeassistant.core.callback must act as a transparent decorator
_mock_ha.core.callback = lambda fn: fn


# --- Real stub for EntityCategory ---
class _EntityCategory(enum.Enum):
    DIAGNOSTIC = "diagnostic"
    CONFIG = "config"


# --- Real stub for SensorEntity ---
class _SensorEntity:
    """Minimal stand-in for homeassistant.components.sensor.SensorEntity."""
    _attr_has_entity_name: bool = False
    _attr_entity_category = None
    _attr_device_class = None
    _attr_state_class = None
    _attr_native_unit_of_measurement = None
    _attr_name: str | None = None
    _attr_unique_id: str | None = None
    _attr_device_info = None


# Build a real module for homeassistant.components.sensor
_sensor_mod = ModuleType("homeassistant.components.sensor")
_sensor_mod.SensorEntity = _SensorEntity
_sensor_mod.SensorDeviceClass = MagicMock()
_sensor_mod.SensorStateClass = MagicMock()

# Build a real module for homeassistant.helpers.entity
_entity_mod = ModuleType("homeassistant.helpers.entity")
_entity_mod.DeviceInfo = MagicMock()
_entity_mod.EntityCategory = _EntityCategory

# dt_util.utcnow – default return value
_mock_dt_util = MagicMock()
_NOW = datetime(2024, 1, 1, 12, 0, 0)
_mock_dt_util.utcnow = MagicMock(return_value=_NOW)
_mock_ha.util.dt = _mock_dt_util

_modules = {
    "homeassistant": _mock_ha,
    "homeassistant.components": _mock_ha.components,
    "homeassistant.components.sensor": _sensor_mod,
    "homeassistant.config_entries": _mock_ha.config_entries,
    "homeassistant.const": _mock_ha.const,
    "homeassistant.core": _mock_ha.core,
    "homeassistant.helpers": _mock_ha.helpers,
    "homeassistant.helpers.entity": _entity_mod,
    "homeassistant.helpers.entity_platform": _mock_ha.helpers.entity_platform,
    "homeassistant.helpers.event": _mock_ha.helpers.event,
    "homeassistant.util": _mock_ha.util,
    "homeassistant.util.dt": _mock_dt_util,
}

for mod_name, mod_obj in _modules.items():
    sys.modules.setdefault(mod_name, mod_obj)

from custom_components.climatesync.const import (  # noqa: E402
    CONF_DESTINATION_ENTITY,
    CONF_IDLE_TEMPERATURE,
    CONF_SOURCE_ENTITIES,
    DEFAULT_IDLE_TEMPERATURE,
    DEFAULT_MIN_CHANGE_THRESHOLD,
    DEFAULT_MIN_SEND_INTERVAL,
    DEFAULT_RESYNC_INTERVAL,
    DEFAULT_ROUNDING_MODE,
)
from custom_components.climatesync.coordinator import (  # noqa: E402
    ClimateSyncCoordinator,
)
from custom_components.climatesync.sensor import (  # noqa: E402
    DestinationCurrentTargetSensor,
    DestinationSetpointSensor,
    MaxDeltaSensor,
    RoomDeltaSensor,
    StatusSensor,
    _entry_unique_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_coordinator(
    *,
    source_entities: list[str] | None = None,
    destination_entity: str = "climate.dest",
) -> tuple[ClimateSyncCoordinator, MagicMock]:
    """Build a coordinator with a fully-mocked hass/entry."""
    if source_entities is None:
        source_entities = ["climate.room1"]

    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    entry = MagicMock()
    entry.data = {
        CONF_SOURCE_ENTITIES: source_entities,
        CONF_DESTINATION_ENTITY: destination_entity,
        CONF_IDLE_TEMPERATURE: DEFAULT_IDLE_TEMPERATURE,
    }
    entry.options = {
        "rounding_mode": DEFAULT_ROUNDING_MODE,
        "min_change_threshold": DEFAULT_MIN_CHANGE_THRESHOLD,
        "min_send_interval_seconds": DEFAULT_MIN_SEND_INTERVAL,
        "resync_interval_seconds": DEFAULT_RESYNC_INTERVAL,
    }
    entry.entry_id = "test_entry_123"

    coord = ClimateSyncCoordinator(hass, entry)
    coord._source_entities = list(source_entities)
    coord._destination_entity = destination_entity
    coord._idle_temperature = float(DEFAULT_IDLE_TEMPERATURE)
    coord._rounding_mode = DEFAULT_ROUNDING_MODE

    return coord, hass


def _make_device_info() -> MagicMock:
    """Return a mock DeviceInfo."""
    return MagicMock()


# ---------------------------------------------------------------------------
# Tests: Entity categorization
# ---------------------------------------------------------------------------


class TestEntityCategorization:
    """Verify sensors vs diagnostic categorization."""

    def test_room_delta_is_not_diagnostic(self):
        """RoomDeltaSensor should NOT have entity_category set (regular sensor)."""
        coord, _ = _build_coordinator()
        sensor = RoomDeltaSensor(coord, coord.entry, "climate.room1", _make_device_info())
        assert not hasattr(sensor, "_attr_entity_category") or sensor._attr_entity_category is None

    def test_max_delta_is_not_diagnostic(self):
        """MaxDeltaSensor should NOT have entity_category set (regular sensor)."""
        coord, _ = _build_coordinator()
        sensor = MaxDeltaSensor(coord, coord.entry, _make_device_info())
        assert not hasattr(sensor, "_attr_entity_category") or sensor._attr_entity_category is None

    def test_setpoint_is_not_diagnostic(self):
        """DestinationSetpointSensor should NOT have entity_category set (regular sensor)."""
        coord, _ = _build_coordinator()
        sensor = DestinationSetpointSensor(coord, coord.entry, _make_device_info())
        assert not hasattr(sensor, "_attr_entity_category") or sensor._attr_entity_category is None

    def test_destination_current_target_is_not_diagnostic(self):
        """DestinationCurrentTargetSensor should NOT have entity_category set."""
        coord, _ = _build_coordinator()
        sensor = DestinationCurrentTargetSensor(coord, coord.entry, _make_device_info())
        assert not hasattr(sensor, "_attr_entity_category") or sensor._attr_entity_category is None

    def test_status_is_diagnostic(self):
        """StatusSensor should have entity_category set to DIAGNOSTIC."""
        coord, _ = _build_coordinator()
        sensor = StatusSensor(coord, coord.entry, _make_device_info())
        assert sensor._attr_entity_category is not None


# ---------------------------------------------------------------------------
# Tests: Sensor naming / sort order
# ---------------------------------------------------------------------------


class TestSensorNaming:
    """Verify sensor names support correct sort ordering."""

    def test_setpoint_name_has_1_prefix(self):
        """DestinationSetpointSensor name should start with '1.'."""
        coord, _ = _build_coordinator()
        sensor = DestinationSetpointSensor(coord, coord.entry, _make_device_info())
        assert sensor._attr_name == "1. Destination Setpoint"

    def test_max_delta_name_has_2_prefix(self):
        """MaxDeltaSensor name should start with '2.'."""
        coord, _ = _build_coordinator()
        sensor = MaxDeltaSensor(coord, coord.entry, _make_device_info())
        assert sensor._attr_name == "2. Delta Max"

    def test_room_delta_name_starts_with_delta(self):
        """RoomDeltaSensor name should start with 'Delta'."""
        coord, _ = _build_coordinator()
        sensor = RoomDeltaSensor(coord, coord.entry, "climate.living_room", _make_device_info())
        assert sensor._attr_name.startswith("Delta ")

    def test_sort_order_is_correct(self):
        """Names should sort in the order: setpoint, delta max, then room deltas."""
        coord, _ = _build_coordinator(source_entities=["climate.room1", "climate.room2"])
        di = _make_device_info()

        setpoint = DestinationSetpointSensor(coord, coord.entry, di)
        max_delta = MaxDeltaSensor(coord, coord.entry, di)
        room1 = RoomDeltaSensor(coord, coord.entry, "climate.room1", di)
        room2 = RoomDeltaSensor(coord, coord.entry, "climate.room2", di)

        names = [setpoint._attr_name, max_delta._attr_name, room1._attr_name, room2._attr_name]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# Tests: DestinationCurrentTargetSensor
# ---------------------------------------------------------------------------


class TestDestinationCurrentTargetSensor:
    """Tests for the new DestinationCurrentTargetSensor."""

    def test_unique_id(self):
        """Verify unique_id format."""
        coord, _ = _build_coordinator()
        sensor = DestinationCurrentTargetSensor(coord, coord.entry, _make_device_info())
        assert sensor._attr_unique_id == "test_entry_123_destination_current_target"

    def test_name(self):
        """Verify name."""
        coord, _ = _build_coordinator()
        sensor = DestinationCurrentTargetSensor(coord, coord.entry, _make_device_info())
        assert sensor._attr_name == "Destination Current Target"

    def test_native_value_returns_destination_target(self):
        """native_value should return the coordinator's destination_current_target."""
        coord, _ = _build_coordinator()
        coord.destination_current_target = 21.5
        sensor = DestinationCurrentTargetSensor(coord, coord.entry, _make_device_info())
        assert sensor.native_value == 21.5

    def test_native_value_returns_none_when_unavailable(self):
        """native_value should return None when destination_current_target is None."""
        coord, _ = _build_coordinator()
        coord.destination_current_target = None
        sensor = DestinationCurrentTargetSensor(coord, coord.entry, _make_device_info())
        assert sensor.native_value is None

    def test_extra_state_attributes(self):
        """Verify extra_state_attributes returns destination context."""
        coord, _ = _build_coordinator(destination_entity="climate.emma")
        coord.destination_current_temperature = 19.5
        sensor = DestinationCurrentTargetSensor(coord, coord.entry, _make_device_info())
        attrs = sensor.extra_state_attributes
        assert attrs["destination_entity_id"] == "climate.emma"
        assert attrs["destination_current_temperature"] == 19.5
