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
    DOMAIN,
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


def _destination_schema(
    default_dest: str | None = None,
    default_idle: float = DEFAULT_IDLE_TEMPERATURE,
    default_rounding: str = DEFAULT_ROUNDING_MODE,
    default_resync: int = DEFAULT_RESYNC_INTERVAL,
    default_threshold: float = DEFAULT_MIN_CHANGE_THRESHOLD,
    default_send_interval: int = DEFAULT_MIN_SEND_INTERVAL,
    include_advanced: bool = False,
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
        vol.Required(CONF_ROUNDING_MODE, default=default_rounding): selector.selector(
            {
                "select": {
                    "options": [{"value": m, "label": m} for m in ROUNDING_MODES],
                    "translation_key": "rounding_mode",
                }
            }
        ),
    }

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

    return vol.Schema(fields)


# ──────────────────────────────────────────────────────────────────────────────
# Initial config flow (2 steps, no advanced options)
# ──────────────────────────────────────────────────────────────────────────────

class ClimateSyncConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for ClimateSync."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise config flow."""
        self._source_entities: list[str] = []

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
                return await self.async_step_destination()

        return self.async_show_form(
            step_id="user",
            data_schema=_sources_schema(),
            errors=errors,
        )

    async def async_step_destination(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2: select destination entity + basic settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            dest = user_input.get(CONF_DESTINATION_ENTITY)
            if dest in self._source_entities:
                errors[CONF_DESTINATION_ENTITY] = "dest_is_source"
            elif not dest:
                errors[CONF_DESTINATION_ENTITY] = "no_destination"
            else:
                return self.async_create_entry(
                    title="ClimateSync",
                    data={
                        CONF_SOURCE_ENTITIES: self._source_entities,
                        CONF_DESTINATION_ENTITY: dest,
                        CONF_IDLE_TEMPERATURE: user_input[CONF_IDLE_TEMPERATURE],
                        CONF_ROUNDING_MODE: user_input[CONF_ROUNDING_MODE],
                    },
                )

        return self.async_show_form(
            step_id="destination",
            data_schema=_destination_schema(),
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
# Options flow – same wizard as initial setup, all settings editable
# ──────────────────────────────────────────────────────────────────────────────

class ClimateSyncOptionsFlow(config_entries.OptionsFlow):
    """Options flow: mirrors the 2-step setup wizard."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialise options flow."""
        self._config_entry = config_entry
        self._source_entities: list[str] = []

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
                return await self.async_step_destination()

        current_sources = self._get(CONF_SOURCE_ENTITIES, [])
        return self.async_show_form(
            step_id="init",
            data_schema=_sources_schema(default_sources=current_sources),
            errors=errors,
        )

    async def async_step_destination(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2 (re-configure): destination + all settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            dest = user_input.get(CONF_DESTINATION_ENTITY)
            if dest in self._source_entities:
                errors[CONF_DESTINATION_ENTITY] = "dest_is_source"
            elif not dest:
                errors[CONF_DESTINATION_ENTITY] = "no_destination"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_SOURCE_ENTITIES: self._source_entities,
                        CONF_DESTINATION_ENTITY: dest,
                        CONF_IDLE_TEMPERATURE: user_input[CONF_IDLE_TEMPERATURE],
                        CONF_ROUNDING_MODE: user_input[CONF_ROUNDING_MODE],
                        CONF_RESYNC_INTERVAL: user_input[CONF_RESYNC_INTERVAL],
                        CONF_MIN_CHANGE_THRESHOLD: user_input[CONF_MIN_CHANGE_THRESHOLD],
                        CONF_MIN_SEND_INTERVAL: user_input[CONF_MIN_SEND_INTERVAL],
                    },
                )

        return self.async_show_form(
            step_id="destination",
            data_schema=_destination_schema(
                default_dest=self._get(CONF_DESTINATION_ENTITY, None),
                default_idle=self._get(CONF_IDLE_TEMPERATURE, DEFAULT_IDLE_TEMPERATURE),
                default_rounding=self._get(CONF_ROUNDING_MODE, DEFAULT_ROUNDING_MODE),
                default_resync=self._get(CONF_RESYNC_INTERVAL, DEFAULT_RESYNC_INTERVAL),
                default_threshold=self._get(
                    CONF_MIN_CHANGE_THRESHOLD, DEFAULT_MIN_CHANGE_THRESHOLD
                ),
                default_send_interval=self._get(
                    CONF_MIN_SEND_INTERVAL, DEFAULT_MIN_SEND_INTERVAL
                ),
                include_advanced=True,
            ),
            errors=errors,
        )

