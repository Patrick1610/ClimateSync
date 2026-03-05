"""Tests for ClimateSyncCoordinator."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock the homeassistant package tree before importing coordinator
# ---------------------------------------------------------------------------
_mock_ha = MagicMock()

# homeassistant.core.callback must act as a transparent decorator
_mock_ha.core.callback = lambda fn: fn

# dt_util.utcnow – default return value; tests can override via patch
_mock_dt_util = MagicMock()
_NOW = datetime(2024, 1, 1, 12, 0, 0)
_mock_dt_util.utcnow = MagicMock(return_value=_NOW)

# Wire dt_util into the mock tree so `from homeassistant.util import dt` resolves
_mock_ha.util.dt = _mock_dt_util

_modules = {
    "homeassistant": _mock_ha,
    "homeassistant.config_entries": _mock_ha.config_entries,
    "homeassistant.core": _mock_ha.core,
    "homeassistant.helpers": _mock_ha.helpers,
    "homeassistant.helpers.event": _mock_ha.helpers.event,
    "homeassistant.util": _mock_ha.util,
    "homeassistant.util.dt": _mock_dt_util,
}

for mod_name, mod_obj in _modules.items():
    sys.modules.setdefault(mod_name, mod_obj)

# Now safe to import our code
from custom_components.climatesync.const import (  # noqa: E402
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
    STATUS_APPLY_FAILED,
    STATUS_MISMATCH,
    STATUS_MISSING_SOURCE_DATA,
    STATUS_OK,
    STATUS_RATE_LIMITED,
)
from custom_components.climatesync.coordinator import (  # noqa: E402
    ClimateSyncCoordinator,
    _apply_rounding,
    _safe_float,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(current_temperature: float | None, target_temperature: float | None, state: str = "heat") -> MagicMock:
    """Return a mock HA State object."""
    attrs: dict = {}
    if current_temperature is not None:
        attrs["current_temperature"] = current_temperature
    if target_temperature is not None:
        attrs["temperature"] = target_temperature
    s = MagicMock()
    s.state = state
    s.attributes = attrs
    return s


def _build_coordinator(
    *,
    source_entities: list[str] | None = None,
    destination_entity: str = "climate.dest",
    idle_temperature: float = DEFAULT_IDLE_TEMPERATURE,
    min_change_threshold: float = DEFAULT_MIN_CHANGE_THRESHOLD,
    min_send_interval: int = DEFAULT_MIN_SEND_INTERVAL,
    resync_interval: int = DEFAULT_RESYNC_INTERVAL,
    rounding_mode: str = DEFAULT_ROUNDING_MODE,
) -> tuple[ClimateSyncCoordinator, MagicMock]:
    """Build a coordinator with a fully-mocked hass/entry, return (coordinator, hass)."""
    if source_entities is None:
        source_entities = ["climate.room1"]

    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    entry = MagicMock()
    entry.data = {
        CONF_SOURCE_ENTITIES: source_entities,
        CONF_DESTINATION_ENTITY: destination_entity,
        CONF_IDLE_TEMPERATURE: idle_temperature,
    }
    entry.options = {
        CONF_ROUNDING_MODE: rounding_mode,
        CONF_MIN_CHANGE_THRESHOLD: min_change_threshold,
        CONF_MIN_SEND_INTERVAL: min_send_interval,
        CONF_RESYNC_INTERVAL: resync_interval,
    }

    coord = ClimateSyncCoordinator(hass, entry)
    # Apply config without setting up real HA listeners
    coord._source_entities = list(source_entities)
    coord._destination_entity = destination_entity
    coord._idle_temperature = float(idle_temperature)
    coord._rounding_mode = rounding_mode
    coord._min_change_threshold = float(min_change_threshold)
    coord._min_send_interval = int(min_send_interval)
    coord._resync_interval = int(resync_interval)

    return coord, hass


def _configure_states(hass: MagicMock, states: dict[str, MagicMock]) -> None:
    """Set ``hass.states.get`` to return per-entity mock states."""
    hass.states.get = lambda eid: states.get(eid)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSafeFloat:
    """Unit tests for _safe_float helper."""

    def test_valid(self):
        assert _safe_float("21.5") == 21.5

    def test_none(self):
        assert _safe_float(None) is None

    def test_unknown(self):
        assert _safe_float("unknown") is None

    def test_unavailable(self):
        assert _safe_float("unavailable") is None


class TestApplyRounding:
    """Unit tests for _apply_rounding helper."""

    def test_half_step(self):
        assert _apply_rounding(21.3, "half_step") == 21.5

    def test_1dec(self):
        assert _apply_rounding(21.34, "1_decimal") == 21.3

    def test_2dec(self):
        assert _apply_rounding(21.346, "2_decimals") == 21.35


# ---------------------------------------------------------------------------
# Core coordinator tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_mismatch_is_set():
    """STATUS_MISMATCH is set when destination target differs from computed setpoint."""
    coord, hass = _build_coordinator(min_change_threshold=0.2)

    # Source room: target 23, current 20 → delta 3
    # Destination: current 20, existing target 18 (will mismatch computed 23)
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=20.0, target_temperature=23.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=18.0),
    })

    # utcnow must advance so that mismatch_seconds > 0 on the second eval.
    # First eval sets mismatch_since; second eval computes elapsed > 0.
    t0 = datetime(2024, 1, 1, 12, 0, 0)
    t1 = t0 + timedelta(seconds=5)
    call_times = iter([t0, t0, t0, t0, t0,   # first eval calls
                       t1, t1, t1, t1, t1])   # second eval calls
    _mock_dt_util.utcnow = MagicMock(side_effect=lambda: next(call_times))

    try:
        await coord._async_evaluate()
        # After first eval mismatch_since is set but mismatch_seconds == 0.
        # The service call succeeds so status may be OK or MISMATCH depending on
        # mismatch_seconds.  Reset the service call time so rate-limit doesn't block.
        coord.last_service_call_time = None

        await coord._async_evaluate()

        assert coord.computed_setpoint == 23.0
        assert coord.mismatch_seconds > 0
        assert coord.status == STATUS_MISMATCH
    finally:
        _mock_dt_util.utcnow = MagicMock(return_value=_NOW)


@pytest.mark.asyncio
async def test_status_ok_when_in_sync():
    """STATUS_OK when destination target already matches computed setpoint."""
    coord, hass = _build_coordinator(min_change_threshold=0.2)

    # Source: target 22, current 20 → delta 2; dest current 20 → setpoint 22
    # Dest target already 22 → within threshold → anti-flap skips → no mismatch
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=20.0, target_temperature=22.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=22.0),
    })

    await coord._async_evaluate()

    assert coord.computed_setpoint == 22.0
    assert coord.status == STATUS_OK


@pytest.mark.asyncio
async def test_blocking_true_in_service_call():
    """The service call to set_temperature must use blocking=True."""
    coord, hass = _build_coordinator(min_change_threshold=0.2)

    # Force a mismatch large enough to trigger apply
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=20.0, target_temperature=25.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=18.0),
    })

    await coord._async_evaluate()

    hass.services.async_call.assert_called_once()
    _, kwargs = hass.services.async_call.call_args
    assert kwargs.get("blocking") is True


@pytest.mark.asyncio
async def test_anti_flap_skip_counter():
    """skipped_anti_flap increments when change is within threshold."""
    coord, hass = _build_coordinator(min_change_threshold=0.5)

    # Computed setpoint = dest_current + delta = 20 + 2 = 22.0
    # Dest target already 22.1 → diff 0.1 < threshold 0.5 → anti-flap skip
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=20.0, target_temperature=22.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=22.1),
    })

    assert coord.skipped_anti_flap == 0
    await coord._async_evaluate()
    assert coord.skipped_anti_flap == 1

    # Second evaluation should also skip
    await coord._async_evaluate()
    assert coord.skipped_anti_flap == 2


@pytest.mark.asyncio
async def test_rate_limit_skip_counter():
    """skipped_rate_limit increments when service call is rate-limited."""
    coord, hass = _build_coordinator(
        min_change_threshold=0.2,
        min_send_interval=60,
    )

    # First call: mismatch triggers actual service call
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=20.0, target_temperature=25.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=18.0),
    })

    await coord._async_evaluate()
    assert coord.skipped_rate_limit == 0
    assert hass.services.async_call.call_count == 1

    # Change dest target so anti-flap won't fire, but rate limit will
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=20.0, target_temperature=25.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=19.0),
    })

    await coord._async_evaluate()
    assert coord.skipped_rate_limit == 1
    assert coord.status == STATUS_RATE_LIMITED


@pytest.mark.asyncio
async def test_evaluation_count_increments():
    """evaluation_count increases with each _async_evaluate call."""
    coord, hass = _build_coordinator()

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=20.0, target_temperature=22.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=22.0),
    })

    assert coord.evaluation_count == 0
    await coord._async_evaluate()
    assert coord.evaluation_count == 1
    await coord._async_evaluate()
    assert coord.evaluation_count == 2
    await coord._async_evaluate()
    assert coord.evaluation_count == 3


@pytest.mark.asyncio
async def test_apply_failure_sets_status():
    """STATUS_APPLY_FAILED is set when the service call raises an exception."""
    coord, hass = _build_coordinator(min_change_threshold=0.2)

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=20.0, target_temperature=25.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=18.0),
    })

    hass.services.async_call = AsyncMock(side_effect=RuntimeError("connection lost"))

    await coord._async_evaluate()

    assert coord.status == STATUS_APPLY_FAILED
    assert coord.apply_failures == 1
    assert coord.last_error == "connection lost"


@pytest.mark.asyncio
async def test_missing_source_takes_priority_over_mismatch():
    """STATUS_MISSING_SOURCE_DATA is reported when a source is unavailable, even if there's a mismatch."""
    coord, hass = _build_coordinator(
        source_entities=["climate.room1", "climate.room2"],
        min_change_threshold=0.2,
    )

    # room1 is fine, room2 is unavailable
    # dest target 18 vs computed setpoint from room1 → mismatch exists too
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=20.0, target_temperature=25.0),
        "climate.room2": _make_state(current_temperature=None, target_temperature=None, state="unavailable"),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=18.0),
    })

    await coord._async_evaluate()

    # MISSING_SOURCE_DATA should take priority over MISMATCH
    assert coord.status == STATUS_MISSING_SOURCE_DATA


# ---------------------------------------------------------------------------
# Status priority ordering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_failed_takes_priority_over_rate_limited():
    """STATUS_APPLY_FAILED takes priority over all other evaluation-level statuses."""
    coord, hass = _build_coordinator(min_change_threshold=0.2)

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=20.0, target_temperature=25.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=18.0),
    })

    hass.services.async_call = AsyncMock(side_effect=RuntimeError("boom"))

    await coord._async_evaluate()
    assert coord.status == STATUS_APPLY_FAILED


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_destination_unavailable_when_state_unavailable():
    """STATUS_DESTINATION_UNAVAILABLE when destination entity is unavailable."""
    coord, hass = _build_coordinator()

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=20.0, target_temperature=22.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=22.0, state="unavailable"),
    })

    await coord._async_evaluate()

    assert coord.status == "destination_unavailable"
    # Service call should NOT have been attempted
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_destination_unavailable_when_entity_missing():
    """STATUS_DESTINATION_UNAVAILABLE when destination entity does not exist."""
    coord, hass = _build_coordinator()

    # Destination entity returns None (not in HA state machine)
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=20.0, target_temperature=22.0),
    })

    await coord._async_evaluate()

    assert coord.status == "destination_unavailable"
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_resync_increments_count():
    """Periodic resync callback increments resync_count and triggers evaluation."""
    coord, hass = _build_coordinator()

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=20.0, target_temperature=22.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=22.0),
    })

    assert coord.resync_count == 0
    coord._async_resync(None)
    assert coord.resync_count == 1
    coord._async_resync(None)
    assert coord.resync_count == 2

    # Verify async_create_task was called (evaluation triggered)
    assert hass.async_create_task.call_count == 2


@pytest.mark.asyncio
async def test_resync_skipped_when_apply_failed():
    """Resync should NOT trigger evaluation when status is APPLY_FAILED."""
    coord, hass = _build_coordinator()

    coord.status = STATUS_APPLY_FAILED
    coord._async_resync(None)
    assert coord.resync_count == 1
    # async_create_task should NOT have been called
    hass.async_create_task.assert_not_called()


@pytest.mark.asyncio
async def test_idle_temperature_when_no_demand():
    """When all rooms are satisfied (delta ≤ 0), destination gets idle temperature."""
    coord, hass = _build_coordinator(idle_temperature=5.0)

    # Room is already at target → delta = 0
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=22.0, target_temperature=22.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=20.0),
    })

    await coord._async_evaluate()

    assert coord.computed_setpoint == 5.0
    assert coord.delta_max == 0.0


# ---------------------------------------------------------------------------
# State-change filtering tests
# ---------------------------------------------------------------------------

def _make_event(old_state: MagicMock | None, new_state: MagicMock | None) -> MagicMock:
    """Return a mock state_changed event."""
    event = MagicMock()
    event.data = {"old_state": old_state, "new_state": new_state}
    return event


class TestHasRelevantChange:
    """Tests for _has_relevant_change static method."""

    def test_new_entity_added(self):
        """Evaluate when entity is newly added (old_state is None)."""
        new = _make_state(current_temperature=20.0, target_temperature=22.0)
        event = _make_event(None, new)
        assert ClimateSyncCoordinator._has_relevant_change(event) is True

    def test_entity_removed(self):
        """Evaluate when entity is removed (new_state is None)."""
        old = _make_state(current_temperature=20.0, target_temperature=22.0)
        event = _make_event(old, None)
        assert ClimateSyncCoordinator._has_relevant_change(event) is True

    def test_main_state_changed(self):
        """Evaluate when main state changes (e.g. heat → off)."""
        old = _make_state(current_temperature=20.0, target_temperature=22.0, state="heat")
        new = _make_state(current_temperature=20.0, target_temperature=22.0, state="off")
        event = _make_event(old, new)
        assert ClimateSyncCoordinator._has_relevant_change(event) is True

    def test_current_temperature_changed(self):
        """Evaluate when current_temperature changes."""
        old = _make_state(current_temperature=20.0, target_temperature=22.0)
        new = _make_state(current_temperature=20.5, target_temperature=22.0)
        event = _make_event(old, new)
        assert ClimateSyncCoordinator._has_relevant_change(event) is True

    def test_target_temperature_changed(self):
        """Evaluate when target temperature changes."""
        old = _make_state(current_temperature=20.0, target_temperature=22.0)
        new = _make_state(current_temperature=20.0, target_temperature=23.0)
        event = _make_event(old, new)
        assert ClimateSyncCoordinator._has_relevant_change(event) is True

    def test_irrelevant_attribute_change_ignored(self):
        """Skip evaluation when only non-temperature attributes change."""
        old = _make_state(current_temperature=20.0, target_temperature=22.0)
        old.attributes["hvac_action"] = "heating"
        new = _make_state(current_temperature=20.0, target_temperature=22.0)
        new.attributes["hvac_action"] = "idle"
        event = _make_event(old, new)
        assert ClimateSyncCoordinator._has_relevant_change(event) is False

    def test_no_change_at_all(self):
        """Skip evaluation when nothing changed."""
        old = _make_state(current_temperature=20.0, target_temperature=22.0)
        new = _make_state(current_temperature=20.0, target_temperature=22.0)
        event = _make_event(old, new)
        assert ClimateSyncCoordinator._has_relevant_change(event) is False

    def test_unavailable_state_triggers_evaluation(self):
        """Evaluate when entity becomes unavailable."""
        old = _make_state(current_temperature=20.0, target_temperature=22.0, state="heat")
        new = _make_state(current_temperature=20.0, target_temperature=22.0, state="unavailable")
        event = _make_event(old, new)
        assert ClimateSyncCoordinator._has_relevant_change(event) is True
