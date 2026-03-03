"""Config flow for ClimateSync."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, selector

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
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SOURCE_ENTITIES): selector.selector(
                        {
                            "entity": {
                                "domain": CLIMATE_DOMAIN,
                                "multiple": True,
                            }
                        }
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_destination(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2: select destination entity + basic options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            dest = user_input.get(CONF_DESTINATION_ENTITY)
            if dest in self._source_entities:
                errors[CONF_DESTINATION_ENTITY] = "dest_is_source"
            elif not dest:
                errors[CONF_DESTINATION_ENTITY] = "no_destination"
            else:
                data = {
                    CONF_SOURCE_ENTITIES: self._source_entities,
                    CONF_DESTINATION_ENTITY: dest,
                    CONF_IDLE_TEMPERATURE: user_input[CONF_IDLE_TEMPERATURE],
                    CONF_ROUNDING_MODE: user_input[CONF_ROUNDING_MODE],
                }
                return self.async_create_entry(title="ClimateSync", data=data)

        return self.async_show_form(
            step_id="destination",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DESTINATION_ENTITY): selector.selector(
                        {
                            "entity": {
                                "domain": CLIMATE_DOMAIN,
                            }
                        }
                    ),
                    vol.Required(
                        CONF_IDLE_TEMPERATURE,
                        default=DEFAULT_IDLE_TEMPERATURE,
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
                        CONF_ROUNDING_MODE,
                        default=DEFAULT_ROUNDING_MODE,
                    ): selector.selector(
                        {
                            "select": {
                                "options": [
                                    {
                                        "value": mode,
                                        "label": mode,
                                    }
                                    for mode in ROUNDING_MODES
                                ],
                                "translation_key": "rounding_mode",
                            }
                        }
                    ),
                }
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


class ClimateSyncOptionsFlow(config_entries.OptionsFlow):
    """Options flow for ClimateSync."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialise options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_options = self._config_entry.options
        current_data = self._config_entry.data

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_IDLE_TEMPERATURE,
                        default=current_options.get(
                            CONF_IDLE_TEMPERATURE,
                            current_data.get(
                                CONF_IDLE_TEMPERATURE, DEFAULT_IDLE_TEMPERATURE
                            ),
                        ),
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
                        CONF_ROUNDING_MODE,
                        default=current_options.get(
                            CONF_ROUNDING_MODE,
                            current_data.get(CONF_ROUNDING_MODE, DEFAULT_ROUNDING_MODE),
                        ),
                    ): selector.selector(
                        {
                            "select": {
                                "options": [
                                    {
                                        "value": mode,
                                        "label": mode,
                                    }
                                    for mode in ROUNDING_MODES
                                ],
                                "translation_key": "rounding_mode",
                            }
                        }
                    ),
                    vol.Required(
                        CONF_RESYNC_INTERVAL,
                        default=current_options.get(
                            CONF_RESYNC_INTERVAL, DEFAULT_RESYNC_INTERVAL
                        ),
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
                        CONF_MIN_CHANGE_THRESHOLD,
                        default=current_options.get(
                            CONF_MIN_CHANGE_THRESHOLD, DEFAULT_MIN_CHANGE_THRESHOLD
                        ),
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
                        CONF_MIN_SEND_INTERVAL,
                        default=current_options.get(
                            CONF_MIN_SEND_INTERVAL, DEFAULT_MIN_SEND_INTERVAL
                        ),
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
            ),
        )
