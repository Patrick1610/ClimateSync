"""ClimateSync coordinator: delta algorithm, anti-flap, rate limiting, resync."""
from __future__ import annotations

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
    ROUNDING_MODE_2DEC,
    ROUNDING_MODE_HALF,
    STATUS_APPLY_FAILED,
    STATUS_DESTINATION_UNAVAILABLE,
    STATUS_MISMATCH,
    STATUS_MISSING_SOURCE_DATA,
    STATUS_OK,
    STATUS_RATE_LIMITED,
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
        # Round UP to the nearest 0.5 so the setpoint is always high enough to
        # trigger heating.  Standard rounding could silently floor a small delta
        # (e.g. 19.2 → 19.0) and leave the destination thermostat below its own
        # activation threshold.  The inner round(..., 9) eliminates floating-point
        # noise so that exact multiples of 0.5 stay unchanged (19.0 → 19.0).
        return math.ceil(round(value * 2, 9)) / 2
    if mode == ROUNDING_MODE_2DEC:
        return round(value, 2)
    # Default / ROUNDING_MODE_1DEC
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
        self.evaluation_count: int = 0
        self.skipped_anti_flap: int = 0
        self.skipped_rate_limit: int = 0

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

    @staticmethod
    def _has_relevant_change(event: Any) -> bool:
        """Return True when temperature-relevant attributes changed.

        Only ``current_temperature``, ``temperature`` (target), and the main
        entity state (e.g. heat → off, unavailable) are considered relevant.
        Other attribute changes (hvac_action, preset_mode, …) are ignored so
        that integrations like Versatile Thermostat, which forward many TRV
        attribute updates, do not trigger unnecessary evaluations.
        """
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        # Entity added or removed — always evaluate
        if old_state is None or new_state is None:
            return True

        # Main state changed (e.g. heat → off, unavailable)
        if old_state.state != new_state.state:
            return True

        _ATTRS = ("current_temperature", "temperature")
        old_attrs = old_state.attributes
        new_attrs = new_state.attributes
        for attr in _ATTRS:
            if old_attrs.get(attr) != new_attrs.get(attr):
                return True

        return False

    def _setup_listeners(self) -> None:
        """Register state-change listeners and periodic resync."""
        if not self._source_entities and not self._destination_entity:
            return

        entities_to_watch = list(self._source_entities)
        if self._destination_entity:
            entities_to_watch.append(self._destination_entity)

        @callback
        def _handle_state_change(event: Any) -> None:
            if self._has_relevant_change(event):
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
        self.evaluation_count += 1
        has_missing = False

        _LOGGER.debug("ClimateSync: evaluation #%d started", self.evaluation_count)

        # Read destination state
        dest_state = self.hass.states.get(self._destination_entity)
        if dest_state is None or dest_state.state in ("unavailable", "unknown"):
            self.status = STATUS_DESTINATION_UNAVAILABLE
            _LOGGER.debug(
                "ClimateSync: destination %s unavailable",
                self._destination_entity,
            )
            self._notify_listeners()
            return

        self.destination_current_temperature = _safe_float(
            dest_state.attributes.get("current_temperature")
        )
        self.destination_current_target = _safe_float(
            dest_state.attributes.get("temperature")
        )

        _LOGGER.debug(
            "ClimateSync: destination current=%.2f target=%s",
            self.destination_current_temperature or 0.0,
            self.destination_current_target,
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
                _LOGGER.debug(
                    "ClimateSync: source %s unavailable", entity_id
                )
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

            _LOGGER.debug(
                "ClimateSync: room %s current=%.2f target=%.2f delta=%.2f",
                entity_id,
                current or 0.0,
                target or 0.0,
                delta,
            )

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

        _LOGGER.debug(
            "ClimateSync: computed setpoint=%.2f (max_delta=%.2f, leading=%s)",
            setpoint_final,
            max_delta,
            leading,
        )

        # Mismatch tracking
        if self.destination_current_target is not None:
            diff = abs(self.destination_current_target - setpoint_final)
            if diff > self._min_change_threshold:
                if self.mismatch_since is None:
                    self.mismatch_since = dt_util.utcnow()
                self.mismatch_seconds = (
                    dt_util.utcnow() - self.mismatch_since
                ).total_seconds()
                _LOGGER.debug(
                    "ClimateSync: mismatch detected, desired=%.2f actual=%.2f diff=%.2f for %.1fs",
                    setpoint_final,
                    self.destination_current_target,
                    diff,
                    self.mismatch_seconds,
                )
            else:
                self.mismatch_since = None
                self.mismatch_seconds = 0.0

        # Reset status before apply – apply may set RATE_LIMITED or APPLY_FAILED
        self.status = STATUS_OK

        # Apply setpoint
        await self._async_apply_setpoint(setpoint_final)

        # Determine final status: preserve apply-specific status, then layer on
        # evaluation-level statuses in priority order.
        if self.status not in (STATUS_APPLY_FAILED, STATUS_RATE_LIMITED):
            if has_missing:
                self.status = STATUS_MISSING_SOURCE_DATA
            elif self.mismatch_seconds > 0:
                self.status = STATUS_MISMATCH

        _LOGGER.debug(
            "ClimateSync: evaluation #%d finished, status=%s",
            self.evaluation_count,
            self.status,
        )

        self._notify_listeners()

    @callback
    def _async_resync(self, _now: Any) -> None:
        """Periodic resync callback."""
        self.resync_count += 1
        _LOGGER.debug("ClimateSync: periodic resync #%d", self.resync_count)
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
                self.skipped_anti_flap += 1
                _LOGGER.debug(
                    "ClimateSync: skipped (anti-flap), current=%.2f desired=%.2f diff=%.2f threshold=%.2f",
                    current_target,
                    setpoint,
                    abs(current_target - setpoint),
                    self._min_change_threshold,
                )
                return

        # Rate limiting
        if self.last_service_call_time is not None:
            elapsed = (dt_util.utcnow() - self.last_service_call_time).total_seconds()
            if elapsed < self._min_send_interval:
                self.skipped_rate_limit += 1
                self.status = STATUS_RATE_LIMITED
                _LOGGER.debug(
                    "ClimateSync: skipped (rate-limit), elapsed=%.1fs min_interval=%ds",
                    elapsed,
                    self._min_send_interval,
                )
                return

        self.apply_attempts += 1
        _LOGGER.debug(
            "ClimateSync: applying setpoint %.2f to %s (attempt #%d)",
            setpoint,
            self._destination_entity,
            self.apply_attempts,
        )
        try:
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {
                    "entity_id": self._destination_entity,
                    "temperature": setpoint,
                },
                blocking=True,
            )
            self.last_applied_setpoint = setpoint
            self.last_service_call_time = dt_util.utcnow()
            _LOGGER.debug(
                "ClimateSync: setpoint %.2f applied successfully",
                setpoint,
            )
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
