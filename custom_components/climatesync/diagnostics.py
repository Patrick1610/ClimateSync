"""Downloadable diagnostics for ClimateSync."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import ClimateSyncCoordinator


def _timestamp(value: datetime | None) -> str | None:
    """Return an ISO 8601 timestamp that diagnostics can serialise."""
    return value.isoformat() if value is not None else None


def _coordinator_diagnostics(
    coordinator: ClimateSyncCoordinator,
) -> dict[str, Any]:
    """Build a serialisable snapshot of coordinator configuration and state."""
    return {
        "status": coordinator.status,
        "configuration": {
            "source_entities": list(coordinator.source_entities),
            "primary_source_entities": list(coordinator.primary_source_entities),
            "destination_entity": coordinator.destination_entity,
            "destination_target": coordinator.destination_target,
            "idle_temperature": coordinator.idle_temperature,
            "maximum_setpoint": coordinator.max_setpoint,
            "rounding_mode": coordinator.rounding_mode,
            "rounding_direction": coordinator.rounding_direction,
            "resync_interval_seconds": coordinator.resync_interval,
            "minimum_change_threshold": coordinator.min_change_threshold,
            "minimum_send_interval_seconds": coordinator.min_send_interval,
            "demand_activation_threshold": coordinator.demand_activation_threshold,
            "demand_deactivation_threshold": coordinator.demand_deactivation_threshold,
        },
        "sources": {
            "room_deltas": {
                entity_id: dict(values)
                for entity_id, values in coordinator.room_deltas.items()
            },
            "delta_max": coordinator.delta_max,
            "leading_source": coordinator.leading_room,
            "demand_active": coordinator.demand_active,
            "usable_source_count": coordinator.usable_source_count,
            "degraded_source_entities": list(coordinator.degraded_source_entities),
            "inactive_source_entities": list(coordinator.inactive_source_entities),
        },
        "primary_sources": {
            "delta_max": coordinator.primary_delta_max,
            "leading_source": coordinator.primary_leading_room,
            "demand_active": coordinator.primary_demand_active,
            "usable_source_count": coordinator.primary_usable_source_count,
            "degraded_source_entities": list(
                coordinator.primary_degraded_source_entities
            ),
            "inactive_source_entities": list(
                coordinator.primary_inactive_source_entities
            ),
        },
        "destination": {
            "current_temperature": coordinator.destination_current_temperature,
            "current_target": coordinator.destination_current_target,
            "paired_target": coordinator.destination_paired_target,
        },
        "setpoint": {
            "raw": coordinator.raw_setpoint,
            "rounded": coordinator.rounded_setpoint,
            "final": coordinator.computed_setpoint,
            "last_desired": coordinator.last_desired_setpoint,
            "last_applied": coordinator.last_applied_setpoint,
        },
        "timing": {
            "last_update": _timestamp(coordinator.last_update_time),
            "last_service_call": _timestamp(coordinator.last_service_call_time),
            "mismatch_since": _timestamp(coordinator.mismatch_since),
            "mismatch_seconds": coordinator.mismatch_seconds,
        },
        "counters": {
            "resync": coordinator.resync_count,
            "evaluations": coordinator.evaluation_count,
            "apply_attempts": coordinator.apply_attempts,
            "apply_failures": coordinator.apply_failures,
            "skipped_anti_flap": coordinator.skipped_anti_flap,
            "skipped_rate_limit": coordinator.skipped_rate_limit,
        },
        "last_error": coordinator.last_error,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a ClimateSync config entry.

    ClimateSync does not store credentials or other secrets. The config-entry
    metadata is deliberately limited to schema versions; configured entity ids
    and operational settings are included in the coordinator snapshot because
    they are required to diagnose source and destination behaviour.
    """
    coordinator: ClimateSyncCoordinator = hass.data[DOMAIN][entry.entry_id]
    return {
        "config_entry": {
            "version": entry.version,
            "minor_version": entry.minor_version,
        },
        "coordinator": _coordinator_diagnostics(coordinator),
    }
