# unfurled

An async Python library for controlling [Unfolded Circle](https://www.unfoldedcircle.com/) Remote Two and Remote 3 devices.

---

## Features

- Async-first design built on `aiohttp` and `websockets`
- Full REST API coverage via `CoreAPI`
- Real-time WebSocket event stream with auto-reconnect
- High-level `Remote` class for common operations (activities, IR, media, docks)
- `Dock` class for IR learning, firmware updates, and codeset management
- mDNS discovery via `zeroconf`
- Clean exception hierarchy
- Typed with mypy

---

## Installation

```bash
# Using uv (recommended)
uv add unfurled

# Or pip
pip install unfurled
```

To install for development:

```bash
git clone https://github.com/you/unfurled
cd unfurled
uv venv --python 3.11
uv pip install -e ".[dev]"
```

---

## Quick Start

```python
import asyncio
from unfurled.remote import Remote


async def main():
    remote = Remote("http://192.168.1.100:80", api_key="your-api-key")
    await remote.init()

    print(remote.name)  # "My Remote Two" (or auto-derived)
    print(remote.info.model_name)  # "Remote Two"
    print(remote.info.hw_revision)  # "Revision 2"
    print(remote.sw_version)  # "2.1.0"

    # List activities
    for act in remote.activities:
        print(act.name, "-", "ON" if act.is_on else "off")

    # Turn on an activity
    await remote.find_activity("my-activity-id").turn_on()


asyncio.run(main())
```

---

## Discovery

Find remotes on the local network via mDNS:

```python
from unfurled.discovery import discover_remotes


async def main():
    devices = await discover_remotes(timeout=5)
    for d in devices:
        print(d.hostname, d.address, d.port)
```

---

## Remote

### Construction

```python
# API key (preferred)
remote = Remote("http://192.168.1.100:80", api_key="your-api-key")

# PIN
remote = Remote("http://192.168.1.100:80", pin="1234")
```

### Initialisation

`await remote.init()` fetches the full device state in one call:
configuration, activities, entities, docks, IR emitters, update info.

### Key Properties

| Property | Description |
|---|---|
| `name` | Device display name (falls back to model name) |
| `info.model_name` | Marketing model name (e.g. `"Remote Two"`) |
| `info.hw_revision` | Human-readable hardware revision (e.g. `"Revision 2"`) |
| `info.serial_number` | Device serial number |
| `sw_version` | Currently running firmware |
| `latest_sw_version` | Latest available firmware |
| `available_update` | `True` when an update is ready |
| `settings.network.wifi_enabled` | Wi-Fi radio state |
| `settings.network.bt_enabled` | Bluetooth radio state |
| `settings.display.brightness` | Display brightness (0-100) |
| `settings.power_saving.standby_sec` | Display sleep timeout (seconds) |
| `activities` | `list[Activity]` |
| `docks` | `list[Dock]` |

### Firmware capability matrix

Availability flags are grouped in the `RemoteFeatureFlags` dataclass at
`remote.system.flags` and populated by `await remote.init()`.

| Flag | Minimum firmware |
|---|---|
| `external_entity_configuration_available` | 2.0.0 (also enabled for simulators) |
| `new_web_configurator` | 2.2.0 |
| `inplace_upgrade_available` | 2.9.3 |
| `entity_state_update_available` | 2.10.1 |
| `webview_available` | 2.14.0 |

These flags describe firmware support. For a web view's runtime availability,
check `view.state`: Core can report `UNAVAILABLE` when no capable UI is connected.
Flags do not prevent explicit API calls or trigger requests themselves.
Network capability information remains grouped with network settings:
`remote.settings.network.wifi.wake_on_wlan_available` (Remote 3 requires 2.7.0+).

### Activities

```python
# List
for act in remote.activities:
    print(act.name, act.state)

# Find and control
act = remote.find_activity("activity-id")
await act.turn_on()
await act.turn_off()

# Correct a drifted runtime state without running an activity sequence
if remote.system.flags.entity_state_update_available:
    await act.set_state("ON")

# All off
await remote.turn_off_all_activities()
```

### Settings

Configuration is grouped under `remote.settings`:

```python
# Adjust display brightness
await remote.settings.update_display(brightness=80)

# Enable Wi-Fi wake-on-LAN
await remote.settings.update_network(wake_on_wlan=True)

# Change sound volume
await remote.settings.update_sound(volume=60)
```

### Web views

Web views require Remote firmware 2.14.0 or newer. Definitions are fetched on
demand; existing initialization works with older firmware.

```python
if remote.system.flags.webview_available:
    view = await remote.get_webview("uc_web.main.dashboard")
    await view.open()
```

```python
# List overviews, filtered to USER or EXTERNAL if desired
overviews = await remote.api.get_webviews(kind="USER", q="Dashboard")
count = await remote.api.get_webview_count(kind="USER")

# Fetch a full definition; the object receives subsequent WebSocket changes
view = await remote.get_webview(overviews[0]["entity_id"])
await view.open()
await view.reload()

# Create a user-defined web view (or use clone_from to copy an existing one)
view = await remote.create_webview({
    "name": {"en": "Dashboard"},
    "options": {"url": "https://home.example/dashboard", "zoom": 80},
})
await view.update({"options": {"zoom": None}})  # restore default

# Global settings; changes require admin scope
await remote.settings.update_webview(color_scheme="dark", buttons={"BACK": None})
presets = await remote.api.get_webview_button_presets()
await remote.settings.update_webview(buttons=presets[0]["buttons"])
```

`view.state` indicates availability, not whether the page is open. Core routes
commands to the Remote UI for both user and integration web views. Integration
web views allow metadata changes and `options.user` overrides for zoom, reload
interval, navigation and buttons. Core validates options and reports errors.

Options preserve Core's secret-presence flags; passwords, tokens and cookie
values are omitted from responses. Updates send only the supplied fields,
including explicit `None` resets. `view.refresh()` reloads the full definition.
`view.clear_cache()` and `view.clear_data()` affect **all web views** because they
share website data. `view.delete()` deletes or unconfigures the entity;
`remote.api.delete_webviews()` permanently deletes all user-defined web views.

Typed payloads provide autocomplete and static checking while remaining ordinary
dictionaries. Core still performs validation. Omit a key to keep its stored value;
use `None` to reset an option that supports resetting.

```python
from unfurled import WebViewCreate, WebViewUpdate

definition: WebViewCreate = {
    "name": {"en": "Dashboard"},
    "options": {
        "url": "https://home.example/dashboard",
        "auth": {"type": "bearer", "token": "my-token"},
        "buttons": {"BACK": {"action": "function", "function": "back_or_close"}},
    },
}
view = await remote.create_webview(definition)
patch: WebViewUpdate = {"options": {"zoom": None, "buttons": {"BACK": None}}}
await view.update(patch)
```

Additional exported types cover options, authentication, TLS, cookies, button
actions, integration overrides and global settings updates. `view.options` is
typed as `WebViewOptions`; `editable` and `has_*` fields are response-only.

An **activity widget** is content embedded in an activity's touchscreen page.
A web view widget displays the page in that grid (minimum 3 × 3 cells), whereas
`view.open()` displays it fullscreen. A widget references its included entity as
`{"type": "webview", "webview": {"webview_id": "uc_web.main.dashboard"}, ...}`.
The unused-entity scanner recognizes this reference; there is no widget builder.

WebSocket entity and configuration events update cached objects and invoke the
existing state-change callbacks. Only explicitly fetched or created web views are
cached. Reconnecting does not automatically fetch web view definitions; callers
can choose when to run `await view.refresh()`. The Remote's existing reconnect
refresh of other state remains in place. The library sends no open, reload or
clear commands automatically. Core may reload a shown page in response to an
explicit options update, as defined by its API.

### IR

```python
# Send a raw HEX or PRONTO code
await remote.ir.send(
    "0000 006C ...",
    emitter_name="Dock IR",  # or emitter_id="device-id"
    repeat=1,
)

# Send a command from a remote's assigned codeset
await remote.ir.send("VOLUME_UP", remote_name="Sony")

# Or resolve a manufacturer codeset
await remote.ir.send("VOLUME_UP", manufacturer="Samsung", codeset="Generic TV 1")

# List available emitters
for e in remote.ir.emitters:
    print(e.name, e.device_id)

# Convert a PRONTO or HEX code to raw timings for inspection or reuse
from unfurled import IRFormat

converted = await remote.ir.convert("0000 006C ...", format=IRFormat.PRONTO, repeat=1)
print(converted["raw"], converted["frequency"])
```

### Integrations / External Systems

```python
# Find a specific integration driver instance
instance = await remote.integrations.get_by_driver("hass")

# Set an API token for an external system (e.g. Home Assistant)
await remote.auth.set_external_token(
    system="hass",
    token_id="primary",
    token="long-lived-token",
    name="Home Assistant",
)
```

### Authentication / API Keys

```python
# Generate an admin API key and make it the active key for this Remote.
# A same-named unfurled key is safely replaced.
api_key = await remote.auth.generate_key()
print(api_key)

# Create another key without replacing an existing one
key = await remote.auth.create_key("Home Assistant")

# Revoke a key by name
await remote.auth.revoke_key("Home Assistant")
```

### Firmware Updates

```python
# Force an update check
result = await remote.system.force_update_check()

# GET /system/update also returns the available update metadata
result = await remote.api.get_system_update()
for update in remote.system.update_info.available:
    print(update["id"], update["version"])

# Select and retain the concrete ID in your integration
update_id = "your-selected-update-id"
await remote.system.update_firmware(update_id=update_id, download_only=True)
status = await remote.system.get_update_status(update_id=update_id)
await remote.system.update_firmware(update_id=update_id)

# Current status
print(remote.system.update_info.in_progress, remote.system.update_info.update_percent)
```

GET and PUT `/system/update` return available update records with IDs and versions.
Remote refreshes and `force_update_check()` cache those records in
`remote.system.update_info.available`. The low-level methods
`remote.api.get_system_update_by_id(update_id)` and
`remote.api.post_system_update(update_id)` request that exact ID, safely encoded
as a path segment.

Calls without an ID still request `latest` for compatibility. POST with `latest`
requires a locally downloaded update; GET does not resolve aliases. Use a concrete
ID to download an available update. `download_only=True` checks that same ID and
only sends POST when its state is `PENDING` or `ERROR`. HTTP errors propagate to the
caller, without switching IDs or falling back to `latest`.

### WebSocket Events

```python
from unfurled.websocket import RemoteWebSocketClient


async def on_message(msg: str):
    print("WS event:", msg)


ws = RemoteWebSocketClient(api_url, api_key)
ws.on_message(on_message)
await ws.connect()
```

Or use the built-in client on `Remote`:

```python
remote.add_listener(my_callback)  # raw WS message handler
await remote.connect_websocket()
```

---

## Dock

```python
dock = remote.docks[0]

# Refresh state
await dock.update()

# IR learning
result = await dock.start_ir_learning()
await dock.stop_ir_learning()

# Firmware update
info = await dock.get_update_status()
if info.get("update_available"):
    await dock.update_firmware()

# Custom codesets
codesets = await dock.get_custom_codesets()
await dock.delete_custom_codeset("my-codeset-id")
```

---

## Exceptions

| Exception | When raised |
|---|---|
| `AuthenticationError` | Wrong API key / PIN |
| `HTTPError` | Non-2xx response |
| `RemoteIsSleeping` | Device is asleep; wake it first |
| `ExternalSystemNotSupported` | Unknown external system ID |

---

## Interactive Tester

A built-in REPL tester is included:

```bash
uv run main.py
```

It discovers remotes on the local network, prompts for credentials, then
offers a numbered menu to inspect state and send commands.

---

## Development

```bash
# Run tests
uv run pytest

# Type checking
uv run mypy unfurled/

# Lint / format
uv run ruff check unfurled/
uv run ruff format unfurled/
```

---

## Licence

MIT
