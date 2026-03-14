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
    CONF_MAX_SETPOINT,
    CONF_MIN_CHANGE_THRESHOLD,
    CONF_MIN_SEND_INTERVAL,
    CONF_RESYNC_INTERVAL,
    CONF_ROUNDING_MODE,
    CONF_SOURCE_ENTITIES,
    DEFAULT_IDLE_TEMPERATURE,
    DEFAULT_MAX_SETPOINT,
    DEFAULT_MIN_CHANGE_THRESHOLD,
    DEFAULT_MIN_SEND_INTERVAL,
    DEFAULT_RESYNC_INTERVAL,
    DEFAULT_ROUNDING_MODE,
    STATUS_APPLY_FAILED,
    STATUS_MISMATCH,
    STATUS_MISSING_SOURCE_DATA,
    STATUS_OFFSET_ENTITY_UNAVAILABLE,
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
    max_setpoint: float = DEFAULT_MAX_SETPOINT,
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
        CONF_MAX_SETPOINT: max_setpoint,
        CONF_MIN_CHANGE_THRESHOLD: min_change_threshold,
        CONF_MIN_SEND_INTERVAL: min_send_interval,
        CONF_RESYNC_INTERVAL: resync_interval,
    }

    coord = ClimateSyncCoordinator(hass, entry)
    # Apply config without setting up real HA listeners
    coord._source_entities = list(source_entities)
    coord._destination_entity = destination_entity
    coord._idle_temperature = float(idle_temperature)
    coord._max_setpoint = float(max_setpoint)
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
        # 21.3 → nearest 0.5 is 21.5
        assert _apply_rounding(21.3, "half_step") == 21.5

    def test_half_step_rounds_down(self):
        # 19.2 → nearest 0.5 is 19.0 (standard rounding, not ceiling)
        assert _apply_rounding(19.2, "half_step") == 19.0

    def test_half_step_exact_half_unchanged(self):
        assert _apply_rounding(19.0, "half_step") == 19.0
        assert _apply_rounding(19.5, "half_step") == 19.5

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


# ---------------------------------------------------------------------------
# Max setpoint cap tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_setpoint_clamps_computed_value():
    """Setpoint is clamped to max_setpoint when the raw value exceeds it."""
    # Simulate the Plugwise cascade: Emma current=19.9, delta=25.1 → raw 45.0
    # With max_setpoint=35 the coordinator must cap it at 35.
    coord, hass = _build_coordinator(max_setpoint=35.0, min_change_threshold=0.2)

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=9.9, target_temperature=35.0),
        "climate.dest": _make_state(current_temperature=19.9, target_temperature=5.0),
    })

    await coord._async_evaluate()

    # Raw setpoint = 19.9 + 25.1 = 45.0 – must be clamped to 35.0
    assert coord.computed_setpoint == 35.0
    # Service call must have been made with the capped value
    hass.services.async_call.assert_called_once()
    call_args = hass.services.async_call.call_args
    assert call_args[0][2]["temperature"] == 35.0


@pytest.mark.asyncio
async def test_max_setpoint_does_not_clamp_normal_value():
    """Setpoint below max_setpoint is passed through unchanged."""
    coord, hass = _build_coordinator(max_setpoint=35.0, min_change_threshold=0.2)

    # delta=5, dest_current=20 → setpoint=25, well below max_setpoint
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=15.0, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=5.0),
    })

    await coord._async_evaluate()

    assert coord.computed_setpoint == 25.0


# ---------------------------------------------------------------------------
# Double-listener bug regression test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_setup_registers_listeners_exactly_once():
    """async_setup must not create duplicate state listeners.

    Previously, async_apply_options() (called from async_setup) already
    registered listeners, but async_setup then called _setup_listeners() a
    second time, orphaning the first subscription.  After the fix, only one
    subscription should be stored.
    """
    coord, hass = _build_coordinator()

    # Patch _setup_listeners to count invocations
    call_count = {"n": 0}
    original = coord._setup_listeners

    def counting_setup():
        call_count["n"] += 1
        original()

    coord._setup_listeners = counting_setup

    # async_apply_options internally calls _setup_listeners (via _teardown+setup)
    coord.async_apply_options()

    assert call_count["n"] == 1, (
        "async_apply_options must call _setup_listeners exactly once"
    )


# ---------------------------------------------------------------------------
# Destination-triggered rate-limit bypass tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_bypassed_when_destination_triggers_evaluation():
    """When the destination itself changes its reported temperature, the rate
    limiter must be bypassed so ClimateSync corrects the mismatch immediately.

    Scenario mirrors the real-world Plugwise Emma 45°C incident:
    1. ClimateSync sets Emma to 35°C (last_service_call_time = now).
    2. Emma firmware autonomously reports 45°C ~2s later.
    3. The destination state-change event triggers _async_evaluate with
       bypass_rate_limit=True.
    4. Despite only 2s elapsing (well within min_send_interval=60s), the
       service call must still be made to push 35°C back.
    """
    coord, hass = _build_coordinator(
        min_change_threshold=0.2,
        min_send_interval=60,
    )

    # Step 1: simulate a prior service call recorded 2 seconds ago
    t0 = datetime(2024, 1, 1, 12, 0, 0)
    t2s = t0 + timedelta(seconds=2)

    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t2s)
        coord.last_service_call_time = t0  # last call was 2s ago

        # Step 2: destination reports 45, but computed setpoint is 35
        _configure_states(hass, {
            "climate.room1": _make_state(current_temperature=19.7, target_temperature=35.0),
            "climate.dest": _make_state(current_temperature=19.9, target_temperature=45.0),
        })

        # Step 3: evaluate as if triggered by the destination changing (bypass=True)
        await coord._async_evaluate(bypass_rate_limit=True)

    # Service call must have fired (rate limit bypassed)
    assert hass.services.async_call.call_count == 1
    call_args = hass.services.async_call.call_args
    assert call_args[0][2]["temperature"] == 35.0

    # skipped_rate_limit counter must NOT have been incremented
    assert coord.skipped_rate_limit == 0


@pytest.mark.asyncio
async def test_rate_limit_still_applies_for_source_triggered_evaluation():
    """Rate limiter must still apply when a source entity triggers the evaluation."""
    coord, hass = _build_coordinator(
        min_change_threshold=0.2,
        min_send_interval=60,
    )

    t0 = datetime(2024, 1, 1, 12, 0, 0)
    t2s = t0 + timedelta(seconds=2)

    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t2s)
        coord.last_service_call_time = t0  # last call was 2s ago

        _configure_states(hass, {
            "climate.room1": _make_state(current_temperature=19.7, target_temperature=35.0),
            "climate.dest": _make_state(current_temperature=19.9, target_temperature=20.0),
        })

        # Source-triggered evaluation (bypass=False, the default)
        await coord._async_evaluate(bypass_rate_limit=False)

    # Service call must NOT have fired (rate limited)
    hass.services.async_call.assert_not_called()
    assert coord.skipped_rate_limit == 1
    assert coord.status == STATUS_RATE_LIMITED


# ---------------------------------------------------------------------------
# Offset mode tests
# ---------------------------------------------------------------------------

from custom_components.climatesync.const import (  # noqa: E402
    CONF_MODE,
    CONF_OFFSET_ENTITY,
    MODE_DELTA,
    MODE_OFFSET,
)


def _build_offset_coordinator(
    *,
    source_entities: list[str] | None = None,
    destination_entity: str = "climate.dest",
    idle_temperature: float = DEFAULT_IDLE_TEMPERATURE,
    max_setpoint: float = DEFAULT_MAX_SETPOINT,
    min_change_threshold: float = DEFAULT_MIN_CHANGE_THRESHOLD,
    min_send_interval: int = DEFAULT_MIN_SEND_INTERVAL,
    offset_entity: str = "number.dest_offset",
    rounding_mode: str = DEFAULT_ROUNDING_MODE,
) -> tuple[ClimateSyncCoordinator, MagicMock]:
    """Build a coordinator configured for offset mode."""
    coord, hass = _build_coordinator(
        source_entities=source_entities,
        destination_entity=destination_entity,
        idle_temperature=idle_temperature,
        max_setpoint=max_setpoint,
        min_change_threshold=min_change_threshold,
        min_send_interval=min_send_interval,
        rounding_mode=rounding_mode,
    )
    coord._mode = MODE_OFFSET
    coord._offset_entity = offset_entity
    return coord, hass


def _make_number_state(value: float, step: float | None = None) -> MagicMock:
    """Return a mock number entity state."""
    s = MagicMock()
    s.state = str(value)
    attrs: dict = {}
    if step is not None:
        attrs["step"] = step
    s.attributes = attrs
    return s


@pytest.mark.asyncio
async def test_offset_mode_example1_first_sync():
    """Verify Offset Mode Example 1 from the problem spec (first sync)."""
    # Destination: real=19.2, offset=0.0, reported=19.2
    # Rooms:
    #   Living Room: 20.0 → 20.5 (delta 0.5)
    #   Bathroom: 18.0 → 19.5 (delta 1.5)  ← leading
    #   Office: 19.5 → 19.5 (delta 0)
    # Expected: new_offset = 18.0 - 19.2 = -1.2, new_target = 19.5

    coord, hass = _build_offset_coordinator(
        source_entities=["climate.living", "climate.bathroom", "climate.office"],
        min_change_threshold=0.05,  # low threshold to allow the change
    )

    _configure_states(hass, {
        "climate.living": _make_state(current_temperature=20.0, target_temperature=20.5),
        "climate.bathroom": _make_state(current_temperature=18.0, target_temperature=19.5),
        "climate.office": _make_state(current_temperature=19.5, target_temperature=19.5),
        "climate.dest": _make_state(current_temperature=19.2, target_temperature=20.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    await coord._async_evaluate()

    # Leading room should be bathroom (highest delta)
    assert coord.leading_room == "climate.bathroom"
    # Reconstructed real temperature
    assert abs(coord.destination_real_current - 19.2) < 0.001
    # New offset = 18.0 - 19.2 = -1.2
    assert abs(coord.desired_offset - (-1.2)) < 0.001
    # New target = 19.5
    assert coord.desired_target == 19.5

    # Verify service calls: first set_value (offset), then set_temperature
    assert hass.services.async_call.call_count == 2
    calls = hass.services.async_call.call_args_list
    # First call: set offset
    assert calls[0][0][0] == "number"
    assert calls[0][0][1] == "set_value"
    assert abs(calls[0][0][2]["value"] - (-1.2)) < 0.001
    # Second call: set temperature
    assert calls[1][0][0] == "climate"
    assert calls[1][0][1] == "set_temperature"
    assert calls[1][0][2]["temperature"] == 19.5


@pytest.mark.asyncio
async def test_offset_mode_example2_resync_with_new_leading_room():
    """Verify Offset Mode Example 2 from the problem spec (resync)."""
    # Current destination state: real=19.2, offset=-1.2, reported=18.0
    # New rooms:
    #   Living Room: 19.8 → 20.0 (delta 0.2)
    #   Bathroom: 19.2 → 19.5 (delta 0.3)
    #   Master Bedroom: 16.0 → 18.0 (delta 2.0) ← leading
    # Reconstruct real: 18.0 - (-1.2) = 19.2
    # new_offset = 16.0 - 19.2 = -3.2
    # new_target = 18.0

    coord, hass = _build_offset_coordinator(
        source_entities=["climate.living", "climate.bathroom", "climate.master"],
        min_change_threshold=0.05,
    )

    _configure_states(hass, {
        "climate.living": _make_state(current_temperature=19.8, target_temperature=20.0),
        "climate.bathroom": _make_state(current_temperature=19.2, target_temperature=19.5),
        "climate.master": _make_state(current_temperature=16.0, target_temperature=18.0),
        "climate.dest": _make_state(current_temperature=18.0, target_temperature=19.5),
        "number.dest_offset": _make_number_state(-1.2),
    })

    await coord._async_evaluate()

    assert coord.leading_room == "climate.master"
    assert abs(coord.destination_real_current - 19.2) < 0.001
    assert abs(coord.desired_offset - (-3.2)) < 0.001
    assert coord.desired_target == 18.0

    calls = hass.services.async_call.call_args_list
    assert len(calls) == 2
    assert abs(calls[0][0][2]["value"] - (-3.2)) < 0.001
    assert calls[1][0][2]["temperature"] == 18.0


@pytest.mark.asyncio
async def test_offset_mode_idle_when_no_demand():
    """Offset mode: when all deltas are 0, set idle temperature and reset offset to 0."""
    coord, hass = _build_offset_coordinator(
        min_change_threshold=0.05,
    )

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=21.0, target_temperature=21.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=21.0),
        "number.dest_offset": _make_number_state(-1.0),
    })

    await coord._async_evaluate()

    # computed_setpoint should be the idle temperature
    assert coord.computed_setpoint == DEFAULT_IDLE_TEMPERATURE

    # Offset MUST have been reset to 0 (offset was -1.0, which is meaningfully non-zero)
    calls = hass.services.async_call.call_args_list
    set_value_calls = [c for c in calls if c[0][1] == "set_value"]
    assert len(set_value_calls) == 1, "Offset must be reset to 0 when idle and non-zero"
    assert set_value_calls[0][0][2]["value"] == 0

    # idle_offset_reset_count should reflect the reset
    assert coord.idle_offset_reset_count == 1
    assert coord.last_idle_offset_reset_time is not None


@pytest.mark.asyncio
async def test_offset_mode_missing_offset_entity():
    """Offset mode: when offset entity is unavailable, status is set correctly."""
    coord, hass = _build_offset_coordinator(
        min_change_threshold=0.05,
    )

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=18.0, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.0, target_temperature=20.0),
        # offset entity is unavailable
        "number.dest_offset": _make_state(current_temperature=None, target_temperature=None, state="unavailable"),
    })

    # Must not raise
    await coord._async_evaluate()

    # Status must reflect offset entity unavailability (not destination_unavailable)
    assert coord.status == STATUS_OFFSET_ENTITY_UNAVAILABLE
    # No temperature calls should have been made
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_offset_mode_anti_flap_skips_when_no_change():
    """Offset mode: anti-flap skips when offset and target would not change meaningfully."""
    coord, hass = _build_offset_coordinator(
        min_change_threshold=0.5,
    )

    # Room delta = 1.0 (temp 19.0 → target 20.0)
    # Dest reported = 19.0, offset = 0.0 → real = 19.0
    # new_offset = 19.0 - 19.0 = 0.0 (no change)
    # new_target = 20.0, current dest target = 20.0 (no change)
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=19.0, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.0, target_temperature=20.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    initial_skipped = coord.skipped_anti_flap
    await coord._async_evaluate()

    # Anti-flap should have fired since neither offset nor target changes
    assert coord.skipped_anti_flap > initial_skipped
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_offset_mode_uses_entity_step_for_rounding():
    """Offset mode: offset is rounded according to the number entity's step attribute."""
    coord, hass = _build_offset_coordinator(
        min_change_threshold=0.05,
    )

    # Room: current=17.3, target=20.0 → delta 2.7
    # Dest: reported=19.0, offset=0.0 → real=19.0
    # Raw new_offset = 17.3 - 19.0 = -1.7
    # With step=0.5: round(-1.7 / 0.5) * 0.5 = round(-3.4) * 0.5 = -3 * 0.5 = -1.5
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=17.3, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.0, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0, step=0.5),
    })

    await coord._async_evaluate()

    # Offset should be rounded to nearest 0.5
    calls = hass.services.async_call.call_args_list
    assert len(calls) == 2
    applied_offset = calls[0][0][2]["value"]
    # Should be a multiple of 0.5
    assert abs(round(applied_offset / 0.5) * 0.5 - applied_offset) < 0.001


@pytest.mark.asyncio
async def test_offset_mode_input_number_domain():
    """Offset mode: set_value is called on input_number domain when used."""
    coord, hass = _build_offset_coordinator(
        offset_entity="input_number.dest_offset",
        min_change_threshold=0.05,
    )

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=18.0, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=5.0),
        "input_number.dest_offset": _make_number_state(0.0),
    })

    await coord._async_evaluate()

    calls = hass.services.async_call.call_args_list
    assert len(calls) == 2
    # First call must target input_number domain
    assert calls[0][0][0] == "input_number"
    assert calls[0][0][1] == "set_value"


@pytest.mark.asyncio
async def test_delta_mode_remains_default():
    """Existing delta-mode behaviour is unchanged when mode=delta (default)."""
    coord, hass = _build_coordinator(min_change_threshold=0.05)
    # Verify mode defaults to delta
    assert coord._mode == MODE_DELTA

    # delta = 2.0, dest_current = 20.0 → setpoint = 22.0
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=20.0, target_temperature=22.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=5.0),
    })

    await coord._async_evaluate()

    assert coord.computed_setpoint == 22.0
    calls = hass.services.async_call.call_args_list
    assert len(calls) == 1
    assert calls[0][0][1] == "set_temperature"


@pytest.mark.asyncio
async def test_offset_mode_leading_room_disappears_falls_back_to_idle():
    """Offset mode: when leading room loses temperature data, fall back to idle temperature."""
    coord, hass = _build_offset_coordinator(
        source_entities=["climate.room1", "climate.room2"],
        min_change_threshold=0.05,
    )

    # room1 becomes unavailable (no current/target temperatures), room2 has delta 0
    _configure_states(hass, {
        # room1 loses both temperatures mid-evaluation
        "climate.room1": _make_state(current_temperature=None, target_temperature=None, state="heat"),
        "climate.room2": _make_state(current_temperature=21.0, target_temperature=21.0),
        "climate.dest": _make_state(current_temperature=19.0, target_temperature=20.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    await coord._async_evaluate()

    # With no real demand (leading=None or leading has None temps), fall back to idle
    assert coord.computed_setpoint == DEFAULT_IDLE_TEMPERATURE


@pytest.mark.asyncio
async def test_offset_mode_no_offset_entity_configured_sets_status():
    """Offset mode: when no offset entity is configured at all, status is offset_entity_unavailable."""
    coord, hass = _build_offset_coordinator(
        min_change_threshold=0.05,
    )
    # Remove the offset entity from the coordinator (simulates mis-configuration)
    coord._offset_entity = None

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=18.0, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.0, target_temperature=20.0),
    })

    await coord._async_evaluate()

    assert coord.status == STATUS_OFFSET_ENTITY_UNAVAILABLE
    hass.services.async_call.assert_not_called()


# ---------------------------------------------------------------------------
# Offset Mode stabilization tests
# ---------------------------------------------------------------------------

from custom_components.climatesync.const import (  # noqa: E402
    CONF_OFFSET_SETTLE_SECONDS,
    CONF_OFFSET_MIN_CHANGE,
    CONF_OFFSET_MIN_INTERVAL_SECONDS,
    CONF_LEADER_SWITCH_THRESHOLD,
    CONF_LEADER_STICK_SECONDS,
    DEFAULT_OFFSET_SETTLE_SECONDS,
    DEFAULT_OFFSET_MIN_CHANGE,
    DEFAULT_OFFSET_MIN_INTERVAL_SECONDS,
    DEFAULT_LEADER_SWITCH_THRESHOLD,
    DEFAULT_LEADER_STICK_SECONDS,
)


def _build_offset_coordinator_stable(
    *,
    source_entities: list[str] | None = None,
    destination_entity: str = "climate.dest",
    idle_temperature: float = DEFAULT_IDLE_TEMPERATURE,
    max_setpoint: float = DEFAULT_MAX_SETPOINT,
    min_change_threshold: float = DEFAULT_MIN_CHANGE_THRESHOLD,
    min_send_interval: int = DEFAULT_MIN_SEND_INTERVAL,
    offset_entity: str = "number.dest_offset",
    rounding_mode: str = DEFAULT_ROUNDING_MODE,
    offset_settle_seconds: float = DEFAULT_OFFSET_SETTLE_SECONDS,
    offset_min_change: float = DEFAULT_OFFSET_MIN_CHANGE,
    offset_min_interval_seconds: float = DEFAULT_OFFSET_MIN_INTERVAL_SECONDS,
    leader_switch_threshold: float = DEFAULT_LEADER_SWITCH_THRESHOLD,
    leader_stick_seconds: float = DEFAULT_LEADER_STICK_SECONDS,
) -> tuple[ClimateSyncCoordinator, MagicMock]:
    """Build an offset-mode coordinator with stabilization settings."""
    coord, hass = _build_offset_coordinator(
        source_entities=source_entities,
        destination_entity=destination_entity,
        idle_temperature=idle_temperature,
        max_setpoint=max_setpoint,
        min_change_threshold=min_change_threshold,
        min_send_interval=min_send_interval,
        offset_entity=offset_entity,
        rounding_mode=rounding_mode,
    )
    coord._offset_settle_seconds = float(offset_settle_seconds)
    coord._offset_min_change = float(offset_min_change)
    coord._offset_min_interval_seconds = float(offset_min_interval_seconds)
    coord._leader_switch_threshold = float(leader_switch_threshold)
    coord._leader_stick_seconds = float(leader_stick_seconds)
    return coord, hass


# ── Settling window ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_offset_settling_window_blocks_source_triggered_eval():
    """Source-triggered eval during settling window should be skipped and counted."""
    coord, hass = _build_offset_coordinator_stable(
        min_change_threshold=0.05,
        offset_settle_seconds=10,  # 10 second window
    )

    t0 = datetime(2024, 1, 1, 12, 0, 0)
    t1 = t0 + timedelta(seconds=2)  # still inside settling window

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=18.0, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t0)
        # First eval: writes offset, starts settling window
        await coord._async_evaluate()

    assert hass.services.async_call.call_count == 2  # offset + target written
    assert coord._last_offset_write_at == t0
    assert coord._settling_until == t0 + timedelta(seconds=10)
    assert coord._possible_feedback_events == 0

    hass.services.async_call.reset_mock()

    # Second eval: source-triggered (bypass_rate_limit=False), within settling window
    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t1)
        await coord._async_evaluate(bypass_rate_limit=False)

    # No writes should have happened during settling
    hass.services.async_call.assert_not_called()
    assert coord._possible_feedback_events == 1


@pytest.mark.asyncio
async def test_offset_settling_window_does_not_block_destination_triggered():
    """Destination-triggered eval (bypass=True) bypasses the settling window."""
    coord, hass = _build_offset_coordinator_stable(
        min_change_threshold=0.05,
        offset_settle_seconds=30,
        min_send_interval=0,  # no rate limit
    )

    t0 = datetime(2024, 1, 1, 12, 0, 0)
    t1 = t0 + timedelta(seconds=2)

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=18.0, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t0)
        await coord._async_evaluate()

    assert coord._settling_until == t0 + timedelta(seconds=30)
    hass.services.async_call.reset_mock()

    # Destination-triggered eval bypasses settling
    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t1)
        await coord._async_evaluate(bypass_rate_limit=True)

    # Writes should still happen (bypass=True skips settling check)
    assert hass.services.async_call.call_count > 0
    assert coord._possible_feedback_events == 0


@pytest.mark.asyncio
async def test_offset_settling_window_expires():
    """After the settling window expires, source-triggered evals proceed normally."""
    coord, hass = _build_offset_coordinator_stable(
        min_change_threshold=0.05,
        offset_settle_seconds=5,
        min_send_interval=0,
    )

    t0 = datetime(2024, 1, 1, 12, 0, 0)
    t_expired = t0 + timedelta(seconds=6)  # after window

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=18.0, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t0)
        await coord._async_evaluate()

    assert coord._settling_until == t0 + timedelta(seconds=5)
    hass.services.async_call.reset_mock()

    # Source-triggered eval after settling window expires
    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t_expired)
        await coord._async_evaluate(bypass_rate_limit=False)

    # Should proceed normally (settling expired)
    assert coord._possible_feedback_events == 0


# ── Offset min change threshold ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_offset_min_change_blocks_small_offset_write():
    """Offset write is skipped when change is below offset_min_change threshold."""
    coord, hass = _build_offset_coordinator_stable(
        min_change_threshold=0.05,   # low combined threshold so anti-flap passes
        offset_min_change=0.5,       # high offset-specific threshold
        offset_min_interval_seconds=0,
    )

    # Room delta forces a small offset change (< 0.5) but large target change (> 0.05)
    # Dest: reported=20.0, offset=0.0 → real=20.0
    # Room: current=20.3, target=22.0 → desired_offset = 20.3 - 20.0 = 0.3 (< 0.5 threshold)
    # target_change = |22.0 - 5.0| = 17.0 → large, will be written
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=20.3, target_temperature=22.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    await coord._async_evaluate()

    # offset_threshold_skipped_count should increment
    assert coord._offset_threshold_skipped_count == 1
    # The offset set_value call must NOT have been made
    calls = hass.services.async_call.call_args_list
    for call in calls:
        assert call[0][1] != "set_value", "Offset must not be written (below min_change)"
    # But the target should still be written
    climate_calls = [c for c in calls if c[0][0] == "climate"]
    assert len(climate_calls) == 1


@pytest.mark.asyncio
async def test_offset_min_change_allows_large_offset_write():
    """Offset write proceeds when change exceeds offset_min_change threshold."""
    coord, hass = _build_offset_coordinator_stable(
        min_change_threshold=0.05,
        offset_min_change=0.2,
        offset_min_interval_seconds=0,
    )

    # Offset change = 2.0 - 0.0 = 2.0 > 0.2 threshold → write allowed
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=18.0, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    await coord._async_evaluate()

    assert coord._offset_threshold_skipped_count == 0
    calls = hass.services.async_call.call_args_list
    assert len(calls) == 2
    assert calls[0][0][1] == "set_value"  # offset written


# ── Offset min write interval ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_offset_min_interval_blocks_too_frequent_writes():
    """Offset write is blocked when min interval hasn't elapsed since last write."""
    coord, hass = _build_offset_coordinator_stable(
        min_change_threshold=0.05,
        offset_min_change=0.05,   # low, so change threshold won't block
        offset_min_interval_seconds=30,
        min_send_interval=0,
    )

    t0 = datetime(2024, 1, 1, 12, 0, 0)
    t5s = t0 + timedelta(seconds=5)   # within 30s interval

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=18.0, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t0)
        await coord._async_evaluate()

    assert hass.services.async_call.call_count == 2  # first write OK
    hass.services.async_call.reset_mock()

    # Second eval 5 seconds later: min interval not yet elapsed
    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t5s)
        await coord._async_evaluate(bypass_rate_limit=True)  # bypass general rate limit

    # Offset write should be blocked
    assert coord._offset_min_interval_blocked_count == 1
    calls = hass.services.async_call.call_args_list
    for call in calls:
        assert call[0][1] != "set_value", "Offset must not be written within min interval"


@pytest.mark.asyncio
async def test_offset_min_interval_allows_after_expiry():
    """Offset write is allowed once the min interval has elapsed."""
    coord, hass = _build_offset_coordinator_stable(
        min_change_threshold=0.05,
        offset_min_change=0.05,
        offset_min_interval_seconds=10,
        min_send_interval=0,
    )

    t0 = datetime(2024, 1, 1, 12, 0, 0)
    t15s = t0 + timedelta(seconds=15)  # beyond 10s interval

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=18.0, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=20.0, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t0)
        await coord._async_evaluate()

    hass.services.async_call.reset_mock()

    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t15s)
        await coord._async_evaluate(bypass_rate_limit=True)

    assert coord._offset_min_interval_blocked_count == 0
    calls = hass.services.async_call.call_args_list
    assert any(c[0][1] == "set_value" for c in calls), "Offset write expected after interval"


# ── Sticky leader hysteresis ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sticky_leader_prevents_switch_below_threshold():
    """Leader does not switch when the new candidate doesn't exceed the threshold."""
    coord, hass = _build_offset_coordinator_stable(
        source_entities=["climate.room1", "climate.room2"],
        min_change_threshold=0.05,
        offset_min_change=0.05,
        offset_min_interval_seconds=0,
        leader_switch_threshold=0.5,   # high threshold
        leader_stick_seconds=3600,     # very long stick time
    )

    # Initial eval: room1 is leading (delta 1.0)
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=19.0, target_temperature=20.0),
        "climate.room2": _make_state(current_temperature=19.5, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    await coord._async_evaluate()

    assert coord._current_leading_room == "climate.room1"
    initial_leading = coord.leading_room

    hass.services.async_call.reset_mock()

    # Second eval: room2 leads by 0.3 (below 0.5 threshold, no stick time yet)
    # room2 delta = 0.8, room1 delta = 0.5 → diff = 0.3 < threshold 0.5
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=19.5, target_temperature=20.0),
        "climate.room2": _make_state(current_temperature=19.2, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    await coord._async_evaluate()

    # Leader should still be room1 (hysteresis kept it)
    assert coord._current_leading_room == "climate.room1"


@pytest.mark.asyncio
async def test_sticky_leader_switches_when_exceeds_threshold():
    """Leader switches immediately when new room exceeds by more than the threshold."""
    coord, hass = _build_offset_coordinator_stable(
        source_entities=["climate.room1", "climate.room2"],
        min_change_threshold=0.05,
        offset_min_change=0.05,
        offset_min_interval_seconds=0,
        leader_switch_threshold=0.3,   # threshold
        leader_stick_seconds=3600,
    )

    # Initial eval: room1 is leading (delta 1.0)
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=19.0, target_temperature=20.0),
        "climate.room2": _make_state(current_temperature=19.5, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    await coord._async_evaluate()
    assert coord._current_leading_room == "climate.room1"

    hass.services.async_call.reset_mock()

    # Second eval: room2 leads by 1.5 (>> 0.3 threshold) → immediate switch
    # room2 delta = 2.5, room1 delta = 0.5 → diff = 2.0 > threshold 0.3
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=19.5, target_temperature=20.0),
        "climate.room2": _make_state(current_temperature=17.5, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    await coord._async_evaluate()

    # Leader should now be room2
    assert coord._current_leading_room == "climate.room2"
    assert coord._previous_leading_room == "climate.room1"


@pytest.mark.asyncio
async def test_sticky_leader_switches_after_stick_seconds():
    """Leader switches after the candidate has been consistently leading for stick_seconds."""
    coord, hass = _build_offset_coordinator_stable(
        source_entities=["climate.room1", "climate.room2"],
        min_change_threshold=0.05,
        offset_min_change=0.05,
        offset_min_interval_seconds=0,
        leader_switch_threshold=1.0,   # high threshold, won't trigger immediately
        leader_stick_seconds=30,
    )

    t0 = datetime(2024, 1, 1, 12, 0, 0)
    t35s = t0 + timedelta(seconds=35)  # beyond stick time

    # Initial eval: room1 is leading
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=19.0, target_temperature=20.0),
        "climate.room2": _make_state(current_temperature=19.5, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t0)
        await coord._async_evaluate()

    assert coord._current_leading_room == "climate.room1"

    # room2 leads by 0.3 (below 1.0 threshold) from t0 onwards
    # After 35s candidate timer, room2 should take over
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=19.5, target_temperature=20.0),
        "climate.room2": _make_state(current_temperature=19.2, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t0)
        # First eval to start candidate timer
        await coord._async_evaluate()

    assert coord._candidate_leading_room == "climate.room2"
    assert coord._current_leading_room == "climate.room1"  # not yet switched

    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t35s)
        await coord._async_evaluate()

    # Now the candidate timer has expired → switch
    assert coord._current_leading_room == "climate.room2"


@pytest.mark.asyncio
async def test_sticky_leader_candidate_resets_when_raw_leader_changes():
    """Candidate timer resets if a different room becomes the raw leader."""
    coord, hass = _build_offset_coordinator_stable(
        source_entities=["climate.room1", "climate.room2", "climate.room3"],
        min_change_threshold=0.05,
        offset_min_change=0.05,
        offset_min_interval_seconds=0,
        leader_switch_threshold=1.0,   # high, no immediate switch
        leader_stick_seconds=30,
    )

    t0 = datetime(2024, 1, 1, 12, 0, 0)

    # Establish room1 as leader
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=18.0, target_temperature=20.0),
        "climate.room2": _make_state(current_temperature=19.5, target_temperature=20.0),
        "climate.room3": _make_state(current_temperature=19.8, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t0)
        await coord._async_evaluate()

    assert coord._current_leading_room == "climate.room1"

    # room2 slightly leads (starts candidate timer)
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=19.5, target_temperature=20.0),
        "climate.room2": _make_state(current_temperature=19.2, target_temperature=20.0),
        "climate.room3": _make_state(current_temperature=19.8, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    t5 = t0 + timedelta(seconds=5)
    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t5)
        await coord._async_evaluate()

    assert coord._candidate_leading_room == "climate.room2"
    candidate_since = coord._candidate_leading_since

    # Now room3 suddenly leads instead of room2 (resets candidate)
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=19.5, target_temperature=20.0),
        "climate.room2": _make_state(current_temperature=19.8, target_temperature=20.0),
        "climate.room3": _make_state(current_temperature=19.1, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    t10 = t0 + timedelta(seconds=10)
    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t10)
        await coord._async_evaluate()

    # Candidate should have changed to room3, timer reset
    assert coord._candidate_leading_room == "climate.room3"
    assert coord._candidate_leading_since != candidate_since  # reset
    assert coord._current_leading_room == "climate.room1"  # still room1


# ── Diagnostic properties ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_offset_stabilization_diagnostics_exposed():
    """All stabilization diagnostic properties are accessible on the coordinator."""
    coord, hass = _build_offset_coordinator_stable(
        min_change_threshold=0.05,
        offset_settle_seconds=5,
        offset_min_change=0.05,
        offset_min_interval_seconds=0,
    )

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=18.0, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    t0 = datetime(2024, 1, 1, 12, 0, 0)
    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t0)
        await coord._async_evaluate()

    # Verify diagnostic properties
    assert coord.last_offset_write_time == t0
    assert coord.last_target_write_time == t0
    assert coord.settling_until == t0 + timedelta(seconds=5)
    assert coord.settling_active is True  # still inside window at t0
    assert coord.possible_feedback_events == 0
    assert coord.offset_min_interval_blocked_count == 0
    assert coord.offset_threshold_skipped_count == 0
    assert coord.current_leading_room == "climate.room1"
    assert coord.previous_leading_room is None  # first time


@pytest.mark.asyncio
async def test_possible_feedback_events_counted():
    """possible_feedback_events increments on source evals during settling window."""
    coord, hass = _build_offset_coordinator_stable(
        min_change_threshold=0.05,
        offset_settle_seconds=10,
    )

    t0 = datetime(2024, 1, 1, 12, 0, 0)
    t1 = t0 + timedelta(seconds=1)
    t2 = t0 + timedelta(seconds=2)

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=18.0, target_temperature=20.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=5.0),
        "number.dest_offset": _make_number_state(0.0),
    })

    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t0)
        await coord._async_evaluate()

    assert coord._possible_feedback_events == 0

    # Two source-triggered evals within settling window
    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t1)
        await coord._async_evaluate(bypass_rate_limit=False)

    with patch("custom_components.climatesync.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=t2)
        await coord._async_evaluate(bypass_rate_limit=False)

    assert coord._possible_feedback_events == 2
    assert coord.possible_feedback_events == 2


# ---------------------------------------------------------------------------
# Idle offset reset tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idle_offset_reset_does_not_fire_when_offset_already_zero():
    """Offset idle reset must NOT call set_value when offset is already 0."""
    coord, hass = _build_offset_coordinator(
        min_change_threshold=0.05,
    )

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=21.0, target_temperature=21.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=21.0),
        "number.dest_offset": _make_number_state(0.0),  # already zero
    })

    await coord._async_evaluate()

    assert coord.computed_setpoint == DEFAULT_IDLE_TEMPERATURE

    calls = hass.services.async_call.call_args_list
    set_value_calls = [c for c in calls if c[0][1] == "set_value"]
    assert len(set_value_calls) == 0, "Offset must NOT be written when already 0"
    assert coord.idle_offset_reset_count == 0


@pytest.mark.asyncio
async def test_idle_offset_reset_does_not_fire_when_below_min_change():
    """Offset idle reset is skipped when offset is below offset_min_change threshold."""
    coord, hass = _build_offset_coordinator_stable(
        min_change_threshold=0.05,
        offset_min_change=0.5,  # high threshold
    )
    # offset = 0.1, abs(0.1) < offset_min_change (0.5) → no reset
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=21.0, target_temperature=21.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=21.0),
        "number.dest_offset": _make_number_state(0.1),
    })

    await coord._async_evaluate()

    calls = hass.services.async_call.call_args_list
    set_value_calls = [c for c in calls if c[0][1] == "set_value"]
    assert len(set_value_calls) == 0, "Offset reset skipped because change < offset_min_change"
    assert coord.idle_offset_reset_count == 0


@pytest.mark.asyncio
async def test_idle_offset_reset_count_increments_each_reset():
    """idle_offset_reset_count increments on each idle-triggered offset reset."""
    coord, hass = _build_offset_coordinator(
        min_change_threshold=0.05,
    )

    # First idle evaluation with non-zero offset
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=21.0, target_temperature=21.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=21.0),
        "number.dest_offset": _make_number_state(-1.5),
    })

    await coord._async_evaluate()
    assert coord.idle_offset_reset_count == 1
    assert coord.last_idle_offset_reset_time is not None
    first_reset_time = coord.last_idle_offset_reset_time

    # Second idle evaluation with offset still non-zero (simulate it wasn't applied yet)
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=21.0, target_temperature=21.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=21.0),
        "number.dest_offset": _make_number_state(-1.5),
    })

    await coord._async_evaluate()
    assert coord.idle_offset_reset_count == 2


@pytest.mark.asyncio
async def test_idle_offset_reset_uses_set_value_with_domain():
    """Idle offset reset calls set_value on the correct entity domain."""
    coord, hass = _build_offset_coordinator(
        offset_entity="input_number.dest_offset",
        min_change_threshold=0.05,
    )

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=21.0, target_temperature=21.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=21.0),
        "input_number.dest_offset": _make_number_state(-2.0),
    })

    await coord._async_evaluate()

    calls = hass.services.async_call.call_args_list
    set_value_calls = [c for c in calls if c[0][1] == "set_value"]
    assert len(set_value_calls) == 1
    assert set_value_calls[0][0][0] == "input_number"
    assert set_value_calls[0][0][2]["value"] == 0


@pytest.mark.asyncio
async def test_idle_offset_reset_order_target_first():
    """Idle offset reset must set the target temperature BEFORE resetting the offset."""
    coord, hass = _build_offset_coordinator(
        min_change_threshold=0.05,
    )

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=21.0, target_temperature=21.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=21.0),
        "number.dest_offset": _make_number_state(-1.0),
    })

    await coord._async_evaluate()

    calls = hass.services.async_call.call_args_list
    # There should be 2 calls: set_temperature first, then set_value
    climate_idx = next(i for i, c in enumerate(calls) if c[0][1] == "set_temperature")
    offset_idx = next(i for i, c in enumerate(calls) if c[0][1] == "set_value")
    assert climate_idx < offset_idx, "Target must be set before offset reset"
    # Target should be idle temperature
    assert calls[climate_idx][0][2]["temperature"] == DEFAULT_IDLE_TEMPERATURE
    # Offset should be reset to 0
    assert calls[offset_idx][0][2]["value"] == 0


@pytest.mark.asyncio
async def test_idle_offset_reset_failure_sets_apply_failed_status():
    """When idle offset reset service call fails, status is set to apply_failed."""
    coord, hass = _build_offset_coordinator(
        min_change_threshold=0.05,
    )

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=21.0, target_temperature=21.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=21.0),
        "number.dest_offset": _make_number_state(-1.0),
    })

    call_count = {"n": 0}
    original_call = hass.services.async_call

    async def selective_fail(*args, **kwargs):
        call_count["n"] += 1
        # Fail only on the second call (offset reset), not the first (set_temperature)
        if call_count["n"] == 2:
            raise RuntimeError("offset write failed")
        return await original_call(*args, **kwargs)

    hass.services.async_call = selective_fail

    await coord._async_evaluate()

    assert coord.status == STATUS_APPLY_FAILED
    assert coord.apply_failures == 1
    assert coord.idle_offset_reset_count == 0


@pytest.mark.asyncio
async def test_idle_offset_reset_skipped_when_no_offset_entity():
    """When no offset entity is configured, idle path still works (no crash)."""
    coord, hass = _build_offset_coordinator(
        min_change_threshold=0.05,
    )
    coord._offset_entity = None  # no offset entity

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=21.0, target_temperature=21.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=21.0),
    })

    # Must not raise
    await coord._async_evaluate()

    assert coord.computed_setpoint == DEFAULT_IDLE_TEMPERATURE
    assert coord.idle_offset_reset_count == 0
    calls = hass.services.async_call.call_args_list
    set_value_calls = [c for c in calls if c[0][1] == "set_value"]
    assert len(set_value_calls) == 0


@pytest.mark.asyncio
async def test_idle_offset_reset_skipped_when_offset_entity_unavailable():
    """When offset entity is unavailable during idle, offset reset is gracefully skipped."""
    coord, hass = _build_offset_coordinator(
        min_change_threshold=0.05,
    )

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=21.0, target_temperature=21.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=21.0),
        "number.dest_offset": _make_state(current_temperature=None, target_temperature=None, state="unavailable"),
    })

    await coord._async_evaluate()

    assert coord.computed_setpoint == DEFAULT_IDLE_TEMPERATURE
    assert coord.idle_offset_reset_count == 0


@pytest.mark.asyncio
async def test_idle_offset_reset_last_applied_offset_set_to_zero():
    """After idle reset, last_applied_offset and last_offset_write_time are updated."""
    coord, hass = _build_offset_coordinator(
        min_change_threshold=0.05,
    )

    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=21.0, target_temperature=21.0),
        "climate.dest": _make_state(current_temperature=19.5, target_temperature=21.0),
        "number.dest_offset": _make_number_state(-2.0),
    })

    await coord._async_evaluate()

    assert coord.last_applied_offset == 0.0
    assert coord._last_offset_write_at is not None
