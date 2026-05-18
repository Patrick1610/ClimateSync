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
    CONF_ROUNDING_DIRECTION,
    CONF_ROUNDING_MODE,
    CONF_SOURCE_ENTITIES,
    DEFAULT_IDLE_TEMPERATURE,
    DEFAULT_MAX_SETPOINT,
    DEFAULT_MIN_CHANGE_THRESHOLD,
    DEFAULT_MIN_SEND_INTERVAL,
    DEFAULT_RESYNC_INTERVAL,
    DEFAULT_ROUNDING_DIRECTION,
    DEFAULT_ROUNDING_MODE,
    ROUNDING_DIRECTION_CEILING,
    ROUNDING_DIRECTION_FLOOR,
    ROUNDING_DIRECTION_NEAREST,
    ROUNDING_MODE_1DEC,
    ROUNDING_MODE_2DEC,
    ROUNDING_MODE_HALF,
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
    round_setpoint,
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
    rounding_direction: str | None = None,
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
    if rounding_direction is not None:
        entry.options[CONF_ROUNDING_DIRECTION] = rounding_direction

    coord = ClimateSyncCoordinator(hass, entry)
    # Apply config without setting up real HA listeners
    coord._source_entities = list(source_entities)
    coord._destination_entity = destination_entity
    coord._idle_temperature = float(idle_temperature)
    coord._max_setpoint = float(max_setpoint)
    coord._rounding_mode = rounding_mode
    if rounding_direction is not None:
        coord._rounding_direction = rounding_direction
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


class TestRoundSetpoint:
    """Unit tests for round_setpoint helper."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (19.2, 19.0),
            (19.3, 19.5),
            (19.0, 19.0),
            (19.5, 19.5),
        ],
    )
    def test_half_step_nearest(self, value: float, expected: float):
        assert (
            round_setpoint(value, ROUNDING_MODE_HALF, ROUNDING_DIRECTION_NEAREST)
            == expected
        )

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (19.1, 19.0),
            (19.5, 19.5),
            (19.9, 19.5),
            (19.0, 19.0),
        ],
    )
    def test_half_step_floor(self, value: float, expected: float):
        assert (
            round_setpoint(value, ROUNDING_MODE_HALF, ROUNDING_DIRECTION_FLOOR)
            == expected
        )

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (19.1, 19.5),
            (19.5, 19.5),
            (19.6, 20.0),
            (19.0, 19.0),
        ],
    )
    def test_half_step_ceiling(self, value: float, expected: float):
        assert (
            round_setpoint(value, ROUNDING_MODE_HALF, ROUNDING_DIRECTION_CEILING)
            == expected
        )

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (19.14, 19.1),
            (19.16, 19.2),
            (19.10, 19.1),
        ],
    )
    def test_1_decimal_nearest(self, value: float, expected: float):
        assert (
            round_setpoint(value, ROUNDING_MODE_1DEC, ROUNDING_DIRECTION_NEAREST)
            == expected
        )

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (19.19, 19.1),
            (19.11, 19.1),
            (19.10, 19.1),
        ],
    )
    def test_1_decimal_floor(self, value: float, expected: float):
        assert (
            round_setpoint(value, ROUNDING_MODE_1DEC, ROUNDING_DIRECTION_FLOOR)
            == expected
        )

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (19.11, 19.2),
            (19.19, 19.2),
            (19.10, 19.1),
        ],
    )
    def test_1_decimal_ceiling(self, value: float, expected: float):
        assert (
            round_setpoint(value, ROUNDING_MODE_1DEC, ROUNDING_DIRECTION_CEILING)
            == expected
        )

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (19.114, 19.11),
            (19.116, 19.12),
            (19.10, 19.1),
        ],
    )
    def test_2_decimals_nearest(self, value: float, expected: float):
        assert (
            round_setpoint(value, ROUNDING_MODE_2DEC, ROUNDING_DIRECTION_NEAREST)
            == expected
        )

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (19.119, 19.11),
            (19.111, 19.11),
            (19.10, 19.1),
        ],
    )
    def test_2_decimals_floor(self, value: float, expected: float):
        assert (
            round_setpoint(value, ROUNDING_MODE_2DEC, ROUNDING_DIRECTION_FLOOR)
            == expected
        )

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (19.111, 19.12),
            (19.119, 19.12),
            (19.10, 19.1),
        ],
    )
    def test_2_decimals_ceiling(self, value: float, expected: float):
        assert (
            round_setpoint(value, ROUNDING_MODE_2DEC, ROUNDING_DIRECTION_CEILING)
            == expected
        )

    def test_missing_rounding_direction_defaults_to_nearest(self):
        coord, _ = _build_coordinator(rounding_mode=ROUNDING_MODE_HALF)

        coord.async_apply_options()

        assert coord.rounding_direction == DEFAULT_ROUNDING_DIRECTION
        assert coord.rounding_direction == ROUNDING_DIRECTION_NEAREST
        assert round_setpoint(19.2, coord.rounding_mode, coord.rounding_direction) == 19.0

    def test_invalid_rounding_direction_falls_back_to_nearest(self):
        assert round_setpoint(19.3, ROUNDING_MODE_HALF, "invalid") == 19.5

    def test_invalid_rounding_mode_falls_back_to_default(self):
        assert round_setpoint(19.26, "invalid_mode", ROUNDING_DIRECTION_FLOOR) == 19.2

    def test_epsilon_safe_floor_and_ceiling(self):
        assert round_setpoint(19.5000000001, ROUNDING_MODE_HALF, ROUNDING_DIRECTION_FLOOR) == 19.5
        assert round_setpoint(19.4999999999, ROUNDING_MODE_HALF, ROUNDING_DIRECTION_CEILING) == 19.5


def test_apply_options_invalid_rounding_direction_defaults_to_nearest():
    coord, _ = _build_coordinator(rounding_mode=ROUNDING_MODE_HALF)
    coord.entry.options[CONF_ROUNDING_DIRECTION] = "bad_value"

    coord.async_apply_options()

    assert coord.rounding_direction == DEFAULT_ROUNDING_DIRECTION


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


@pytest.mark.asyncio
async def test_rounding_direction_applies_to_raw_setpoint_not_delta():
    """Rounding direction is applied after destination_current + delta_max."""
    coord, hass = _build_coordinator(
        min_change_threshold=0.0,
        rounding_mode=ROUNDING_MODE_HALF,
        rounding_direction=ROUNDING_DIRECTION_CEILING,
    )

    # delta=0.1 and destination_current=19.1 produce raw=19.2.
    # Ceiling raw 19.2 to half steps gives 19.5. If the delta were rounded
    # separately first, this would become 20.0, which is not desired.
    _configure_states(hass, {
        "climate.room1": _make_state(current_temperature=20.0, target_temperature=20.1),
        "climate.dest": _make_state(current_temperature=19.1, target_temperature=5.0),
    })

    await coord._async_evaluate()

    assert coord.raw_setpoint == pytest.approx(19.2)
    assert coord.rounded_setpoint == 19.5
    assert coord.computed_setpoint == 19.5
    call_args = hass.services.async_call.call_args
    assert call_args[0][2]["temperature"] == 19.5


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
