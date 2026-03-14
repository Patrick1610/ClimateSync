"""Config flow for ClimateSync."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DESTINATION_ENTITY,
    CONF_IDLE_TEMPERATURE,
    CONF_LEADER_STICK_SECONDS,
    CONF_LEADER_SWITCH_THRESHOLD,
    CONF_MAX_SETPOINT,
    CONF_MIN_CHANGE_THRESHOLD,
    CONF_MIN_SEND_INTERVAL,
    CONF_MODE,
    CONF_OFFSET_ENTITY,
    CONF_OFFSET_MIN_CHANGE,
    CONF_OFFSET_MIN_INTERVAL_SECONDS,
    CONF_OFFSET_SETTLE_SECONDS,
    CONF_RESYNC_INTERVAL,
    CONF_ROUNDING_MODE,
    CONF_SOURCE_ENTITIES,
    DEFAULT_IDLE_TEMPERATURE,
    DEFAULT_LEADER_STICK_SECONDS,
    DEFAULT_LEADER_SWITCH_THRESHOLD,
    DEFAULT_MAX_SETPOINT,
    DEFAULT_MIN_CHANGE_THRESHOLD,
    DEFAULT_MIN_SEND_INTERVAL,
    DEFAULT_MODE,
    DEFAULT_OFFSET_MIN_CHANGE,
    DEFAULT_OFFSET_MIN_INTERVAL_SECONDS,
    DEFAULT_OFFSET_SETTLE_SECONDS,
    DEFAULT_RESYNC_INTERVAL,
    DEFAULT_ROUNDING_MODE,
    DOMAIN,
    MODE_DELTA,
    MODE_OFFSET,
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


def _mode_schema(default_mode: str = DEFAULT_MODE) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_MODE, default=default_mode): selector.selector(
                {
                    "select": {
                        "options": [
                            {"value": MODE_DELTA, "label": MODE_DELTA},
                            {"value": MODE_OFFSET, "label": MODE_OFFSET},
                        ],
                        "translation_key": "mode",
                    }
                }
            ),
        }
    )


def _destination_schema(
    default_dest: str | None = None,
    default_idle: float = DEFAULT_IDLE_TEMPERATURE,
    default_max_setpoint: float = DEFAULT_MAX_SETPOINT,
    default_rounding: str = DEFAULT_ROUNDING_MODE,
    default_resync: int = DEFAULT_RESYNC_INTERVAL,
    default_threshold: float = DEFAULT_MIN_CHANGE_THRESHOLD,
    default_send_interval: int = DEFAULT_MIN_SEND_INTERVAL,
    include_advanced: bool = False,
    include_offset_entity: bool = False,
    default_offset_entity: str | None = None,
    default_offset_settle_seconds: int = DEFAULT_OFFSET_SETTLE_SECONDS,
    default_offset_min_change: float = DEFAULT_OFFSET_MIN_CHANGE,
    default_offset_min_interval: int = DEFAULT_OFFSET_MIN_INTERVAL_SECONDS,
    default_leader_switch_threshold: float = DEFAULT_LEADER_SWITCH_THRESHOLD,
    default_leader_stick_seconds: int = DEFAULT_LEADER_STICK_SECONDS,
) -> vol.Schema:
    fields: dict = {
        vol.Required(CONF_DESTINATION_ENTITY, default=default_dest): selector.selector(
            {
                "entity": {
                    "domain": CLIMATE_DOMAIN,
                }
            }
        ),
        vol.Required(CONF_IDLE_TEMPERATURE, default=default_idle): selector.selector(
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
        vol.Required(CONF_MAX_SETPOINT, default=default_max_setpoint): selector.selector(
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
        vol.Required(CONF_ROUNDING_MODE, default=default_rounding): selector.selector(
            {
                "select": {
                    "options": [{"value": m, "label": m} for m in ROUNDING_MODES],
                    "translation_key": "rounding_mode",
                }
            }
        ),
    }

    if include_offset_entity:
        fields[vol.Required(CONF_OFFSET_ENTITY, default=default_offset_entity)] = (
            selector.selector(
                {
                    "entity": {
                        "domain": ["number", "input_number"],
                    }
                }
            )
        )

    if include_advanced:
        fields[vol.Required(CONF_RESYNC_INTERVAL, default=default_resync)] = (
            selector.selector(
                {
                    "number": {
                        "min": 10,
                        "max": 3600,
                        "step": 1,
                        "mode": "box",
                        "unit_of_measurement": "s",
                    }
                }
            )
        )
        fields[vol.Required(CONF_MIN_CHANGE_THRESHOLD, default=default_threshold)] = (
            selector.selector(
                {
                    "number": {
                        "min": 0.0,
                        "max": 5.0,
                        "step": 0.1,
                        "mode": "box",
                        "unit_of_measurement": "°C",
                    }
                }
            )
        )
        fields[vol.Required(CONF_MIN_SEND_INTERVAL, default=default_send_interval)] = (
            selector.selector(
                {
                    "number": {
                        "min": 1,
                        "max": 300,
                        "step": 1,
                        "mode": "box",
                        "unit_of_measurement": "s",
                    }
                }
            )
        )
        fields[vol.Required(CONF_OFFSET_SETTLE_SECONDS, default=default_offset_settle_seconds)] = (
            selector.selector(
                {
                    "number": {
                        "min": 0,
                        "max": 30,
                        "step": 1,
                        "mode": "box",
                        "unit_of_measurement": "s",
                    }
                }
            )
        )
        fields[vol.Required(CONF_OFFSET_MIN_CHANGE, default=default_offset_min_change)] = (
            selector.selector(
                {
                    "number": {
                        "min": 0.0,
                        "max": 2.0,
                        "step": 0.05,
                        "mode": "box",
                        "unit_of_measurement": "°C",
                    }
                }
            )
        )
        fields[vol.Required(CONF_OFFSET_MIN_INTERVAL_SECONDS, default=default_offset_min_interval)] = (
            selector.selector(
                {
                    "number": {
                        "min": 0,
                        "max": 120,
                        "step": 1,
                        "mode": "box",
                        "unit_of_measurement": "s",
                    }
                }
            )
        )
        fields[vol.Required(CONF_LEADER_SWITCH_THRESHOLD, default=default_leader_switch_threshold)] = (
            selector.selector(
                {
                    "number": {
                        "min": 0.0,
                        "max": 5.0,
                        "step": 0.1,
                        "mode": "box",
                        "unit_of_measurement": "°C",
                    }
                }
            )
        )
        fields[vol.Required(CONF_LEADER_STICK_SECONDS, default=default_leader_stick_seconds)] = (
            selector.selector(
                {
                    "number": {
                        "min": 0,
                        "max": 300,
                        "step": 1,
                        "mode": "box",
                        "unit_of_measurement": "s",
                    }
                }
            )
        )

    return vol.Schema(fields)


# ──────────────────────────────────────────────────────────────────────────────
# Initial config flow (3 steps: sources → mode → destination)
# ──────────────────────────────────────────────────────────────────────────────

class ClimateSyncConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for ClimateSync."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise config flow."""
        self._source_entities: list[str] = []
        self._mode: str = DEFAULT_MODE

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
                self._source_entities = sources
                return await self.async_step_select_mode()

        return self.async_show_form(
            step_id="user",
            data_schema=_sources_schema(),
            errors=errors,
        )

    async def async_step_select_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2: choose operating mode."""
        if user_input is not None:
            self._mode = user_input.get(CONF_MODE, DEFAULT_MODE)
            return await self.async_step_destination()

        return self.async_show_form(
            step_id="select_mode",
            data_schema=_mode_schema(),
            errors={},
        )

    async def async_step_destination(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 3: select destination entity + basic settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            dest = user_input.get(CONF_DESTINATION_ENTITY)
            if dest in self._source_entities:
                errors[CONF_DESTINATION_ENTITY] = "dest_is_source"
            elif not dest:
                errors[CONF_DESTINATION_ENTITY] = "no_destination"
            elif self._mode == MODE_OFFSET and not user_input.get(CONF_OFFSET_ENTITY):
                errors[CONF_OFFSET_ENTITY] = "no_offset_entity"
            else:
                data: dict[str, Any] = {
                    CONF_SOURCE_ENTITIES: self._source_entities,
                    CONF_DESTINATION_ENTITY: dest,
                    CONF_IDLE_TEMPERATURE: user_input[CONF_IDLE_TEMPERATURE],
                    CONF_MAX_SETPOINT: user_input[CONF_MAX_SETPOINT],
                    CONF_ROUNDING_MODE: user_input[CONF_ROUNDING_MODE],
                    CONF_MODE: self._mode,
                }
                if self._mode == MODE_OFFSET:
                    data[CONF_OFFSET_ENTITY] = user_input[CONF_OFFSET_ENTITY]
                return self.async_create_entry(title="ClimateSync", data=data)

        return self.async_show_form(
            step_id="destination",
            data_schema=_destination_schema(
                include_offset_entity=(self._mode == MODE_OFFSET),
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ClimateSyncOptionsFlow:
        """Return the options flow."""
        return ClimateSyncOptionsFlow(config_entry)


# ──────────────────────────────────────────────────────────────────────────────
# Options flow – same 3-step wizard as initial setup, all settings editable
# ──────────────────────────────────────────────────────────────────────────────

class ClimateSyncOptionsFlow(config_entries.OptionsFlow):
    """Options flow: mirrors the 3-step setup wizard."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialise options flow."""
        self._config_entry = config_entry
        self._source_entities: list[str] = []
        self._mode: str = DEFAULT_MODE

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
                self._source_entities = sources
                return await self.async_step_select_mode()

        current_sources = self._get(CONF_SOURCE_ENTITIES, [])
        return self.async_show_form(
            step_id="init",
            data_schema=_sources_schema(default_sources=current_sources),
            errors=errors,
        )

    async def async_step_select_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2 (re-configure): choose operating mode."""
        if user_input is not None:
            self._mode = user_input.get(CONF_MODE, DEFAULT_MODE)
            return await self.async_step_destination()

        current_mode = self._get(CONF_MODE, DEFAULT_MODE)
        return self.async_show_form(
            step_id="select_mode",
            data_schema=_mode_schema(default_mode=current_mode),
            errors={},
        )

    async def async_step_destination(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 3 (re-configure): destination + all settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            dest = user_input.get(CONF_DESTINATION_ENTITY)
            if dest in self._source_entities:
                errors[CONF_DESTINATION_ENTITY] = "dest_is_source"
            elif not dest:
                errors[CONF_DESTINATION_ENTITY] = "no_destination"
            elif self._mode == MODE_OFFSET and not user_input.get(CONF_OFFSET_ENTITY):
                errors[CONF_OFFSET_ENTITY] = "no_offset_entity"
            else:
                data: dict[str, Any] = {
                    CONF_SOURCE_ENTITIES: self._source_entities,
                    CONF_DESTINATION_ENTITY: dest,
                    CONF_IDLE_TEMPERATURE: user_input[CONF_IDLE_TEMPERATURE],
                    CONF_MAX_SETPOINT: user_input[CONF_MAX_SETPOINT],
                    CONF_ROUNDING_MODE: user_input[CONF_ROUNDING_MODE],
                    CONF_RESYNC_INTERVAL: user_input[CONF_RESYNC_INTERVAL],
                    CONF_MIN_CHANGE_THRESHOLD: user_input[CONF_MIN_CHANGE_THRESHOLD],
                    CONF_MIN_SEND_INTERVAL: user_input[CONF_MIN_SEND_INTERVAL],
                    CONF_OFFSET_SETTLE_SECONDS: user_input[CONF_OFFSET_SETTLE_SECONDS],
                    CONF_OFFSET_MIN_CHANGE: user_input[CONF_OFFSET_MIN_CHANGE],
                    CONF_OFFSET_MIN_INTERVAL_SECONDS: user_input[CONF_OFFSET_MIN_INTERVAL_SECONDS],
                    CONF_LEADER_SWITCH_THRESHOLD: user_input[CONF_LEADER_SWITCH_THRESHOLD],
                    CONF_LEADER_STICK_SECONDS: user_input[CONF_LEADER_STICK_SECONDS],
                    CONF_MODE: self._mode,
                }
                if self._mode == MODE_OFFSET:
                    data[CONF_OFFSET_ENTITY] = user_input[CONF_OFFSET_ENTITY]
                return self.async_create_entry(title="", data=data)

        return self.async_show_form(
            step_id="destination",
            data_schema=_destination_schema(
                default_dest=self._get(CONF_DESTINATION_ENTITY, None),
                default_idle=self._get(CONF_IDLE_TEMPERATURE, DEFAULT_IDLE_TEMPERATURE),
                default_max_setpoint=self._get(CONF_MAX_SETPOINT, DEFAULT_MAX_SETPOINT),
                default_rounding=self._get(CONF_ROUNDING_MODE, DEFAULT_ROUNDING_MODE),
                default_resync=self._get(CONF_RESYNC_INTERVAL, DEFAULT_RESYNC_INTERVAL),
                default_threshold=self._get(
                    CONF_MIN_CHANGE_THRESHOLD, DEFAULT_MIN_CHANGE_THRESHOLD
                ),
                default_send_interval=self._get(
                    CONF_MIN_SEND_INTERVAL, DEFAULT_MIN_SEND_INTERVAL
                ),
                default_offset_settle_seconds=self._get(
                    CONF_OFFSET_SETTLE_SECONDS, DEFAULT_OFFSET_SETTLE_SECONDS
                ),
                default_offset_min_change=self._get(
                    CONF_OFFSET_MIN_CHANGE, DEFAULT_OFFSET_MIN_CHANGE
                ),
                default_offset_min_interval=self._get(
                    CONF_OFFSET_MIN_INTERVAL_SECONDS, DEFAULT_OFFSET_MIN_INTERVAL_SECONDS
                ),
                default_leader_switch_threshold=self._get(
                    CONF_LEADER_SWITCH_THRESHOLD, DEFAULT_LEADER_SWITCH_THRESHOLD
                ),
                default_leader_stick_seconds=self._get(
                    CONF_LEADER_STICK_SECONDS, DEFAULT_LEADER_STICK_SECONDS
                ),
                include_advanced=True,
                include_offset_entity=(self._mode == MODE_OFFSET),
                default_offset_entity=self._get(CONF_OFFSET_ENTITY, None),
            ),
            errors=errors,
        )


