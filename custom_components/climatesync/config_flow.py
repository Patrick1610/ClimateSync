"""Config flow for ClimateSync."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DEMAND_ACTIVATION_THRESHOLD,
    CONF_DEMAND_DEACTIVATION_THRESHOLD,
    CONF_DESTINATION_ENTITY,
    CONF_DESTINATION_TARGET,
    CONF_IDLE_TEMPERATURE,
    CONF_MAX_SETPOINT,
    CONF_MIN_CHANGE_THRESHOLD,
    CONF_MIN_SEND_INTERVAL,
    CONF_PRIMARY_SOURCE_ENTITIES,
    CONF_RESYNC_INTERVAL,
    CONF_ROUNDING_DIRECTION,
    CONF_ROUNDING_MODE,
    CONF_SOURCE_ENTITIES,
    DEFAULT_DEMAND_ACTIVATION_THRESHOLD,
    DEFAULT_DEMAND_DEACTIVATION_THRESHOLD,
    DEFAULT_DESTINATION_TARGET,
    DEFAULT_IDLE_TEMPERATURE,
    DEFAULT_MAX_SETPOINT,
    DEFAULT_MIN_CHANGE_THRESHOLD,
    DEFAULT_MIN_SEND_INTERVAL,
    DEFAULT_RESYNC_INTERVAL,
    DEFAULT_ROUNDING_DIRECTION,
    DEFAULT_ROUNDING_MODE,
    DESTINATION_TARGET_HIGH,
    DESTINATION_TARGET_LOW,
    DESTINATION_TARGET_TEMPERATURE,
    DESTINATION_TARGETS,
    DOMAIN,
    ROUNDING_DIRECTIONS,
    ROUNDING_MODES,
)

# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────


def _sources_schema(default_sources: list[str] | None = None) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_SOURCE_ENTITIES,
                default=default_sources or [],
            ): selector.selector(
                {
                    "entity": {
                        "domain": CLIMATE_DOMAIN,
                        "multiple": True,
                    }
                }
            ),
        }
    )


def _primary_sources_schema(
    sources: list[str], default_sources: list[str] | None = None
) -> vol.Schema:
    """Return a selector limited to the source climates chosen in step 1."""
    defaults = [source for source in (default_sources or []) if source in sources]
    return vol.Schema(
        {
            vol.Required(
                CONF_PRIMARY_SOURCE_ENTITIES,
                default=defaults,
            ): selector.selector(
                {
                    "select": {
                        "multiple": True,
                        "mode": "list",
                        "options": [
                            {"value": source, "label": source} for source in sources
                        ],
                    }
                }
            ),
        }
    )


def _destination_schema(
    default_dest: str | None = None,
    default_destination_target: str = DEFAULT_DESTINATION_TARGET,
    default_idle: float = DEFAULT_IDLE_TEMPERATURE,
    default_max_setpoint: float = DEFAULT_MAX_SETPOINT,
    default_rounding: str = DEFAULT_ROUNDING_MODE,
    default_rounding_direction: str = DEFAULT_ROUNDING_DIRECTION,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_DESTINATION_ENTITY, default=default_dest
            ): selector.selector(
                {
                    "entity": {
                        "domain": CLIMATE_DOMAIN,
                    }
                }
            ),
            vol.Required(
                CONF_DESTINATION_TARGET,
                default=default_destination_target,
            ): selector.selector(
                {
                    "select": {
                        "options": [
                            {"value": target, "label": target}
                            for target in DESTINATION_TARGETS
                        ],
                        "translation_key": "destination_target",
                    }
                }
            ),
            vol.Required(
                CONF_IDLE_TEMPERATURE, default=default_idle
            ): selector.selector(
                {
                    "number": {
                        "min": -10.0,
                        "max": 25.0,
                        "step": 0.5,
                        "mode": "box",
                        "unit_of_measurement": "°C",
                    }
                }
            ),
            vol.Required(
                CONF_MAX_SETPOINT, default=default_max_setpoint
            ): selector.selector(
                {
                    "number": {
                        "min": 10.0,
                        "max": 60.0,
                        "step": 0.5,
                        "mode": "box",
                        "unit_of_measurement": "°C",
                    }
                }
            ),
            vol.Required(
                CONF_ROUNDING_MODE, default=default_rounding
            ): selector.selector(
                {
                    "select": {
                        "options": [
                            {"value": mode, "label": mode} for mode in ROUNDING_MODES
                        ],
                        "translation_key": "rounding_mode",
                    }
                }
            ),
            vol.Required(
                CONF_ROUNDING_DIRECTION,
                default=default_rounding_direction,
            ): selector.selector(
                {
                    "select": {
                        "options": [
                            {"value": direction, "label": direction}
                            for direction in ROUNDING_DIRECTIONS
                        ],
                        "translation_key": "rounding_direction",
                    }
                }
            ),
        }
    )


def _control_schema(
    default_resync: int = DEFAULT_RESYNC_INTERVAL,
    default_threshold: float = DEFAULT_MIN_CHANGE_THRESHOLD,
    default_send_interval: int = DEFAULT_MIN_SEND_INTERVAL,
    default_demand_activation: float = DEFAULT_DEMAND_ACTIVATION_THRESHOLD,
    default_demand_deactivation: float = DEFAULT_DEMAND_DEACTIVATION_THRESHOLD,
) -> vol.Schema:
    """Return advanced control and hysteresis settings."""
    return vol.Schema(
        {
            vol.Required(
                CONF_RESYNC_INTERVAL, default=default_resync
            ): selector.selector(
                {
                    "number": {
                        "min": 10,
                        "max": 3600,
                        "step": 1,
                        "mode": "box",
                        "unit_of_measurement": "s",
                    }
                }
            ),
            vol.Required(
                CONF_MIN_CHANGE_THRESHOLD, default=default_threshold
            ): selector.selector(
                {
                    "number": {
                        "min": 0.0,
                        "max": 5.0,
                        "step": 0.1,
                        "mode": "box",
                        "unit_of_measurement": "°C",
                    }
                }
            ),
            vol.Required(
                CONF_DEMAND_ACTIVATION_THRESHOLD,
                default=default_demand_activation,
            ): selector.selector(
                {
                    "number": {
                        "min": 0.1,
                        "max": 5.0,
                        "step": 0.1,
                        "mode": "box",
                        "unit_of_measurement": "°C",
                    }
                }
            ),
            vol.Required(
                CONF_DEMAND_DEACTIVATION_THRESHOLD,
                default=default_demand_deactivation,
            ): selector.selector(
                {
                    "number": {
                        "min": 0.0,
                        "max": 4.9,
                        "step": 0.1,
                        "mode": "box",
                        "unit_of_measurement": "°C",
                    }
                }
            ),
            vol.Required(
                CONF_MIN_SEND_INTERVAL, default=default_send_interval
            ): selector.selector(
                {
                    "number": {
                        "min": 1,
                        "max": 300,
                        "step": 1,
                        "mode": "box",
                        "unit_of_measurement": "s",
                    }
                }
            ),
        }
    )


def _normalize_rounding_direction(value: Any) -> str:
    """Return a safe rounding direction value for storage/use."""
    if value in ROUNDING_DIRECTIONS:
        return value
    return DEFAULT_ROUNDING_DIRECTION


def _normalize_destination_target(value: Any) -> str:
    """Return a safe destination target attribute."""
    if value in DESTINATION_TARGETS:
        return value
    return DEFAULT_DESTINATION_TARGET


def _destination_supports_target(state: Any, target: str) -> bool:
    """Return whether a destination currently exposes the selected target."""
    if state is None or state.state in ("unavailable", "unknown"):
        return False

    attributes = state.attributes
    if target == DESTINATION_TARGET_LOW:
        required = (DESTINATION_TARGET_LOW, DESTINATION_TARGET_HIGH)
    else:
        required = (DESTINATION_TARGET_TEMPERATURE,)

    for attribute in required:
        value = attributes.get(attribute)
        if value is None or str(value).lower() in (
            "unknown",
            "unavailable",
            "none",
            "",
        ):
            return False
        try:
            float(value)
        except (TypeError, ValueError):
            return False
    return True


# ──────────────────────────────────────────────────────────────────────────────
# Initial config flow (4 steps)
# ──────────────────────────────────────────────────────────────────────────────


class ClimateSyncConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for ClimateSync."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialise config flow."""
        self._source_entities: list[str] = []
        self._primary_source_entities: list[str] = []
        self._destination_settings: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 1: select source climate entities."""
        errors: dict[str, str] = {}

        if user_input is not None:
            sources = user_input.get(CONF_SOURCE_ENTITIES, [])
            if not sources:
                errors[CONF_SOURCE_ENTITIES] = "no_sources"
            else:
                self._source_entities = list(sources)
                return await self.async_step_primary_sources()

        return self.async_show_form(
            step_id="user",
            data_schema=_sources_schema(),
            errors=errors,
            last_step=False,
        )

    async def async_step_primary_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2: select the subset allowed to start piggyback demand."""
        errors: dict[str, str] = {}

        if user_input is not None:
            primary_sources = list(user_input.get(CONF_PRIMARY_SOURCE_ENTITIES, []))
            if not primary_sources:
                errors[CONF_PRIMARY_SOURCE_ENTITIES] = "no_primary_sources"
            elif any(source not in self._source_entities for source in primary_sources):
                errors[CONF_PRIMARY_SOURCE_ENTITIES] = "primary_not_source"
            else:
                self._primary_source_entities = primary_sources
                return await self.async_step_destination()

        return self.async_show_form(
            step_id="primary_sources",
            data_schema=_primary_sources_schema(
                self._source_entities, self._source_entities
            ),
            errors=errors,
            last_step=False,
        )

    async def async_step_destination(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 3: select destination entity and setpoint behaviour."""
        errors: dict[str, str] = {}

        if user_input is not None:
            dest = user_input.get(CONF_DESTINATION_ENTITY)
            destination_target = _normalize_destination_target(
                user_input.get(CONF_DESTINATION_TARGET)
            )
            if dest in self._source_entities:
                errors[CONF_DESTINATION_ENTITY] = "dest_is_source"
            elif not dest:
                errors[CONF_DESTINATION_ENTITY] = "no_destination"
            elif not _destination_supports_target(
                self.hass.states.get(dest), destination_target
            ):
                errors[CONF_DESTINATION_TARGET] = "destination_target_unsupported"
            else:
                self._destination_settings = {
                    CONF_DESTINATION_ENTITY: dest,
                    CONF_DESTINATION_TARGET: destination_target,
                    CONF_IDLE_TEMPERATURE: user_input[CONF_IDLE_TEMPERATURE],
                    CONF_MAX_SETPOINT: user_input[CONF_MAX_SETPOINT],
                    CONF_ROUNDING_MODE: user_input[CONF_ROUNDING_MODE],
                    CONF_ROUNDING_DIRECTION: _normalize_rounding_direction(
                        user_input.get(CONF_ROUNDING_DIRECTION)
                    ),
                }
                return await self.async_step_control()

        return self.async_show_form(
            step_id="destination",
            data_schema=_destination_schema(),
            errors=errors,
            last_step=False,
        )

    async def async_step_control(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 4: configure demand hysteresis and update safeguards."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if (
                user_input[CONF_DEMAND_DEACTIVATION_THRESHOLD]
                >= user_input[CONF_DEMAND_ACTIVATION_THRESHOLD]
            ):
                errors[CONF_DEMAND_DEACTIVATION_THRESHOLD] = "demand_threshold_order"
            else:
                return self.async_create_entry(
                    title="ClimateSync",
                    data={
                        CONF_SOURCE_ENTITIES: self._source_entities,
                        CONF_PRIMARY_SOURCE_ENTITIES: self._primary_source_entities,
                        **self._destination_settings,
                        CONF_RESYNC_INTERVAL: user_input[CONF_RESYNC_INTERVAL],
                        CONF_MIN_CHANGE_THRESHOLD: user_input[
                            CONF_MIN_CHANGE_THRESHOLD
                        ],
                        CONF_DEMAND_ACTIVATION_THRESHOLD: user_input[
                            CONF_DEMAND_ACTIVATION_THRESHOLD
                        ],
                        CONF_DEMAND_DEACTIVATION_THRESHOLD: user_input[
                            CONF_DEMAND_DEACTIVATION_THRESHOLD
                        ],
                        CONF_MIN_SEND_INTERVAL: user_input[CONF_MIN_SEND_INTERVAL],
                    },
                )

        return self.async_show_form(
            step_id="control",
            data_schema=_control_schema(),
            errors=errors,
            last_step=True,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ClimateSyncOptionsFlow:
        """Return the options flow."""
        return ClimateSyncOptionsFlow(config_entry)


# ──────────────────────────────────────────────────────────────────────────────
# Options flow – same 4-step wizard as initial setup
# ──────────────────────────────────────────────────────────────────────────────


class ClimateSyncOptionsFlow(config_entries.OptionsFlow):
    """Options flow: mirrors the 4-step setup wizard."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialise options flow."""
        self._config_entry = config_entry
        self._source_entities: list[str] = []
        self._primary_source_entities: list[str] = []
        self._destination_settings: dict[str, Any] = {}

    def _get(self, key: str, default: Any) -> Any:
        """Return value from options, falling back to data, then to default."""
        return self._config_entry.options.get(
            key, self._config_entry.data.get(key, default)
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 1 (re-configure): select source climate entities."""
        errors: dict[str, str] = {}

        if user_input is not None:
            sources = user_input.get(CONF_SOURCE_ENTITIES, [])
            if not sources:
                errors[CONF_SOURCE_ENTITIES] = "no_sources"
            else:
                self._source_entities = list(sources)
                return await self.async_step_primary_sources()

        current_sources = self._get(CONF_SOURCE_ENTITIES, [])
        return self.async_show_form(
            step_id="init",
            data_schema=_sources_schema(default_sources=current_sources),
            errors=errors,
            last_step=False,
        )

    async def async_step_primary_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2 (re-configure): select primary source subset."""
        errors: dict[str, str] = {}

        if user_input is not None:
            primary_sources = list(user_input.get(CONF_PRIMARY_SOURCE_ENTITIES, []))
            if not primary_sources:
                errors[CONF_PRIMARY_SOURCE_ENTITIES] = "no_primary_sources"
            elif any(source not in self._source_entities for source in primary_sources):
                errors[CONF_PRIMARY_SOURCE_ENTITIES] = "primary_not_source"
            else:
                self._primary_source_entities = primary_sources
                return await self.async_step_destination()

        configured_primary = list(
            self._get(CONF_PRIMARY_SOURCE_ENTITIES, self._source_entities)
        )
        current_primary = [
            source for source in configured_primary if source in self._source_entities
        ]
        if not current_primary:
            current_primary = list(self._source_entities)

        return self.async_show_form(
            step_id="primary_sources",
            data_schema=_primary_sources_schema(self._source_entities, current_primary),
            errors=errors,
            last_step=False,
        )

    async def async_step_destination(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 3 (re-configure): destination and setpoint behaviour."""
        errors: dict[str, str] = {}

        if user_input is not None:
            dest = user_input.get(CONF_DESTINATION_ENTITY)
            destination_target = _normalize_destination_target(
                user_input.get(CONF_DESTINATION_TARGET)
            )
            if dest in self._source_entities:
                errors[CONF_DESTINATION_ENTITY] = "dest_is_source"
            elif not dest:
                errors[CONF_DESTINATION_ENTITY] = "no_destination"
            elif not _destination_supports_target(
                self.hass.states.get(dest), destination_target
            ):
                errors[CONF_DESTINATION_TARGET] = "destination_target_unsupported"
            else:
                self._destination_settings = {
                    CONF_DESTINATION_ENTITY: dest,
                    CONF_DESTINATION_TARGET: destination_target,
                    CONF_IDLE_TEMPERATURE: user_input[CONF_IDLE_TEMPERATURE],
                    CONF_MAX_SETPOINT: user_input[CONF_MAX_SETPOINT],
                    CONF_ROUNDING_MODE: user_input[CONF_ROUNDING_MODE],
                    CONF_ROUNDING_DIRECTION: _normalize_rounding_direction(
                        user_input.get(CONF_ROUNDING_DIRECTION)
                    ),
                }
                return await self.async_step_control()

        return self.async_show_form(
            step_id="destination",
            data_schema=_destination_schema(
                default_dest=self._get(CONF_DESTINATION_ENTITY, None),
                default_destination_target=_normalize_destination_target(
                    self._get(CONF_DESTINATION_TARGET, DEFAULT_DESTINATION_TARGET)
                ),
                default_idle=self._get(CONF_IDLE_TEMPERATURE, DEFAULT_IDLE_TEMPERATURE),
                default_max_setpoint=self._get(CONF_MAX_SETPOINT, DEFAULT_MAX_SETPOINT),
                default_rounding=self._get(CONF_ROUNDING_MODE, DEFAULT_ROUNDING_MODE),
                default_rounding_direction=_normalize_rounding_direction(
                    self._get(CONF_ROUNDING_DIRECTION, DEFAULT_ROUNDING_DIRECTION)
                ),
            ),
            errors=errors,
            last_step=False,
        )

    async def async_step_control(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 4 (re-configure): control behaviour and safeguards."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if (
                user_input[CONF_DEMAND_DEACTIVATION_THRESHOLD]
                >= user_input[CONF_DEMAND_ACTIVATION_THRESHOLD]
            ):
                errors[CONF_DEMAND_DEACTIVATION_THRESHOLD] = "demand_threshold_order"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_SOURCE_ENTITIES: self._source_entities,
                        CONF_PRIMARY_SOURCE_ENTITIES: self._primary_source_entities,
                        **self._destination_settings,
                        CONF_RESYNC_INTERVAL: user_input[CONF_RESYNC_INTERVAL],
                        CONF_MIN_CHANGE_THRESHOLD: user_input[
                            CONF_MIN_CHANGE_THRESHOLD
                        ],
                        CONF_DEMAND_ACTIVATION_THRESHOLD: user_input[
                            CONF_DEMAND_ACTIVATION_THRESHOLD
                        ],
                        CONF_DEMAND_DEACTIVATION_THRESHOLD: user_input[
                            CONF_DEMAND_DEACTIVATION_THRESHOLD
                        ],
                        CONF_MIN_SEND_INTERVAL: user_input[CONF_MIN_SEND_INTERVAL],
                    },
                )

        return self.async_show_form(
            step_id="control",
            data_schema=_control_schema(
                default_resync=self._get(CONF_RESYNC_INTERVAL, DEFAULT_RESYNC_INTERVAL),
                default_threshold=self._get(
                    CONF_MIN_CHANGE_THRESHOLD, DEFAULT_MIN_CHANGE_THRESHOLD
                ),
                default_send_interval=self._get(
                    CONF_MIN_SEND_INTERVAL, DEFAULT_MIN_SEND_INTERVAL
                ),
                default_demand_activation=self._get(
                    CONF_DEMAND_ACTIVATION_THRESHOLD,
                    DEFAULT_DEMAND_ACTIVATION_THRESHOLD,
                ),
                default_demand_deactivation=self._get(
                    CONF_DEMAND_DEACTIVATION_THRESHOLD,
                    DEFAULT_DEMAND_DEACTIVATION_THRESHOLD,
                ),
            ),
            errors=errors,
            last_step=True,
        )
