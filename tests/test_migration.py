"""Legacy idle targets must retain their meaning and effective options."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.climatesync import async_migrate_entry


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("override", [None, 0, 10, 17.5])
async def test_legacy_idle_target_migration_preserves_effective_options(
    version, override
):
    options = {"rounding_mode": "1_decimal", "min_change_threshold": 0.1}
    if override is not None:
        options["idle_temperature"] = override
    entry = SimpleNamespace(
        version=version,
        entry_id="test",
        data={
            "source_entities": ["climate.room"],
            "idle_temperature": 5,
            "destination_entity": "climate.emma",
        },
        options=options.copy(),
    )
    hass = MagicMock()

    def update(entry, **fields):
        for key, value in fields.items():
            setattr(entry, key, value)

    hass.config_entries.async_update_entry.side_effect = update
    assert await async_migrate_entry(hass, entry)
    assert entry.version == 3
    assert entry.data["idle_mode"] == "target"
    effective = {**entry.data, **entry.options}
    assert effective["idle_value"] == (5 if override is None else override)
    assert entry.options["min_change_threshold"] == 0.1
    before = (entry.data.copy(), entry.options.copy())
    hass.config_entries.async_update_entry.reset_mock()
    assert await async_migrate_entry(hass, entry)
    hass.config_entries.async_update_entry.assert_not_called()
    assert before == (entry.data, entry.options)
