"""Sensor platform for ClimateSync."""
from __future__ import annotations

import hashlib  # noqa: S324 – used for non-cryptographic unique_id generation only
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ClimateSyncCoordinator

_LOGGER = logging.getLogger(__name__)


def _entry_unique_id(entry_id: str, suffix: str) -> str:
    """Generate a stable unique_id based on entry_id and a role suffix."""
    return f"{entry_id}_{suffix}"


def _entity_slug(entity_id: str) -> str:
    """Convert an entity_id into a safe slug for unique_id / sensor name.

    Uses SHA-256 (truncated) purely as a deterministic, non-cryptographic
    way to generate a short stable identifier.
    """
    return hashlib.sha256(entity_id.encode()).hexdigest()[:8]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ClimateSync sensors."""
    coordinator: ClimateSyncCoordinator = hass.data[DOMAIN][entry.entry_id]

    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="ClimateSync",
        manufacturer="Community",
        model="ClimateSync",
    )

    entities: list[SensorEntity] = []

    # Per-room delta sensors
    for source in coordinator.source_entities:
        entities.append(
            RoomDeltaSensor(coordinator, entry, source, device_info)
        )

    # Max delta sensor
    entities.append(MaxDeltaSensor(coordinator, entry, device_info))

    # Computed setpoint sensor
    entities.append(DestinationSetpointSensor(coordinator, entry, device_info))

    # Status sensor
    entities.append(StatusSensor(coordinator, entry, device_info))

    async_add_entities(entities)


class _ClimateSyncBaseSensor(SensorEntity):
    """Base class for ClimateSync sensors."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ClimateSyncCoordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
    ) -> None:
        """Initialise base sensor."""
        self._coordinator = coordinator
        self._entry = entry
        self._attr_device_info = device_info
        self._unsub: Any = None

    async def async_added_to_hass(self) -> None:
        """Register with coordinator."""
        self._unsub = self._coordinator.async_add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from coordinator."""
        if self._unsub:
            self._unsub()

    @callback
    def _handle_update(self) -> None:
        """Push update to HA."""
        self.async_write_ha_state()


class RoomDeltaSensor(_ClimateSyncBaseSensor):
    """Delta sensor for a single source climate entity."""

    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(
        self,
        coordinator: ClimateSyncCoordinator,
        entry: ConfigEntry,
        source_entity_id: str,
        device_info: DeviceInfo,
    ) -> None:
        """Initialise room delta sensor."""
        super().__init__(coordinator, entry, device_info)
        self._source_entity_id = source_entity_id
        slug = _entity_slug(source_entity_id)
        self._attr_unique_id = _entry_unique_id(entry.entry_id, f"delta_{slug}")
        # Build a friendly name from the entity_id
        friendly = source_entity_id.split(".")[-1].replace("_", " ").title()
        self._attr_name = f"Delta {friendly}"

    @property
    def native_value(self) -> float | None:
        """Return the room delta."""
        room = self._coordinator.room_deltas.get(self._source_entity_id)
        if room is None:
            return None
        return round(room["delta"], 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes."""
        room = self._coordinator.room_deltas.get(self._source_entity_id, {})
        return {
            "source_entity_id": self._source_entity_id,
            "current_temperature": room.get("current"),
            "target_temperature": room.get("target"),
            "raw_delta": room.get("raw_delta"),
        }


class MaxDeltaSensor(_ClimateSyncBaseSensor):
    """Sensor reporting the maximum delta across all rooms."""

    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_name = "Delta Max"

    def __init__(
        self,
        coordinator: ClimateSyncCoordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
    ) -> None:
        """Initialise max delta sensor."""
        super().__init__(coordinator, entry, device_info)
        self._attr_unique_id = _entry_unique_id(entry.entry_id, "delta_max")

    @property
    def native_value(self) -> float | None:
        """Return max delta."""
        return round(self._coordinator.delta_max, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return room deltas map and leading room."""
        deltas = {
            entity_id: round(info["delta"], 2)
            for entity_id, info in self._coordinator.room_deltas.items()
        }
        return {
            "room_deltas": deltas,
            "leading_room": self._coordinator.leading_room,
        }


class DestinationSetpointSensor(_ClimateSyncBaseSensor):
    """Sensor reporting the computed destination setpoint."""

    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_name = "Destination Setpoint"

    def __init__(
        self,
        coordinator: ClimateSyncCoordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
    ) -> None:
        """Initialise setpoint sensor."""
        super().__init__(coordinator, entry, device_info)
        self._attr_unique_id = _entry_unique_id(entry.entry_id, "destination_setpoint")

    @property
    def native_value(self) -> float | None:
        """Return computed setpoint."""
        return self._coordinator.computed_setpoint

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return detailed setpoint context."""
        return {
            "destination_entity_id": self._coordinator.destination_entity,
            "destination_current_temperature": self._coordinator.destination_current_temperature,
            "destination_current_target": self._coordinator.destination_current_target,
            "delta_max": round(self._coordinator.delta_max, 2),
            "rounding_mode": self._coordinator.rounding_mode,
            "idle_temperature": self._coordinator.idle_temperature,
        }


class StatusSensor(_ClimateSyncBaseSensor):
    """Diagnostic status sensor for ClimateSync."""

    _attr_name = "Status"

    def __init__(
        self,
        coordinator: ClimateSyncCoordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
    ) -> None:
        """Initialise status sensor."""
        super().__init__(coordinator, entry, device_info)
        self._attr_unique_id = _entry_unique_id(entry.entry_id, "status")

    @property
    def native_value(self) -> str:
        """Return current status string."""
        return self._coordinator.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return rich diagnostics."""
        coord = self._coordinator
        last_update = (
            coord.last_update_time.isoformat() if coord.last_update_time else None
        )
        last_call = (
            coord.last_service_call_time.isoformat()
            if coord.last_service_call_time
            else None
        )
        return {
            "last_update_time": last_update,
            "last_service_call_time": last_call,
            "last_desired_setpoint": coord.last_desired_setpoint,
            "last_applied_setpoint": coord.last_applied_setpoint,
            "current_destination_target": coord.destination_current_target,
            "mismatch_seconds": round(coord.mismatch_seconds, 1),
            "resync_count": coord.resync_count,
            "apply_attempts": coord.apply_attempts,
            "apply_failures": coord.apply_failures,
            "last_error": coord.last_error,
        }
