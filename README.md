# ClimateSync

[![HACS Custom][hacs-shield]][hacs-url]
[![License: MIT][license-shield]][license-url]

> **Developed with Plugwise Emma in mind, but universally usable with any Home Assistant climate entity that exposes `current_temperature` and `temperature` attributes.**

ClimateSync is a HACS-ready Home Assistant custom integration that implements **delta-based thermostat synchronisation**. It reads the heating demand (delta between target and current temperature) from multiple source climate entities (rooms) and drives a single destination thermostat by continuously adjusting its target temperature.

### Why delta-based instead of copying the highest setpoint?

Instead of simply syncing the highest source setpoint to the destination, ClimateSync uses the **maximum delta** (largest gap between target and current temperature across all rooms) and adds it to the **destination's own current temperature**:

```
destination_target = destination_current_temperature + delta_max
```

This approach was designed with the **Plugwise Emma** in mind. The Emma is connected to the central-heating boiler (CV) over **OpenTherm** and internally calculates a desired CV water temperature based on its own current delta. By feeding the Emma the home-wide maximum delta on top of its own measured temperature, ClimateSync lets the Emma function as a full OpenTherm controller — it sees the "hardest working" room's demand and adjusts the boiler modulation accordingly.

Even without an Emma, this delta-based method is more accurate than copying setpoints because it accounts for how far the destination already is from equilibrium.

---

## Features

- Event-driven updates — reacts immediately to temperature changes.
- Periodic resync (configurable, default 60 s) to recover from missed events.
- Anti-flap: only sends `climate.set_temperature` when the change exceeds a configurable threshold (default 0.2 °C).
- Rate limiting: maximum one service call per 10 seconds (configurable).
- Rich diagnostic sensors including a `sensor.climatesync_status` that makes desyncs visible.
- **No controllable entities** — all control is internal via `climate.set_temperature`.

---

## Installation

### Via HACS (recommended)

1. Open HACS → Integrations → ⋮ → Custom repositories.
2. Add `https://github.com/Patrick1610/ClimateSync` as an **Integration**.
3. Search for *ClimateSync* and install.
4. Restart Home Assistant.

### Manual

1. Copy `custom_components/climatesync/` to your `<config>/custom_components/` directory.
2. Restart Home Assistant.

---

## Configuration

Navigate to **Settings → Devices & Services → Add Integration → ClimateSync**.

### Step 1 — Source rooms

Select one or more **climate entities** that represent the rooms whose heating demand should be tracked. Each selected entity must expose `current_temperature` and `temperature` attributes.

### Step 2 — Destination & basic settings

| Field | Default | Description |
|---|---|---|
| Destination climate entity | — | The thermostat that ClimateSync will control. |
| Idle temperature | 5.0 °C | Target temperature sent to the destination when no room has a positive delta (all rooms are at or above their target). |
| Rounding mode | 1 decimal | How the computed setpoint is rounded before being sent. |

### Options Flow — reconfigure everything via the settings gear

After setup, open the integration → **Configure** (⚙ gear icon) to get the same 2-step wizard again. You can change:

- **Step 1**: add or remove source rooms
- **Step 2**: change the destination thermostat, idle temperature, rounding mode, and advanced options:

| Option | Default | Description |
|---|---|---|
| Destination thermostat | — | Change which thermostat is controlled. |
| Idle temperature | 5.0 °C | Temperature sent when no room needs heating. |
| Rounding mode | 1 decimal | How setpoints are rounded. |
| Resync interval | 60 s | How often ClimateSync checks even without state changes. |
| Minimum change threshold | 0.2 °C | Only send a new setpoint if the change exceeds this. |
| Minimum send interval | 10 s | At most one service call per this many seconds. |

---

## Algorithm

```
For each source climate entity (room):
    current = current_temperature attribute
    target  = temperature attribute
    delta   = max(target - current, 0)
              (if either attribute is missing/unavailable → delta = 0)

delta_max = max(all room deltas)

If delta_max <= 0:
    setpoint_raw = idle_temperature        # No room needs heating
Else:
    setpoint_raw = destination_current_temperature + delta_max
    # The destination target is set to its own current temperature
    # plus the largest demand across all source rooms. This means the
    # destination "feels" the same heating gap as the hardest-working
    # room, which is critical for OpenTherm controllers like the
    # Plugwise Emma that modulate boiler output based on their own
    # observed delta.

setpoint_final = round(setpoint_raw, rounding_mode)

If abs(destination_current_target - setpoint_final) > min_change_threshold:
    If time_since_last_call >= min_send_interval:
        climate.set_temperature(destination, setpoint_final)
```

### Rounding modes

| Mode | Example input | Result |
|---|---|---|
| `0.5 steps` | 19.3 | 19.5 |
| `1 decimal` | 19.33 | 19.3 |
| `2 decimals` | 19.333 | 19.33 |

---

## Entities

All entities are attached to a **ClimateSync** device. Sensors (setpoint, deltas, destination target) are regular entities; the status sensor is classified as *diagnostic*.

### Sensors

#### Computed setpoint — `sensor.climatesync_1_destination_setpoint`

| Attribute | Description |
|---|---|
| `destination_entity_id` | The controlled thermostat |
| `destination_current_temperature` | Current measured temperature at destination |
| `destination_current_target` | Current target temperature at destination |
| `delta_max` | Max delta used for this computation |
| `rounding_mode` | Active rounding mode |
| `idle_temperature` | Configured idle temperature |

**State**: the rounded setpoint that ClimateSync wants to apply (`destination_current_temperature + delta_max`, rounded).

#### Max delta — `sensor.climatesync_2_delta_max`

| Attribute | Description |
|---|---|
| `room_deltas` | Map of `{entity_id: delta}` for all rooms |
| `leading_room` | Entity id of the room with the highest delta |

**State**: the maximum delta across all rooms.

#### Per-room delta sensors — `sensor.climatesync_delta_<slug>`

One sensor per source climate entity.

| Attribute | Description |
|---|---|
| `source_entity_id` | The climate entity this sensor tracks |
| `current_temperature` | Last known current temperature |
| `target_temperature` | Last known target temperature |
| `raw_delta` | `target - current` (may be negative) |

**State**: `max(raw_delta, 0)` — the effective heating demand for this room.

#### Destination current target — `sensor.climatesync_destination_current_target`

Shows the destination thermostat's actual current target temperature in real time, making it easy to compare against the computed setpoint without switching to the destination device.

| Attribute | Description |
|---|---|
| `destination_entity_id` | The controlled thermostat |
| `destination_current_temperature` | Current measured temperature at destination |

**State**: the destination's current `temperature` attribute (its active target).

### Diagnostic

#### Status — `sensor.climatesync_status` *(most important)*

**States:**

| State | Meaning |
|---|---|
| `ok` | Everything is in sync, no issues. |
| `rate_limited` | A setpoint update was suppressed because the last call was too recent. |
| `destination_unavailable` | The destination climate entity is unavailable or unknown. |
| `missing_source_data` | One or more source entities have missing/unavailable temperature attributes. The integration continues with delta = 0 for those rooms. |
| `apply_failed` | The `climate.set_temperature` service call threw an exception. Check `last_error`. |
| `mismatch` | The destination's actual target deviates from the desired setpoint beyond the threshold. ClimateSync will attempt to correct this on the next cycle. |

**Attributes:**

| Attribute | Description |
|---|---|
| `last_update_time` | ISO timestamp of the last evaluation |
| `last_service_call_time` | ISO timestamp of the last `climate.set_temperature` call |
| `last_desired_setpoint` | What ClimateSync computed as the ideal setpoint |
| `last_applied_setpoint` | What was last actually sent to the destination |
| `current_destination_target` | The destination's actual `temperature` attribute right now |
| `mismatch_seconds` | How long (seconds) the desired and actual setpoint have been diverging |
| `mismatch_since` | ISO timestamp of when the mismatch started (null when in sync) |
| `resync_count` | Number of periodic resyncs since startup |
| `apply_attempts` | Total service call attempts since startup |
| `apply_failures` | Total service call failures since startup |
| `evaluation_count` | Total evaluation cycles since startup |
| `skipped_anti_flap` | Times a setpoint update was skipped because the change was within the threshold |
| `skipped_rate_limit` | Times a setpoint update was skipped due to rate limiting |
| `last_error` | Last exception message, if any |

---

## Troubleshooting

### Destination is not accepting the setpoint

Some thermostats (e.g. Plugwise Emma) only accept specific temperature steps. Use the **0.5 steps** rounding mode in that case.

### `mismatch_seconds` keeps growing

An external automation or the user may be overriding the destination's target. ClimateSync will keep trying to reapply on every evaluation cycle and resync. Check if another integration or automation is fighting over the thermostat.

### `rate_limited` appears frequently

The source rooms are changing temperature very rapidly. Increase `min_send_interval` in the Options Flow to reduce chatter.

### `missing_source_data`

One or more source climate entities are offline or do not expose `current_temperature` / `temperature` attributes. ClimateSync treats those rooms as delta = 0 and continues.

### `destination_unavailable`

The destination thermostat is offline. No service calls are made. ClimateSync will recover automatically once the entity becomes available again.

### `apply_failed`

Check `last_error` in the `sensor.climatesync_status` attributes. Most likely the climate entity does not support the `climate.set_temperature` service or the entity id is wrong.

---

## Compatibility

ClimateSync uses only the standard `climate.set_temperature` service and reads standard climate entity attributes (`current_temperature`, `temperature`). It works with any climate integration that follows the standard HA climate platform contract, including but not limited to:

- Plugwise Emma / Smile
- Generic Thermostat
- ESPHome climate components
- Z-Wave thermostats
- Zigbee thermostats (ZHA / Zigbee2MQTT)
- Google Nest (via the Nest integration)

---

## License

MIT — see [LICENSE](LICENSE).

[hacs-shield]: https://img.shields.io/badge/HACS-Custom-orange.svg
[hacs-url]: https://hacs.xyz
[license-shield]: https://img.shields.io/badge/License-MIT-yellow.svg
[license-url]: LICENSE
