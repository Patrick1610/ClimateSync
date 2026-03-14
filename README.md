# ClimateSync

[![HACS Custom][hacs-shield]][hacs-url]
[![License: MIT][license-shield]][license-url]

> **Developed with Plugwise Emma in mind, but universally usable with any Home Assistant climate entity that exposes `current_temperature` and `temperature` attributes.**

ClimateSync is a HACS-ready Home Assistant custom integration that synchronises a destination thermostat with the room that has the highest heating demand across multiple source rooms. It supports two operating modes: **Delta Mode** (default, universal) and **Offset Mode** (advanced, for thermostats that expose a temperature-offset entity).

---

## Operating Modes

### Delta Mode (Default)

Works with **any** thermostat. ClimateSync finds the room with the highest heat demand (largest gap between target and current temperature — the *delta*) and adds that gap to the destination's own current temperature:

```
destination_target = destination_current_temperature + delta_max
```

This approach was designed with the **Plugwise Emma** in mind. The Emma is connected to the central-heating boiler (CV) over **OpenTherm** and internally calculates a desired CV water temperature based on its own current delta. By feeding the Emma the home-wide maximum delta on top of its own measured temperature, ClimateSync lets the Emma function as a full OpenTherm controller — it sees the "hardest working" room's demand and adjusts the boiler modulation accordingly.

Even without an Emma, the delta-based method is more accurate than copying setpoints because it accounts for how far the destination already is from equilibrium.

### Offset Mode (Advanced)

**Requires** the destination thermostat to expose a temperature-offset entity (`number` or `input_number`).

Offset Mode makes the destination thermostat *behave exactly like the room with the highest heat demand* by manipulating the thermostat's temperature offset — rather than its setpoint alone. The thermostat's internal modulation logic then reacts as if it were physically located in the leading room.

#### Why reconstruct the real temperature?

After applying an offset the thermostat's reported `current_temperature` is no longer its real measured temperature — it becomes an **effective temperature**:

```
reported_current = real_current + offset
```

Therefore, on every recalculation ClimateSync must first reconstruct the real temperature before computing a new offset:

```
destination_real_current = destination_reported_current − current_offset
new_offset               = leading_room_current − destination_real_current
new_target               = leading_room_target
```

#### Worked Example 1 — First sync

Destination state:
- real local current = 19.2 °C
- offset = 0.0
- reported current = 19.2 °C

Source rooms:

| Room | Current | Target | Delta |
|---|---|---|---|
| Living Room | 20.0 | 20.5 | 0.5 |
| **Bathroom** | **18.0** | **19.5** | **1.5** ← leading |
| Office | 19.5 | 19.5 | 0 |

Calculation:
```
destination_real_current = 19.2 − 0.0     = 19.2
new_offset               = 18.0 − 19.2    = −1.2
new_target               = 19.5
```

Result: destination effectively becomes **18.0 → 19.5**.

#### Worked Example 2 — Later resync with a new leading room

Current destination state:
- real local current = 19.2 °C
- offset = −1.2
- reported current = 18.0 °C (= 19.2 + (−1.2))

Source rooms:

| Room | Current | Target | Delta |
|---|---|---|---|
| Living Room | 19.8 | 20.0 | 0.2 |
| Bathroom | 19.2 | 19.5 | 0.3 |
| **Master Bedroom** | **16.0** | **18.0** | **2.0** ← leading |

Reconstruct real temperature:
```
destination_real_current = 18.0 − (−1.2)  = 19.2
new_offset               = 16.0 − 19.2    = −3.2
new_target               = 18.0
```

Result: destination effectively becomes **16.0 → 18.0**.

#### Write order

When applying offset mode changes ClimateSync:
1. Calls `number.set_value` (or `input_number.set_value`) for the offset entity
2. Waits ~500 ms for the thermostat to process the new offset
3. Calls `climate.set_temperature` for the target temperature

---

## Features

- Two operating modes: **Delta Mode** (universal) and **Offset Mode** (advanced).
- Event-driven updates — reacts immediately to temperature changes.
- Periodic resync (configurable, default 60 s) to recover from missed events.
- Anti-flap: only sends service calls when the change exceeds a configurable threshold (default 0.2 °C).
- Rate limiting: maximum one service call sequence per 10 seconds (configurable).
- Robust source handling: missing, unavailable or deleted source entities are silently treated as delta = 0 — the integration never breaks.
- Rich diagnostic sensors including a `sensor.climatesync_status` with offset-mode diagnostics.
- **No controllable entities** — all control is internal via HA service calls.

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

### Step 2 — Operating mode

Choose between:

| Mode | Description |
|---|---|
| **Delta Mode (Default)** | Works with any thermostat. Destination target = destination current + highest room delta. |
| **Offset Mode (Advanced)** | Destination thermostat behaves like the leading room by adjusting its offset. Requires a thermostat offset entity (`number` or `input_number`). |

### Step 3 — Destination & basic settings

| Field | Default | Description |
|---|---|---|
| Destination climate entity | — | The thermostat that ClimateSync will control. |
| Idle temperature | 5.0 °C | Target temperature sent to the destination when no room has a positive delta. |
| Maximum setpoint | 35.0 °C | Hard ceiling for the destination setpoint (prevents runaway spikes). |
| Rounding mode | 1 decimal | How the computed setpoint is rounded before being sent. |
| Offset entity *(Offset Mode only)* | — | The `number` or `input_number` entity that exposes the thermostat's temperature offset. |

### Options Flow — reconfigure everything via the settings gear

After setup, open the integration → **Configure** (⚙ gear icon) to get the same 3-step wizard again. In addition to the above, the options flow also exposes:

| Option | Default | Description |
|---|---|---|
| Resync interval | 60 s | How often ClimateSync checks even without state changes. |
| Minimum change threshold | 0.2 °C | Only send a new setpoint if the change exceeds this. |
| Minimum send interval | 10 s | At most one service call sequence per this many seconds. |
| Offset settling window | 3 s | *(Offset Mode)* Ignore source-triggered re-evaluations for this many seconds after an offset write. |
| Offset minimum change | 0.2 °C | *(Offset Mode)* Minimum offset change required to trigger a write. |
| Offset minimum interval | 10 s | *(Offset Mode)* Minimum time between consecutive offset writes. |
| Leader switch threshold | 0.3 °C | *(Offset Mode)* Challenger must exceed current leader's delta by this amount to switch immediately. |
| Leader stick seconds | 30 s | *(Offset Mode)* Challenger that remains highest delta for this long takes over as leader. |

---

## Algorithms

### Delta Mode

```
For each source climate entity (room):
    delta = max(target - current, 0)
            (if either attribute is missing/unavailable → delta = 0)

delta_max = max(all room deltas)

If delta_max <= 0:
    setpoint = idle_temperature
Else:
    setpoint = destination_current_temperature + delta_max

setpoint_final = round(setpoint, rounding_mode)

If abs(destination_current_target - setpoint_final) > min_change_threshold:
    If time_since_last_call >= min_send_interval:
        climate.set_temperature(destination, setpoint_final)
```

### Offset Mode

```
For each source climate entity (room):
    delta = max(target - current, 0)

delta_max    = max(all room deltas)
leading_room = room with delta_max

If delta_max <= 0:
    climate.set_temperature(destination, idle_temperature)
    # offset is NOT reset
Else:
    current_offset       = number state of offset_entity
    dest_real_current    = destination_reported_current - current_offset
    new_offset           = leading_room_current - dest_real_current
    new_target           = leading_room_target

    # Apply in order:
    number.set_value(offset_entity, new_offset)
    wait 500 ms
    climate.set_temperature(destination, new_target)
```

---

### Offset Mode — Stability Safeguards

In some setups a source room's reported temperature may **indirectly depend on the destination thermostat** itself (for example because a calculated "zone temperature" partially incorporates the destination sensor's reading). This can create a short feedback loop:

1. ClimateSync writes a new offset to the destination thermostat.
2. The thermostat adjusts its reported current temperature almost immediately.
3. That temperature change triggers a source room recalculation.
4. The source room's delta changes — ClimateSync evaluates again.
5. A new offset is written — back to step 2.

The result is rapid oscillation ("thrashing") of the offset and target values. ClimateSync includes five independent safeguards against this, all tunable in the Options Flow.

#### 1. Settling window (`offset_settle_seconds`, default 3 s)

After a successful offset write, ClimateSync ignores **source-triggered** re-evaluations for a short settling period. During this window the physical temperatures are still settling and any delta change is most likely caused by the offset write itself rather than a genuine demand change.

Destination-triggered evaluations (i.e. the destination thermostat reporting an unexpected target value) always bypass this window.

#### 2. Sticky leading room (`leader_switch_threshold`, `leader_stick_seconds`, defaults 0.3 °C / 30 s)

ClimateSync does not immediately switch the leading room every time a slightly different room claims the highest delta. A new room is only accepted as the new leader if either:

- its delta exceeds the current leader's delta by more than `leader_switch_threshold` (immediate switch), **or**
- it has consistently been the raw leader for at least `leader_stick_seconds` (time-based switch).

This prevents constant leader flip-flopping between rooms with nearly identical demand.

#### 3. Offset write threshold (`offset_min_change`, default 0.2 °C)

A new offset value is only written to the thermostat if the difference between the desired offset and the current offset exceeds `offset_min_change`. Sub-threshold changes are silently skipped. The target temperature write is still evaluated independently.

#### 4. Offset write cooldown (`offset_min_interval_seconds`, default 10 s)

Even when a new offset value passes all other checks, it is not written again if the last offset write happened less than `offset_min_interval_seconds` ago. This acts as a hard cooldown between consecutive offset writes.

#### Summary of new Options Flow settings

| Option | Default | Range | Description |
|---|---|---|---|
| `offset_settle_seconds` | 3 s | 0 – 30 s | Post-write settling window. Source-triggered evals are ignored during this period. |
| `offset_min_change` | 0.2 °C | 0.0 – 2.0 °C | Minimum offset change to trigger a write. |
| `offset_min_interval_seconds` | 10 s | 0 – 120 s | Minimum time between consecutive offset writes. |
| `leader_switch_threshold` | 0.3 °C | 0.0 – 5.0 °C | New leader must exceed current leader by this delta to switch immediately. |
| `leader_stick_seconds` | 30 s | 0 – 300 s | Alternatively, a challenger that remains the raw leader for this long is accepted. |

#### Diagnostic attributes (loop-aware)

The `sensor.climatesync_status` entity exposes additional attributes when Offset Mode is active:

| Attribute | Description |
|---|---|
| `settling_active` | `true` when the post-write settling window is currently active. |
| `settling_until` | ISO-8601 timestamp when the settling window expires. |
| `last_offset_write_time` | Timestamp of the most recent successful offset write. |
| `last_target_write_time` | Timestamp of the most recent successful target write. |
| `offset_min_interval_blocked_count` | How many offset writes were suppressed by the cooldown guard. |
| `offset_threshold_skipped_count` | How many offset writes were skipped because the change was below `offset_min_change`. |
| `possible_feedback_events` | How many source-triggered evaluations occurred during a settling window (potential feedback loop detections). |
| `current_leading_room` | The currently active (sticky) leading room entity id. |
| `previous_leading_room` | The entity id of the room that was leader before the last switch. |



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

**State**: the rounded setpoint ClimateSync wants to apply.

| Attribute | Description |
|---|---|
| `destination_entity_id` | The controlled thermostat |
| `destination_current_temperature` | Current measured temperature at destination |
| `destination_current_target` | Current target temperature at destination |
| `delta_max` | Max delta used for this computation |
| `rounding_mode` | Active rounding mode |
| `idle_temperature` | Configured idle temperature |

#### Max delta — `sensor.climatesync_2_delta_max`

**State**: the maximum delta across all rooms.

| Attribute | Description |
|---|---|
| `room_deltas` | Map of `{entity_id: delta}` for all rooms |
| `leading_room` | Entity id of the room with the highest delta |

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

Shows the destination thermostat's actual current target temperature in real time.

### Diagnostic

#### Status — `sensor.climatesync_status` *(most important)*

**States:**

| State | Meaning |
|---|---|
| `ok` | Everything is in sync, no issues. |
| `rate_limited` | An update was suppressed because the last call was too recent. |
| `destination_unavailable` | The destination climate entity is unavailable or unknown. |
| `missing_source_data` | One or more source entities have missing/unavailable temperature attributes. The integration continues with delta = 0 for those rooms. |
| `apply_failed` | A service call threw an exception. Check `last_error`. |
| `mismatch` | The destination's actual target deviates from the desired setpoint beyond the threshold. ClimateSync will attempt to correct this on the next cycle. |

**Attributes (all modes):**

| Attribute | Description |
|---|---|
| `mode` | Active operating mode (`delta` or `offset`) |
| `last_update_time` | ISO timestamp of the last evaluation |
| `last_service_call_time` | ISO timestamp of the last service call |
| `last_desired_setpoint` | What ClimateSync computed as the ideal setpoint |
| `last_applied_setpoint` | What was last actually sent to the destination |
| `current_destination_target` | The destination's actual `temperature` attribute right now |
| `mismatch_seconds` | How long (seconds) the desired and actual setpoint have been diverging |
| `mismatch_since` | ISO timestamp of when the mismatch started (null when in sync) |
| `resync_count` | Number of periodic resyncs since startup |
| `apply_attempts` | Total service call attempts since startup |
| `apply_failures` | Total service call failures since startup |
| `evaluation_count` | Total evaluation cycles since startup |
| `skipped_anti_flap` | Times an update was skipped because the change was within the threshold |
| `skipped_rate_limit` | Times an update was skipped due to rate limiting |
| `last_error` | Last exception message, if any |
| `leading_room` | Entity id of the room with the highest delta |
| `leading_room_current` | Current temperature of the leading room |
| `leading_room_target` | Target temperature of the leading room |
| `destination_reported_current` | The destination's reported current temperature |

**Offset Mode additional attributes:**

| Attribute | Description |
|---|---|
| `destination_real_current` | Reconstructed real temperature (`reported - offset`) |
| `current_offset` | Currently applied offset value |
| `desired_offset` | Computed new offset for this cycle |
| `last_applied_offset` | Last offset value successfully written |
| `desired_target` | Target temperature computed for this cycle |
| `last_applied_target` | Last target temperature successfully written |
| `settling_active` | `true` when the post-write settling window is active |
| `settling_until` | ISO timestamp when settling window expires |
| `last_offset_write_time` | Timestamp of most recent offset write |
| `last_target_write_time` | Timestamp of most recent target write |
| `offset_min_interval_blocked_count` | Offset writes suppressed by cooldown guard |
| `offset_threshold_skipped_count` | Offset writes skipped (change below threshold) |
| `possible_feedback_events` | Source evals blocked during settling window (feedback events) |
| `current_leading_room` | Currently active sticky leading room |
| `previous_leading_room` | Previous leading room before last switch |

---

## Backward Compatibility

Existing installations automatically default to `mode = delta`. No changes are required. All existing behaviour is preserved.

---

## Troubleshooting

### Destination is not accepting the setpoint

Some thermostats (e.g. Plugwise Emma) only accept specific temperature steps. Use the **0.5 steps** rounding mode in that case.

### `mismatch_seconds` keeps growing

An external automation or the user may be overriding the destination's target. ClimateSync will keep trying to reapply on every evaluation cycle and resync.

### `rate_limited` appears frequently

The source rooms are changing temperature very rapidly. Increase `min_send_interval` in the Options Flow to reduce chatter.

### `missing_source_data`

One or more source climate entities are offline or do not expose `current_temperature` / `temperature` attributes. ClimateSync treats those rooms as delta = 0 and continues.

### `destination_unavailable`

The destination thermostat is offline. No service calls are made. ClimateSync will recover automatically once the entity becomes available again.

### `apply_failed`

Check `last_error` in the `sensor.climatesync_status` attributes. Most likely the climate entity does not support the required service or the entity id is wrong.

### Offset mode: offset entity unavailable

Ensure the offset entity (`number` or `input_number`) is online and its state is a valid number. ClimateSync will log a warning and skip the update cycle.

---

## Compatibility

**Delta Mode** works with any climate integration that follows the standard HA climate platform contract, including Plugwise Emma / Smile, Generic Thermostat, ESPHome, Z-Wave, Zigbee, Google Nest, and more.

**Offset Mode** additionally requires the destination thermostat to expose a temperature-offset entity (`number` or `input_number`). Examples include thermostats with a Zigbee `local_temperature_calibration` attribute exposed as a number entity, or any thermostat integration that allows adjusting an internal temperature offset.

---

## License

MIT — see [LICENSE](LICENSE).

[hacs-shield]: https://img.shields.io/badge/HACS-Custom-orange.svg
[hacs-url]: https://hacs.xyz
[license-shield]: https://img.shields.io/badge/License-MIT-yellow.svg
[license-url]: LICENSE


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
