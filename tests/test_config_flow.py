"""Unit-test the wizard with isolated Home Assistant flow/selector stubs."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


class _Flow:
    def __init_subclass__(cls, **kwargs):
        pass

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}


class _Selector:
    def __init__(self, config):
        self.config = config

    def __call__(self, value):
        return value


@pytest.fixture(scope="module")
def flow_module():
    """Do not leak flow stubs into the existing coordinator/sensor tests."""
    ha = ModuleType("homeassistant")
    entries = ModuleType("homeassistant.config_entries")
    entries.ConfigFlow = entries.OptionsFlow = _Flow
    entries.ConfigEntry = SimpleNamespace
    ha.config_entries = entries
    core = ModuleType("homeassistant.core")
    core.callback = lambda fn: fn
    components = ModuleType("homeassistant.components")
    climate = ModuleType("homeassistant.components.climate")
    climate.DOMAIN = "climate"
    helpers = ModuleType("homeassistant.helpers")
    selector = ModuleType("homeassistant.helpers.selector")
    selector.selector = _Selector
    helpers.selector = selector
    modules = {
        "homeassistant": ha,
        "homeassistant.config_entries": entries,
        "homeassistant.core": core,
        "homeassistant.components": components,
        "homeassistant.components.climate": climate,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.selector": selector,
    }
    source = Path(__file__).parents[1] / "custom_components/climatesync/config_flow.py"
    spec = importlib.util.spec_from_file_location(
        "custom_components.climatesync._flow_under_test", source
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict("sys.modules", modules):
        spec.loader.exec_module(module)
    return module


def _flow(module, options=False, data=None):
    if options:
        flow = module.ClimateSyncOptionsFlow(
            SimpleNamespace(data=data or {}, options={})
        )
    else:
        flow = module.ClimateSyncConfigFlow()
    flow.hass = MagicMock()
    flow.hass.states.get.return_value = SimpleNamespace(
        state="heat",
        attributes={"temperature": 20.0, "min_temp": 5.0, "max_temp": 35.0},
    )
    return flow


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [False, True])
@pytest.mark.parametrize(
    "climates, deltas",
    [
        (["climate.room"], []),
        ([], ["sensor.pi_delta"]),
        (["climate.room"], ["sensor.pi_delta"]),
    ],
)
async def test_setup_and_options_roundtrip_both_source_types(
    flow_module, options, climates, deltas
):
    flow = _flow(flow_module, options)
    first_step = flow.async_step_init if options else flow.async_step_user
    result = await first_step(
        {
            "source_entities": climates,
            "delta_source_entities": deltas,
        }
    )
    assert result["step_id"] == "primary_sources"
    assert result["last_step"] is False
    schema = result["data_schema"].schema
    primary_selector = next(iter(schema.values()))
    assert [
        s["value"] for s in primary_selector.config["select"]["options"]
    ] == climates + deltas
    primary = (deltas or climates)[:1]
    result = await flow.async_step_primary_sources({"primary_source_entities": primary})
    assert result["step_id"] == "destination"
    result = await flow.async_step_destination(
        {
            "destination_entity": "climate.emma",
            "idle_temperature": 5,
            "max_setpoint": 35,
            "rounding_mode": "1_decimal",
            "rounding_direction": "nearest",
        }
    )
    assert result["step_id"] == "setback_target"
    result = await flow.async_step_setback_target({"idle_value": 5})
    assert result["step_id"] == "control"
    assert result["last_step"] is True
    result = await flow.async_step_control(
        {
            "resync_interval_seconds": 60,
            "min_change_threshold": 0.2,
            "min_send_interval_seconds": 10,
            "demand_activation_threshold": 0.1,
            "demand_deactivation_threshold": 0.0,
        }
    )
    assert result["type"] == "create_entry"
    assert result["data"]["source_entities"] == climates
    assert result["data"]["delta_source_entities"] == deltas
    assert result["data"]["primary_source_entities"] == primary
    assert result["data"]["demand_activation_threshold"] == 0.1
    assert result["data"]["rounding_direction"] == "nearest"


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [False, True])
async def test_at_least_one_source_across_fields_and_primary_subset(
    flow_module, options
):
    flow = _flow(flow_module, options)
    first_step = flow.async_step_init if options else flow.async_step_user
    result = await first_step({})
    assert result["errors"] == {"source_entities": "no_sources"}
    await first_step({"delta_source_entities": ["sensor.pi_delta"]})
    result = await flow.async_step_primary_sources(
        {"primary_source_entities": ["sensor.other"]}
    )
    assert result["errors"] == {"primary_source_entities": "primary_not_source"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "climates, deltas",
    [
        (["climate.room"], []),
        (["climate.room"], ["sensor.pi_delta"]),
    ],
)
async def test_options_defaults_preserve_both_lists_without_mutating_entry(
    flow_module, climates, deltas
):
    data = {"source_entities": climates, "primary_source_entities": climates}
    if deltas:
        data["delta_source_entities"] = deltas
    before = dict(data)
    flow = _flow(flow_module, options=True, data=data)
    result = await flow.async_step_init()
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults == {"source_entities": climates, "delta_source_entities": deltas}
    assert data == before
    await flow.async_step_init(defaults)
    primary = await flow.async_step_primary_sources()
    key = next(iter(primary["data_schema"].schema))
    assert key.default() == climates


def test_source_selectors_have_separate_domains(flow_module):
    schema = flow_module._sources_schema().schema
    domains = {
        str(key): value.config["entity"]["domain"] for key, value in schema.items()
    }
    assert domains == {"source_entities": "climate", "delta_source_entities": "sensor"}


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [False, True])
async def test_primary_labels_use_names_without_changing_values(flow_module, options):
    sources = ["climate.room", "sensor.pi_delta", "sensor.missing"]
    flow = _flow(flow_module, options)
    flow.hass.states.get.side_effect = lambda entity: {
        "climate.room": SimpleNamespace(name="Living room radiator"),
        "sensor.pi_delta": SimpleNamespace(name="Hallway PI delta"),
    }.get(entity)
    first = flow.async_step_init if options else flow.async_step_user
    await first({"source_entities": sources[:1], "delta_source_entities": sources[1:]})
    result = await flow.async_step_primary_sources()
    schema = result["data_schema"].schema
    selector = next(iter(schema.values())).config["select"]
    assert selector["mode"] == "list"
    assert selector["options"] == [
        {"value": sources[0], "label": "Living room radiator"},
        {"value": sources[1], "label": "Hallway PI delta"},
        {"value": sources[2], "label": sources[2]},
    ]


def test_primary_duplicate_and_empty_names_have_unambiguous_fallback(flow_module):
    hass = MagicMock()
    names = {"climate.a": "Room", "sensor.b": "Room", "sensor.c": " "}
    hass.states.get.side_effect = lambda entity: SimpleNamespace(name=names[entity])
    schema = flow_module._primary_sources_schema(
        list(names), ["sensor.b"], hass=hass
    ).schema
    assert next(iter(schema)).default() == ["sensor.b"]
    assert next(iter(schema.values())).config["select"]["options"] == [
        {"value": "climate.a", "label": "Room (climate.a)"},
        {"value": "sensor.b", "label": "Room (sensor.b)"},
        {"value": "sensor.c", "label": "sensor.c"},
    ]


def test_primary_without_hass_keeps_id_labels_and_selected_values(flow_module):
    sources = ["climate.a", "sensor.b"]
    schema = flow_module._primary_sources_schema(sources, ["sensor.b"]).schema
    assert next(iter(schema)).default() == ["sensor.b"]
    assert next(iter(schema.values())).config["select"]["options"] == [
        {"value": source, "label": source} for source in sources
    ]


def test_renaming_a_primary_source_does_not_change_selection(flow_module):
    hass = MagicMock()
    for name in ("Old name", "New name"):
        hass.states.get.return_value = SimpleNamespace(name=name)
        schema = flow_module._primary_sources_schema(
            ["sensor.b"], ["sensor.b"], hass=hass
        ).schema
        assert next(iter(schema)).default() == ["sensor.b"]
        assert next(iter(schema.values())).config["select"]["options"] == [
            {"value": "sensor.b", "label": name}
        ]


@pytest.mark.parametrize("language", ["en", "nl"])
def test_setup_and_options_describe_delta_input_contract(language):
    path = (
        Path(__file__).parents[1]
        / f"custom_components/climatesync/translations/{language}.json"
    )
    translations = json.loads(path.read_text())
    for section, step in [("config", "user"), ("options", "init")]:
        source_step = translations[section]["step"][step]
        assert "delta_source_entities" in source_step["data"]
        assert "°C" in source_step["description"]
        assert "**0**" in source_step["description"]


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [False, True])
@pytest.mark.parametrize(
    "mode,value,step,minimum",
    [
        ("target", 5, "setback_target", 5),
        ("delta", 0, "setback_delta", 0),
        ("delta", 10, "setback_delta", 0),
    ],
)
async def test_mode_dependent_single_numeric_field(
    flow_module, options, mode, value, step, minimum
):
    flow = _flow(flow_module, options)
    result = await flow.async_step_destination(
        {
            "destination_entity": "climate.emma",
            "idle_mode": mode,
            "max_setpoint": 35,
            "rounding_mode": "1_decimal",
        }
    )
    assert result["step_id"] == step
    schema = result["data_schema"].schema
    assert len(schema) == 1
    key, selector = next(iter(schema.items()))
    assert str(key) == "idle_value"
    assert selector.config["number"]["min"] == minimum
    assert selector.config["number"]["step"] == 0.5
    result = await getattr(flow, f"async_step_{step}")({"idle_value": value})
    assert result["step_id"] == "control"
    assert flow._destination_settings["idle_mode"] == mode
    assert flow._destination_settings["idle_value"] == value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,value",
    [
        ("target", 4.5),
        ("target", 36),
        ("delta", -0.5),
        ("delta", float("nan")),
        ("target", float("inf")),
    ],
)
async def test_setback_rejects_out_of_range_and_nonfinite(flow_module, mode, value):
    flow = _flow(flow_module)
    await flow.async_step_destination(
        {
            "destination_entity": "climate.emma",
            "idle_mode": mode,
            "max_setpoint": 35,
            "rounding_mode": "1_decimal",
        }
    )
    result = await flow._async_step_setback({"idle_value": value})
    assert result["errors"] == {"idle_value": "invalid_idle_value"}


@pytest.mark.asyncio
async def test_mode_switch_does_not_reinterpret_old_absolute_value_as_delta(
    flow_module,
):
    flow = _flow(flow_module, options=True, data={"idle_temperature": 10})
    result = await flow.async_step_destination(
        {
            "destination_entity": "climate.emma",
            "idle_mode": "delta",
            "max_setpoint": 35,
            "rounding_mode": "1_decimal",
        }
    )
    key = next(iter(result["data_schema"].schema))
    assert key.default() == 0


@pytest.mark.asyncio
async def test_target_uses_actual_destination_minimum(flow_module):
    flow = _flow(flow_module)
    flow.hass.states.get.return_value.attributes.update(min_temp=12, max_temp=30)
    result = await flow.async_step_destination(
        {
            "destination_entity": "climate.emma",
            "idle_mode": "target",
            "max_setpoint": 35,
            "rounding_mode": "1_decimal",
        }
    )
    selector = next(iter(result["data_schema"].schema.values()))
    assert selector.config["number"]["min"] == 12
    assert selector.config["number"]["max"] == 30
    result = await flow.async_step_setback_target({"idle_value": 10})
    assert result["errors"] == {"idle_value": "invalid_idle_value"}


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [False, True])
async def test_missing_device_limits_block_numeric_step_without_saving(
    flow_module, options
):
    flow = _flow(flow_module, options)
    flow.hass.states.get.return_value.attributes = {"temperature": 20}
    result = await flow.async_step_destination(
        {
            "destination_entity": "climate.emma",
            "idle_mode": "target",
            "max_setpoint": 35,
            "rounding_mode": "1_decimal",
        }
    )
    assert result["errors"] == {"base": "destination_limits_unavailable"}
    result = await flow.async_step_setback_target({"idle_value": 5})
    assert result["type"] == "form"
    assert result["errors"] == {"base": "destination_limits_unavailable"}
