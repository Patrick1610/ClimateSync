"""Binary sensor platform for ClimateSync heating demand."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ClimateSyncCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ClimateSync demand binary sensors."""
    coordinator: ClimateSyncCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="ClimateSync",
        manufacturer="Community",
        model="ClimateSync",
    )
    async_add_entities(
        [
            HeatingDemandActiveBinarySensor(coordinator, entry, device_info),
            PrimaryHeatingDemandActiveBinarySensor(coordinator, entry, device_info),
        ]
    )


class _ClimateSyncBaseBinarySensor(BinarySensorEntity):
    """Base class for ClimateSync demand binary sensors."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(
        self,
        coordinator: ClimateSyncCoordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
    ) -> None:
        """Initialise the demand binary sensor."""
        self._coordinator = coordinator
        self._attr_device_info = device_info
        self._attr_unique_id = f"{entry.entry_id}_{self._unique_id_suffix}"
        self._unsub: Any = None

    async def async_added_to_hass(self) -> None:
        """Register for coordinator updates."""
        self._unsub = self._coordinator.async_add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from coordinator updates."""
        if self._unsub:
            self._unsub()

    @callback
    def _handle_update(self) -> None:
        """Push an updated state to Home Assistant."""
        self.async_write_ha_state()


class HeatingDemandActiveBinarySensor(_ClimateSyncBaseBinarySensor):
    """Heating demand across every configured source."""

    _attr_name = "Heating Demand Active"
    _unique_id_suffix = "heating_demand_active"

    @property
    def is_on(self) -> bool:
        """Return whether any usable source has active logical demand."""
        return self._coordinator.demand_active

    @property
    def available(self) -> bool:
        """Return whether at least one source can provide valid demand data."""
        return self._coordinator.usable_source_count > 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose scope and hysteresis context."""
        coord = self._coordinator
        return {
            "scope": "all_sources",
            "delta_max": round(coord.delta_max, 2),
            "leading_source": coord.leading_room,
            "activation_threshold": coord.demand_activation_threshold,
            "deactivation_threshold": coord.demand_deactivation_threshold,
            "source_entities": list(coord.source_entities),
            "usable_source_count": coord.usable_source_count,
            "degraded_source_entities": list(coord.degraded_source_entities),
            "inactive_source_entities": list(coord.inactive_source_entities),
        }


class PrimaryHeatingDemandActiveBinarySensor(_ClimateSyncBaseBinarySensor):
    """Heating demand across only the configured primary sources."""

    _attr_name = "Primary Heating Demand Active"
    _unique_id_suffix = "primary_heating_demand_active"

    @property
    def is_on(self) -> bool:
        """Return whether a primary source has active logical demand."""
        return self._coordinator.primary_demand_active

    @property
    def available(self) -> bool:
        """Return whether at least one primary source has valid demand data."""
        return self._coordinator.primary_usable_source_count > 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose primary scope and hysteresis context."""
        coord = self._coordinator
        return {
            "scope": "primary_sources",
            "delta_max": round(coord.primary_delta_max, 2),
            "leading_source": coord.primary_leading_room,
            "activation_threshold": coord.demand_activation_threshold,
            "deactivation_threshold": coord.demand_deactivation_threshold,
            "source_entities": list(coord.primary_source_entities),
            "usable_source_count": coord.primary_usable_source_count,
            "degraded_source_entities": list(coord.primary_degraded_source_entities),
            "inactive_source_entities": list(coord.primary_inactive_source_entities),
        }
