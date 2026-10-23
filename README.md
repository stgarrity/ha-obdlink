# OBDLink BLE

Arrival-only vehicle snapshots for Home Assistant using an **OBDLink CX** and an
existing **ESPHome Bluetooth proxy**. No direct Bluetooth adapter, standalone
daemon, MQTT bridge, or per-vehicle script is required.

This is a community integration, not an official Home Assistant Core integration
or an OBDLink-endorsed product. The protocol library is published separately as
[`obdlink-ble` on PyPI](https://pypi.org/project/obdlink-ble/).

## Install

Requires Home Assistant **2026.10 or later** and an already configured ESPHome
proxy supporting active BLE connections and pairing.

1. In HACS, add `stgarrity/ha-obdlink` as a custom **Integration** repository.
2. Download **OBDLink BLE** and restart Home Assistant.
3. Add **OBDLink BLE** under Settings > Devices & Services, or use Bluetooth
   discovery. Choose a vehicle name, its adapter address, and the existing proxy.

Confirming setup authorizes encrypted Bluetooth connections to this adapter.
For a new bond, power-cycle the CX and finish setup during its bonding window.
Previously bonded peers do not need to be re-enrolled on each arrival.

HACS manages the integration release. Home Assistant installs the exact published
PyPI library version declared in the integration manifest. No source checkout,
local development path, SSH helper, or runtime dependency on this repository is
used by the installed integration.

## Entities

Each configured adapter creates one vehicle device:

| Entity | Meaning |
|---|---|
| Fuel level | Last successfully captured fuel percentage |
| Check engine | Last captured malfunction-indicator status; on means a problem |
| Fuel last updated | Timestamp of the last successful fuel reading |
| Check engine last updated | Timestamp of the last successful indicator reading |

Readings also have `last_updated`, `last_attempt`, `last_error`, and `snapshot`
attributes. Failed captures **never become zero fuel or a false healthy engine**.
Last good readings and their original timestamps survive restarts and remain
visible when the vehicle is away. They are historical snapshots, not live state.

## Arrival behavior

- Listen to fresh advertisements for the configured adapter through the selected
  proxy. Unchanged advertisements count; there is no timer-based OBD polling.
- The first sighting, or a sighting after at least **120 seconds without
  advertisements**, starts one capture. A first installation can therefore read
  an already parked vehicle once.
- Request fuel and check-engine data first and publish each successful response
  immediately. Do not repeat VIN or supported-PID discovery on every encounter.
- Bound capture work to **15 seconds**, with up to five additional seconds for
  disconnect cleanup. No repeated parked polling if a capture fails.
- Secure every BLE link using HA's existing ESPHome connector; never disable the
  ESPHome integration or take over its API subscription.

The adapter may disappear for reasons other than leaving home (radio loss or
sleep), so arrival is a Bluetooth-absence heuristic, not a GPS assertion. A
shorter outage does not trigger a new capture. Slot exhaustion, ECU sleep, phone
connections, and radio conditions can prevent a snapshot in a brief window.
The engine need not be running, but the diagnostic modules must still be awake.
If they shut down before the capture, old readings remain with their timestamps.

The administrative `obdlink_ble.capture` action takes a config-entry `entry_id`
and requests one manual snapshot. It does not enable periodic polling.

For migration or recovery, the administrative `obdlink_ble.import_snapshot`
action accepts `entry_id` and a `readings` mapping. Each `fuel_level` or
`check_engine` record must contain its `value` and original timezone-aware
`captured_at` timestamp. It rejects invalid/future data, never replaces newer
readings, and marks restored values with `capture_source: imported_snapshot`.
Importing historical data does not claim that the vehicle was just read.

## Scope and safety

Only read-only standard OBD diagnostics and temporary adapter text/protocol
settings are allowlisted. There is no arbitrary command API, fault clearing,
ECU coding, actuator control, ignition control, or persistent protocol preset
change. Adapter output headers are restored off after capture when the channel
is still synchronized.

Fuel is standard PID `012F`. The check-engine indication comes from PID `0101`;
any responding ECU reporting an illuminated indicator makes the binary sensor
on. Vehicle support varies. No actual odometer is exposed unless it has been
separately verified; distance since fault clearing is **not** an odometer.

Use one adapter per vehicle. Moving an adapter to another vehicle can otherwise
associate old snapshots with the new vehicle; remove/reconfigure the entry first.

All vehicle addresses, proxy selection, cached protocol, readings, and timestamps
stay in your Home Assistant configuration/storage. The integration does not
collect VIN during automatic captures and sends no vehicle data to external
services. Diagnostics omit addresses, vehicle names, and readings. GitHub/PyPI
receive only software-update requests, not vehicle telemetry.

## Python library

Install `obdlink-ble` to use the UART protocol independently. Supply an async
write function and feed BLE notification payloads to `CommandChannel.notified`.
`capture_snapshot` emits each decoded value through an async callback.
`ArrivalGate` provides the same deterministic arrival policy used by HA.

The library does not open a Bluetooth radio itself. HA's integration uses the
existing HA-owned ESPHome connector so it can explicitly select the right proxy.

## Development

```sh
uv sync --locked
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q src custom_components
uv build
uv run python tools/build_release.py
```

The library has no runtime dependencies. Third-party build tooling is pinned,
with a release-age cutoff in `pyproject.toml`. Public distributions include only
the library, license, and generic documentation. The integration release ZIP
contains only `custom_components/obdlink_ble` contents.

Private reports, credentials, instance backups, `.storage`, virtual environments,
and local configuration must never be committed or attached to releases.

## References

- [OBDLink CX pairing and UART notes](https://support.obdlink.com/support/solutions/articles/43000746707-obdlink-cx-adapter-notes)
- [OBDLink programming reference](https://www.scantool.net/scantool/downloads/678/obdlink_frpm_e.pdf)
- [Home Assistant Bluetooth APIs](https://developers.home-assistant.io/docs/core/bluetooth/api/)
- [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy/)
