"""ClimateSync integration setup."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import CONF_PRIMARY_SOURCE_ENTITIES, CONF_SOURCE_ENTITIES, DOMAIN
from .coordinator import ClimateSyncCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor"]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate legacy entries while preserving their existing behaviour."""
    if entry.version == 1:
        data = dict(entry.data)
        effective_sources = list(
            entry.options.get(
                CONF_SOURCE_ENTITIES,
                data.get(CONF_SOURCE_ENTITIES, []),
            )
        )
        data.setdefault(CONF_PRIMARY_SOURCE_ENTITIES, effective_sources)
        hass.config_entries.async_update_entry(entry, data=data, version=2)
        _LOGGER.info(
            "Migrated ClimateSync entry %s to version 2; all existing sources "
            "remain primary until changed in Configure",
            entry.entry_id,
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ClimateSync from a config entry."""
    coordinator = ClimateSyncCoordinator(hass, entry)
    await coordinator.async_setup()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Register device
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="ClimateSync",
        manufacturer="Community",
        model="ClimateSync",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: ClimateSyncCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
    coordinator.async_teardown()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
