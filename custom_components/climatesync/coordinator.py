"""ClimateSync coordinator: delta algorithm, anti-flap, rate limiting, resync."""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DESTINATION_ENTITY,
    CONF_IDLE_TEMPERATURE,
    CONF_MIN_CHANGE_THRESHOLD,
    CONF_MIN_SEND_INTERVAL,
    CONF_RESYNC_INTERVAL,
    CONF_ROUNDING_MODE,
    CONF_SOURCE_ENTITIES,
    DEFAULT_IDLE_TEMPERATURE,
    DEFAULT_MIN_CHANGE_THRESHOLD,
    DEFAULT_MIN_SEND_INTERVAL,
    DEFAULT_RESYNC_INTERVAL,
    DEFAULT_ROUNDING_MODE,
    ROUNDING_MODE_1DEC,
    ROUNDING_MODE_2DEC,
    ROUNDING_MODE_HALF,
    STATUS_APPLY_FAILED,
    STATUS_DESTINATION_UNAVAILABLE,
    STATUS_MISMATCH,
    STATUS_MISSING_SOURCE_DATA,
    STATUS_OK,
    STATUS_RATE_LIMITED,
    STATUS_RESYNC_NEEDED,
)

_LOGGER = logging.getLogger(__name__)


def _safe_float(value: Any) -> float | None:
    """Convert a value to float, returning None if it is unavailable/unknown/missing."""
    if value is None:
        return None
    str_val = str(value).lower()
    if str_val in ("unknown", "unavailable", "none", ""):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _apply_rounding(value: float, mode: str) -> float:
    """Apply a rounding mode to a temperature value."""
    if mode == ROUNDING_MODE_HALF:
        return round(value * 2) / 2
    if mode == ROUNDING_MODE_2DEC:
        return round(value, 2)
    # Default: 1 decimal
    return round(value, 1)


class ClimateSyncCoordinator:
    """Drives delta-based thermostat synchronisation."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise coordinator."""
        self.hass = hass
        self.entry = entry

        # Resolved config / options
        self._source_entities: list[str] = []
        self._destination_entity: str = ""
        self._idle_temperature: float = DEFAULT_IDLE_TEMPERATURE
        self._rounding_mode: str = DEFAULT_ROUNDING_MODE
        self._resync_interval: int = DEFAULT_RESYNC_INTERVAL
        self._min_change_threshold: float = DEFAULT_MIN_CHANGE_THRESHOLD
        self._min_send_interval: int = DEFAULT_MIN_SEND_INTERVAL

        # Per-room deltas  {entity_id: {"delta": float, "current": float|None, "target": float|None}}
        self.room_deltas: dict[str, dict[str, Any]] = {}
        self.delta_max: float = 0.0
        self.leading_room: str | None = None

        # Destination tracking
        self.destination_current_temperature: float | None = None
        self.destination_current_target: float | None = None

        # Computed setpoint
        self.computed_setpoint: float | None = None

        # Diagnostics
        self.status: str = STATUS_OK
        self.last_update_time: datetime | None = None
        self.last_service_call_time: datetime | None = None
        self.last_desired_setpoint: float | None = None
        self.last_applied_setpoint: float | None = None
        self.mismatch_since: datetime | None = None
        self.mismatch_seconds: float = 0.0
        self.resync_count: int = 0
        self.apply_attempts: int = 0
        self.apply_failures: int = 0
        self.last_error: str | None = None

        # Internal
        self._unsub_state_listeners: list = []
        self._unsub_resync: Any | None = None
        self._listeners: list = []

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Set up coordinator, resolve config, register listeners."""
        self.async_apply_options()
        self._setup_listeners()
        # Do initial calculation
        await self._async_evaluate()

    def async_apply_options(self) -> None:
        """Re-read config entry data + options (called on options update)."""
        data = self.entry.data
        opts = self.entry.options

        self._source_entities = list(
            opts.get(CONF_SOURCE_ENTITIES, data.get(CONF_SOURCE_ENTITIES, []))
        )
        self._destination_entity = opts.get(
            CONF_DESTINATION_ENTITY, data.get(CONF_DESTINATION_ENTITY, "")
        )

        # Options override data for shared keys
        self._idle_temperature = float(
            opts.get(
                CONF_IDLE_TEMPERATURE,
                data.get(CONF_IDLE_TEMPERATURE, DEFAULT_IDLE_TEMPERATURE),
            )
        )
        self._rounding_mode = opts.get(
            CONF_ROUNDING_MODE,
            data.get(CONF_ROUNDING_MODE, DEFAULT_ROUNDING_MODE),
        )
        self._resync_interval = int(
            opts.get(CONF_RESYNC_INTERVAL, DEFAULT_RESYNC_INTERVAL)
        )
        self._min_change_threshold = float(
            opts.get(CONF_MIN_CHANGE_THRESHOLD, DEFAULT_MIN_CHANGE_THRESHOLD)
        )
        self._min_send_interval = int(
            opts.get(CONF_MIN_SEND_INTERVAL, DEFAULT_MIN_SEND_INTERVAL)
        )

        # Re-register listeners with updated intervals if already set up
        self._teardown_listeners()
        self._setup_listeners()

    def _setup_listeners(self) -> None:
        """Register state-change listeners and periodic resync."""
        if not self._source_entities and not self._destination_entity:
            return

        entities_to_watch = list(self._source_entities)
        if self._destination_entity:
            entities_to_watch.append(self._destination_entity)

        @callback
        def _handle_state_change(event: Any) -> None:
            self.hass.async_create_task(self._async_evaluate())

        self._unsub_state_listeners = [
            async_track_state_change_event(
                self.hass,
                entities_to_watch,
                _handle_state_change,
            )
        ]

        self._unsub_resync = async_track_time_interval(
            self.hass,
            self._async_resync,
            timedelta(seconds=self._resync_interval),
        )

    def _teardown_listeners(self) -> None:
        """Unsubscribe all listeners."""
        for unsub in self._unsub_state_listeners:
            unsub()
        self._unsub_state_listeners = []
        if self._unsub_resync is not None:
            self._unsub_resync()
            self._unsub_resync = None

    def async_teardown(self) -> None:
        """Tear down on unload."""
        self._teardown_listeners()

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    async def _async_evaluate(self) -> None:
        """Compute deltas, setpoint, and apply if needed."""
        self.last_update_time = dt_util.utcnow()
        has_missing = False

        # Read destination state
        dest_state = self.hass.states.get(self._destination_entity)
        if dest_state is None or dest_state.state in ("unavailable", "unknown"):
            self.status = STATUS_DESTINATION_UNAVAILABLE
            self._notify_listeners()
            return

        self.destination_current_temperature = _safe_float(
            dest_state.attributes.get("current_temperature")
        )
        self.destination_current_target = _safe_float(
            dest_state.attributes.get("temperature")
        )

        # Compute room deltas
        max_delta = 0.0
        leading = None
        for entity_id in self._source_entities:
            state: State | None = self.hass.states.get(entity_id)
            if state is None or state.state in ("unavailable", "unknown"):
                self.room_deltas[entity_id] = {
                    "delta": 0.0,
                    "current": None,
                    "target": None,
                    "raw_delta": 0.0,
                    "source_entity_id": entity_id,
                }
                has_missing = True
                continue

            current = _safe_float(state.attributes.get("current_temperature"))
            target = _safe_float(state.attributes.get("temperature"))

            if current is None or target is None:
                has_missing = True
                raw_delta = 0.0
                effective_raw_delta = 0.0
            else:
                raw_delta = target - current
                effective_raw_delta = raw_delta

            delta = max(effective_raw_delta, 0.0)

            self.room_deltas[entity_id] = {
                "delta": delta,
                "current": current,
                "target": target,
                "raw_delta": raw_delta,
                "source_entity_id": entity_id,
            }

            if delta > max_delta:
                max_delta = delta
                leading = entity_id

        self.delta_max = max_delta
        self.leading_room = leading

        # Compute setpoint
        if max_delta <= 0:
            setpoint_raw = self._idle_temperature
        else:
            dest_current = self.destination_current_temperature
            if dest_current is None:
                setpoint_raw = self._idle_temperature
            else:
                setpoint_raw = dest_current + max_delta

        setpoint_final = _apply_rounding(setpoint_raw, self._rounding_mode)
        self.computed_setpoint = setpoint_final
        self.last_desired_setpoint = setpoint_final

        # Mismatch tracking
        if self.destination_current_target is not None:
            diff = abs(self.destination_current_target - setpoint_final)
            if diff > self._min_change_threshold:
                if self.mismatch_since is None:
                    self.mismatch_since = dt_util.utcnow()
                self.mismatch_seconds = (
                    dt_util.utcnow() - self.mismatch_since
                ).total_seconds()
            else:
                self.mismatch_since = None
                self.mismatch_seconds = 0.0

        # Determine status
        if has_missing:
            self.status = STATUS_MISSING_SOURCE_DATA
        else:
            self.status = STATUS_OK

        # Apply setpoint
        await self._async_apply_setpoint(setpoint_final)
        self._notify_listeners()

    @callback
    def _async_resync(self, _now: Any) -> None:
        """Periodic resync callback."""
        self.resync_count += 1
        if self.status not in (
            STATUS_DESTINATION_UNAVAILABLE,
            STATUS_APPLY_FAILED,
        ):
            self.hass.async_create_task(self._async_evaluate())

    async def _async_apply_setpoint(self, setpoint: float) -> None:
        """Apply setpoint to destination if anti-flap and rate-limit allow."""
        current_target = self.destination_current_target

        # Anti-flap
        if current_target is not None:
            if abs(current_target - setpoint) <= self._min_change_threshold:
                return

        # Rate limiting
        if self.last_service_call_time is not None:
            elapsed = (dt_util.utcnow() - self.last_service_call_time).total_seconds()
            if elapsed < self._min_send_interval:
                self.status = STATUS_RATE_LIMITED
                return

        self.apply_attempts += 1
        try:
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {
                    "entity_id": self._destination_entity,
                    "temperature": setpoint,
                },
                blocking=False,
            )
            self.last_applied_setpoint = setpoint
            self.last_service_call_time = dt_util.utcnow()
        except Exception as exc:  # noqa: BLE001 – service calls can raise many HA-internal types
            self.apply_failures += 1
            self.last_error = str(exc)
            self.status = STATUS_APPLY_FAILED
            _LOGGER.error(
                "ClimateSync: failed to set temperature on %s: %s",
                self._destination_entity,
                exc,
            )

    # ------------------------------------------------------------------
    # Listener helpers (for sensor entities to subscribe)
    # ------------------------------------------------------------------

    def async_add_listener(self, update_callback: Any) -> Any:
        """Register a listener that is called after each evaluation."""
        self._listeners.append(update_callback)

        @callback
        def _remove() -> None:
            self._listeners.remove(update_callback)

        return _remove

    @callback
    def _notify_listeners(self) -> None:
        """Notify all registered listeners."""
        for listener in list(self._listeners):
            listener()

    # ------------------------------------------------------------------
    # Properties for sensors
    # ------------------------------------------------------------------

    @property
    def source_entities(self) -> list[str]:
        """Return source entity ids."""
        return self._source_entities

    @property
    def destination_entity(self) -> str:
        """Return destination entity id."""
        return self._destination_entity

    @property
    def idle_temperature(self) -> float:
        """Return idle temperature."""
        return self._idle_temperature

    @property
    def rounding_mode(self) -> str:
        """Return rounding mode."""
        return self._rounding_mode
