"""Remote – the primary entry point for interacting with an Unfolded Circle remote."""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlparse

import aiohttp
from packaging.version import Version
from wakeonlan import send_magic_packet

from unfurled.api import CoreAPI
from unfurled.dock import Dock
from unfurled.entities.activity import Activity, ActivityGroup
from unfurled.entities.ir import IR, IRCodeset, IREmitter
from unfurled.entities.media_player import MediaPlayerEntity
from unfurled.helpers.exceptions import (
    AuthenticationError,
    HTTPError,
    InvalidButtonCommand,
    NoActivityRunning,
    RemoteIsSleeping,
    SystemCommandNotFound,
)
from unfurled.helpers.models import (
    ActivityEntityLinkEvent,
    ActivityStateEvent,
    AmbientLightEvent,
    BatteryEvent,
    ConfigurationChangeEvent,
    DeviceIdentity,
    Feature,
    IRLearningEvent,
    LocalizationInfo,
    MediaPlayerAttributesEvent,
    PowerMode,
    PowerModeEvent,
    RemoteFeatureFlags,
    RemoteState,
    RemoteStats,
    SoftwareUpdateEvent,
    SystemInfo,
    UpdateInfo,
    UpdateType,
    parse_ws_message,
)
from unfurled.helpers.websocket import RemoteWebSocketClient
from unfurled.submodules.authentication import Authentication
from unfurled.submodules.integrations import Integrations
from unfurled.submodules.settings import Settings

_LOGGER = logging.getLogger(__name__)

_SIMULATOR_MAC = "aa:bb:cc:dd:ee:ff"
_SIMULATOR_NAMES = {"Remote Two Simulator", "Remote 3 Simulator"}
_SYSTEM_COMMANDS = frozenset(
    {"STANDBY", "REBOOT", "POWER_OFF", "RESTART", "RESTART_UI", "RESTART_CORE"}
)


class Remote:
    """High-level API for an Unfolded Circle remote device.

    Manages REST calls via :class:`~unfurled.api.CoreAPI` and receives
    real-time pushes via a :class:`~unfurled.websocket.RemoteWebSocketClient`.

    Typical usage::

        remote = Remote("http://192.168.1.10/api/", api_key="mykey")
        await remote.init()          # populate all state
        await remote.connect_websocket()  # keep state in sync via WS

        print(remote.battery_level)
        await remote.activities[0].turn_on()

        await remote.close()         # clean up sessions / sockets
    """

    def __init__(
        self,
        api_url: str,
        *,
        pin: str | None = None,
        api_key: str | None = None,
        session: aiohttp.ClientSession | None = None,
        wake_if_asleep: bool = True,
        wake_on_lan_retries: int = 3,
    ) -> None:
        self.endpoint = self._normalize_url(api_url)
        self.configuration_url = self._derive_config_url(self.endpoint)

        self.api = CoreAPI(self.endpoint, api_key=api_key, pin=pin, session=session)

        self._api_key = api_key
        self._pin = pin

        # Device identity
        self.info = SystemInfo()
        self.identity = DeviceIdentity()

        # Real-time state (battery, ambient light, power mode, …)
        self.state = RemoteState()

        # Feature flags (capability detection after init)
        self.flags = RemoteFeatureFlags()

        # Settings  (all sub-sections live here, populated from GET /cfg)
        self.settings = Settings(self)

        # System resource stats (memory, storage, CPU load)
        self.stats = RemoteStats()

        # Update info
        self.update_info = UpdateInfo()

        # Authentication
        self.auth = Authentication(self)

        # Integrations
        self.integrations = Integrations(self)

        # IR control
        self.ir = IR(self)

        # Collections
        self.activities: list[Activity] = []
        self.activity_groups: list[ActivityGroup] = []
        self.docks: list[Dock] = []
        self.ir_emitters: list[IREmitter] = []
        self.ir_codesets: list[IRCodeset] = []

        # Wake-on-LAN
        self._wake_if_asleep = wake_if_asleep
        self._wake_on_lan_retries = wake_on_lan_retries

        # WebSocket
        self._ws_client: RemoteWebSocketClient | None = None
        self._last_update_type: UpdateType = UpdateType.NONE

        # Entities cache (media players referenced by activities)
        self._entities: dict[str, MediaPlayerEntity] = {}

    # ------------------------------------------------------------------
    # URL helpers (static / class)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Ensure the URL has a scheme and ends in ``/api/``."""
        if not re.match(r"^https?://", url):
            url = "http://" + url
        parsed = urlparse(url)
        if parsed.path in ("", "/"):
            url = f"{parsed.scheme}://{parsed.netloc}/api/"
        elif not parsed.path.endswith("/"):
            url = url + "/"
        return url

    @staticmethod
    def _derive_config_url(endpoint: str) -> str:
        parsed = urlparse(endpoint)
        return f"{parsed.scheme}://{parsed.netloc}/configurator/"

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        """Human-friendly name for the remote, if available; otherwise a generic fallback."""
        return self.identity.name or f"Unfolded Circle {self.info.model_name}"

    @property
    def sw_version(self) -> str:
        """Software version of the remote, if available."""
        return self.identity.sw_version or "N/A"

    @property
    def last_update_type(self) -> UpdateType:
        """Type of the last update received from the remote."""
        return self._last_update_type

    @property
    def memory_available(self) -> int:
        """Available memory on the remote."""
        return int(round(self.stats.memory_available))

    @property
    def storage_available(self) -> int:
        """Available storage on the remote."""
        return int(round(self.stats.storage_available))

    @property
    def localization(self) -> LocalizationInfo:
        """Current localization configuration."""
        return self.settings.localization

    @property
    def wake_on_lan_retries(self) -> int:
        """Number of WoL retries when attempting to wake the remote."""
        return self._wake_on_lan_retries

    @wake_on_lan_retries.setter
    def wake_on_lan_retries(self, value: int) -> None:
        self._wake_on_lan_retries = value

    @property
    def internal_ir_enabled(self) -> bool:
        """Return ``True`` if the remote's built-in IR emitter is enabled."""
        return any(f.id == "internal_ir" and f.enabled for f in self.settings.features)

    # ------------------------------------------------------------------
    # Device info (proxied from SystemInfo)
    # ------------------------------------------------------------------

    @property
    def memory_total(self) -> int:
        """Total RAM in MiB."""
        return int(round(self.stats.memory_total))

    @property
    def storage_total(self) -> int:
        """Total user-data storage in MiB."""
        return int(round(self.stats.storage_total))

    # ------------------------------------------------------------------
    # Wake-on-LAN helpers
    # ------------------------------------------------------------------

    async def _ensure_awake(self) -> None:
        """Wake the remote if WoL is configured and it may be asleep."""
        if (
            self._wake_if_asleep
            and self.settings.network.wifi.wake_on_wlan
            and not await self.wake()
        ):
            raise RemoteIsSleeping

    async def wake(self, *, wait: bool = True) -> bool:
        """Send a magic packet and optionally wait for the remote to respond."""
        if self.identity.is_simulator:
            return True
        return await Remote.wake_by_mac(
            self.identity.mac_address,
            self.endpoint,
            wait_for_confirmation=wait,
            retries=self._wake_on_lan_retries,
        )

    @classmethod
    async def wake_by_mac(
        cls,
        mac_address: str,
        api_url: str,
        *,
        wait_for_confirmation: bool = True,
        retries: int = 3,
    ) -> bool:
        """Send a WoL magic packet and optionally verify the device is awake."""
        validated_url = cls._normalize_url(api_url)
        send_magic_packet(mac_address)
        if not wait_for_confirmation:
            return True
        from urllib.parse import urljoin

        status_url = urljoin(validated_url, "pub/status")
        for _ in range(retries):
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(status_url, timeout=aiohttp.ClientTimeout(total=2)) as r:
                        if r.status == 200:
                            return True
            except Exception:
                pass
            await asyncio.sleep(1)
        return False

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    async def connect_websocket(self, *, reconnect_delay: float = 10.0) -> None:
        """Start a WebSocket connection and keep it alive automatically.

        WebSocket messages update the remote's state in real time.
        """
        if not self._api_key:
            _LOGGER.warning("No API key – WebSocket requires an API key")
            return

        self._ws_client = RemoteWebSocketClient(
            self.endpoint, self._api_key, reconnect_delay=reconnect_delay
        )
        self._ws_client.on_message(self._handle_ws_message)
        self._ws_client.on_connect(self._on_ws_reconnect)
        await self._ws_client.connect()

    async def disconnect_websocket(self) -> None:
        """Close the WebSocket connection."""
        if self._ws_client:
            await self._ws_client.disconnect()
            self._ws_client = None

    async def _on_ws_reconnect(self) -> None:
        _LOGGER.debug("Remote WS reconnected – refreshing state")
        await self.update()

    async def _handle_ws_message(self, raw: str) -> None:
        """Dispatch a raw WebSocket message to the appropriate handler."""
        event = parse_ws_message(raw)
        if event is None:
            return

        match event:
            case BatteryEvent():
                self._on_battery(event)
            case AmbientLightEvent():
                self._on_ambient_light(event)
            case ActivityStateEvent():
                self._on_activity_state(event)
            case ActivityEntityLinkEvent():
                self._on_activity_entity_link(event)
            case MediaPlayerAttributesEvent():
                self._on_media_player_attrs(event)
            case SoftwareUpdateEvent():
                self._on_software_update(event)
            case ConfigurationChangeEvent():
                self._on_configuration_change(event)
            case PowerModeEvent():
                self._on_power_mode(event)
            case IRLearningEvent():
                self._on_ir_learning(event)

    # WS event handlers (private, synchronous)

    def _on_battery(self, event: BatteryEvent) -> None:
        _LOGGER.debug("WS battery: cap=%s status=%s", event.capacity, event.status)
        self.state.battery_level = event.capacity
        self.state.battery_status = event.status
        self.state.is_charging = event.power_supply
        self._last_update_type = UpdateType.BATTERY

    def _on_ambient_light(self, event: AmbientLightEvent) -> None:
        self.state.ambient_light_level = event.intensity
        self._last_update_type = UpdateType.AMBIENT_LIGHT

    def _on_activity_state(self, event: ActivityStateEvent) -> None:
        _LOGGER.debug("WS activity %s → %s", event.entity_id, event.state)
        for activity in self.activities:
            if activity.id == event.entity_id:
                activity._set_state(event.state)
                if event.included_entities:
                    self._apply_included_entities(activity, event.included_entities)
        for group in self.activity_groups:
            if group.contains(event.entity_id):
                group._recalculate_state()
        self._last_update_type = UpdateType.ACTIVITY

    def _on_activity_entity_link(self, event: ActivityEntityLinkEvent) -> None:
        _LOGGER.debug(
            "WS entity link: activity=%s entity=%s",
            event.activity_id,
            event.entity_id,
        )
        for activity in self.activities:
            if activity.id == event.activity_id:
                self._apply_included_entities(activity, [event.entity_data])
        self._last_update_type = UpdateType.ACTIVITY

    def _on_media_player_attrs(self, event: MediaPlayerAttributesEvent) -> None:
        entity = self._entities.get(event.entity_id)
        if entity:
            entity.update_attributes(event.attributes)
            self._last_update_type = UpdateType.MEDIA_PLAYER

    def _on_software_update(self, event: SoftwareUpdateEvent) -> None:
        event_type = event.event_type
        progress = event.progress

        if event_type == "START":
            self.update_info.in_progress = True

        elif event_type == "PROGRESS":
            state = progress.get("state", "")
            total_steps = progress.get("total_steps", 1) or 1
            offset = round(100 / total_steps)
            pct_offset = offset / 100

            match state:
                case "START" | "RUN":
                    self.update_info.update_percent = 0
                case "PROGRESS":
                    step = progress.get("current_step", 1)
                    step_offset = offset * (step - 1)
                    self.update_info.update_percent = int(
                        pct_offset * progress.get("current_percent", 0) + step_offset
                    )
                case "SUCCESS":
                    self.update_info.update_percent = 100
                    self.identity.sw_version = self.update_info.latest_version
                case "DONE":
                    self.update_info.in_progress = False
                    self.update_info.update_percent = 0
                    self.update_info.download_percent = 0
                    self.identity.sw_version = self.update_info.latest_version
                case "DOWNLOAD":
                    self.update_info.download_percent = int(progress.get("download_percent", 0))
                case _:
                    self.update_info.in_progress = False
                    self.update_info.update_percent = 0

        self._last_update_type = UpdateType.SOFTWARE

    def _on_configuration_change(self, event: ConfigurationChangeEvent) -> None:
        state = event.new_state

        if display := state.get("display"):
            self.settings.display.auto_brightness = display.get(
                "auto_brightness", self.settings.display.auto_brightness
            )
            self.settings.display.brightness = display.get(
                "brightness", self.settings.display.brightness
            )

        if button := state.get("button"):
            self.settings.button.auto_brightness = button.get(
                "auto_brightness", self.settings.button.auto_brightness
            )
            self.settings.button.brightness = button.get(
                "brightness", self.settings.button.brightness
            )
            if "RGB_COLOR" in self.flags.button_features:
                self.settings.button.static_color = button.get("static_color")

        if sound := state.get("sound"):
            self.settings.sound.enabled = sound.get("enabled", self.settings.sound.enabled)
            self.settings.sound.volume = sound.get("volume", self.settings.sound.volume)

        if haptic := state.get("haptic"):
            self.settings.haptic.enabled = haptic.get("enabled", self.settings.haptic.enabled)

        if sw := state.get("software_update"):
            self.settings.software_update.check_for_updates = sw.get(
                "check_for_updates", self.settings.software_update.check_for_updates
            )
            self.settings.software_update.auto_update = sw.get(
                "auto_update", self.settings.software_update.auto_update
            )
            self.settings.software_update.ota_window_start = sw.get(
                "ota_window_start", self.settings.software_update.ota_window_start
            )
            self.settings.software_update.ota_window_end = sw.get(
                "ota_window_end", self.settings.software_update.ota_window_end
            )
            self.settings.software_update.channel = sw.get(
                "channel", self.settings.software_update.channel
            )

        if ps := state.get("power_saving"):
            self.settings.power_saving.display_off_sec = ps.get(
                "display_off_sec", self.settings.power_saving.display_off_sec
            )
            self.settings.power_saving.wakeup_sensitivity = ps.get(
                "wakeup_sensitivity", self.settings.power_saving.wakeup_sensitivity
            )
            self.settings.power_saving.standby_sec = ps.get(
                "standby_sec", self.settings.power_saving.standby_sec
            )

        if net := state.get("network"):
            self.settings.network.bt_enabled = bool(
                net.get("bt_enabled", self.settings.network.bt_enabled)
            )
            self.settings.network.wifi_enabled = bool(
                net.get("wifi_enabled", self.settings.network.wifi_enabled)
            )
            wifi = net.get("wifi", {})
            if wifi:
                self.settings.network.wifi.band = wifi.get("band", self.settings.network.wifi.band)
                self.settings.network.wifi.scan_interval_sec = wifi.get(
                    "scan_interval_sec", self.settings.network.wifi.scan_interval_sec
                )
                self.settings.network.wifi.ipv4_type = wifi.get(
                    "ipv4_type", self.settings.network.wifi.ipv4_type
                )
            wol = wifi.get("wake_on_wlan") or net.get("wake_on_wlan") or {}
            if wol:
                self.settings.network.wifi.wake_on_wlan = bool(
                    wol.get("enabled", self.settings.network.wifi.wake_on_wlan)
                )

        if loc := state.get("localization"):
            self.settings.localization.language_code = loc.get(
                "language_code", self.settings.localization.language_code
            )
            self.settings.localization.country_code = loc.get(
                "country_code", self.settings.localization.country_code
            )
            self.settings.localization.time_zone = loc.get(
                "time_zone", self.settings.localization.time_zone
            )
            self.settings.localization.time_format_24h = bool(
                loc.get("time_format_24h", self.settings.localization.time_format_24h)
            )
            self.settings.localization.measurement_unit = loc.get(
                "measurement_unit", self.settings.localization.measurement_unit
            )

        if bt := state.get("bt"):
            self.settings.bluetooth.peripheral_connections = bt.get(
                "peripheral_connections", self.settings.bluetooth.peripheral_connections
            )
            self.settings.bluetooth.advertisement_name = bt.get(
                "advertisement_name", self.settings.bluetooth.advertisement_name
            )
            self.settings.bluetooth.enable_hci_log = bool(
                bt.get("enable_hci_log", self.settings.bluetooth.enable_hci_log)
            )
            self.settings.bluetooth.enable_debug_port = bool(
                bt.get("enable_debug_port", self.settings.bluetooth.enable_debug_port)
            )
            self.settings.bluetooth.version = bt.get("version", self.settings.bluetooth.version)

        if device := state.get("device"):
            self.identity.name = device.get("name", "")

        if profile := state.get("profile"):
            self.settings.profile.has_admin_pin = bool(
                profile.get("has_admin_pin", self.settings.profile.has_admin_pin)
            )

        if voice := state.get("voice"):
            self.settings.voice.microphone = bool(
                voice.get("microphone", self.settings.voice.microphone)
            )
            self.settings.voice.voice_assistant = voice.get(
                "voice_assistant", self.settings.voice.voice_assistant
            )

        if features := state.get("features"):
            self.settings.features = [
                Feature(
                    id=f.get("id", ""),
                    enabled=bool(f.get("enabled", False)),
                    title=f.get("title", {}),
                    description=f.get("description", {}),
                    help_url=f.get("help_url", ""),
                )
                for f in features
            ]

        self._last_update_type = UpdateType.CONFIGURATION

    def _on_power_mode(self, event: PowerModeEvent) -> None:
        self.state.power_mode = event.mode
        self._last_update_type = UpdateType.CONFIGURATION

    def _on_ir_learning(self, event: IRLearningEvent) -> None:
        dock = self.find_dock(event.device_id)
        if dock:
            dock._learned_code = event.code

    # ------------------------------------------------------------------
    # Initialization & updates
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Fetch all device state concurrently.

        This is the primary way to populate a freshly created ``Remote``.
        After calling ``init()``, all properties and collections are set.
        """
        _LOGGER.debug("Remote init starting for %s", self.endpoint)

        # First pass – run independent fetches concurrently
        tasks = [
            self._fetch_version(),
            self._fetch_system_info(),
            self._fetch_wifi_info(),
            self._fetch_configuration(),
            self._fetch_battery(),
            self._fetch_power(),
            self._fetch_charger(),
            self._fetch_ambient_light(),
            self._fetch_stats(),
            self._fetch_update_info(),
            self._fetch_ir_emitters(),
            self._fetch_docks(),
            self._fetch_ir_codesets(),
        ]

        for coro in asyncio.as_completed(tasks):
            try:
                await coro
            except Exception as exc:
                _LOGGER.debug("Remote init task error: %s", exc)

        # Activities must be loaded before groups
        try:
            await self._fetch_activities()
        except Exception as exc:
            _LOGGER.error("Remote init: failed to fetch activities: %s", exc)

        try:
            await self._fetch_activity_groups()
        except Exception as exc:
            _LOGGER.debug("Remote init: failed to fetch activity groups: %s", exc)

        _LOGGER.debug("Remote init complete for %s", self.endpoint)

    async def update(self) -> None:
        """Refresh volatile state (battery, stats, settings, activity states)."""
        tasks = [
            self._fetch_battery(),
            self._fetch_ambient_light(),
            self._fetch_stats(),
            self._fetch_configuration(),
            self._fetch_update_info(),
            self._fetch_charger(),
            self._fetch_activities_state(),
        ]
        for coro in asyncio.as_completed(tasks):
            try:
                await coro
            except Exception as exc:
                _LOGGER.debug("Remote update task error: %s", exc)

    async def _refresh_core_state(self) -> None:
        """Lightweight refresh after a WS reconnect."""
        try:
            await asyncio.gather(
                self._fetch_battery(),
                self._fetch_activities_state(),
                return_exceptions=True,
            )
        except Exception as exc:
            _LOGGER.debug("WS reconnect refresh error: %s", exc)

    # ------------------------------------------------------------------
    # Internal fetch helpers
    # ------------------------------------------------------------------

    async def _fetch_version(self) -> None:
        data = await self.api.get_pub_version()
        self.identity.hostname = data.get("hostname", "")
        self.identity.mac_address = data.get("address", "")
        if not self.identity.mac_address:
            # Simulator path: get from /system
            await self._fetch_system_info()
        if self.identity.is_simulator is not True:
            self.identity.sw_version = data.get("os", "")
            v = Version(self.identity.sw_version) if self.identity.sw_version else None
            if v:
                self.flags.external_entity_configuration_available = v >= Version("2.0.0")
                self.flags.new_web_configurator = v >= Version("2.2.0")

    async def _fetch_system_info(self) -> None:
        data = await self.api.get_system_info()
        self.info.model_name = data.get("model_name", "")
        self.info.model_number = data.get("model_number", "")
        self.info.serial_number = data.get("serial_number", "")
        self.info.hw_revision = data.get("hw_revision", "")
        if self.info.model_name in _SIMULATOR_NAMES:
            self.identity.is_simulator = True
            self.identity.mac_address = _SIMULATOR_MAC
            self.flags.external_entity_configuration_available = True

    async def _fetch_wifi_info(self) -> None:
        if self.identity.is_simulator:
            parsed = urlparse(self.endpoint)
            self.identity.ip_address = parsed.hostname or ""
            return
        try:
            data = await self.api.get_wifi_info()
            self.identity.mac_address = data.get("address", self.identity.mac_address)
            self.identity.ip_address = data.get("ip_address", "")
        except Exception:
            pass

    async def _fetch_configuration(self) -> None:
        """Fetch and parse the full ``GET /cfg`` response into ``self.settings``."""
        data = await self.api.get_configuration()

        # Device
        device = data.get("device", {})
        self.identity.name = device.get("name", "")

        # Display
        display = data.get("display", {})
        self.settings.display.auto_brightness = bool(display.get("auto_brightness", False))
        self.settings.display.brightness = display.get("brightness", 50)

        # Button
        button = data.get("button", {})
        self.settings.button.auto_brightness = bool(button.get("auto_brightness", False))
        self.settings.button.brightness = button.get("brightness", 50)
        self.settings.button.static_color = button.get("static_color")

        # Sound
        sound = data.get("sound", {})
        self.settings.sound.enabled = bool(sound.get("enabled", True))
        self.settings.sound.volume = sound.get("volume", 50)

        # Haptic
        haptic = data.get("haptic", {})
        self.settings.haptic.enabled = bool(haptic.get("enabled", True))

        # Power saving
        ps = data.get("power_saving", {})
        self.settings.power_saving.display_off_sec = ps.get("display_off_sec", 30)
        self.settings.power_saving.wakeup_sensitivity = ps.get("wakeup_sensitivity", 2)
        self.settings.power_saving.standby_sec = ps.get("standby_sec", 900)

        # Network
        net = data.get("network", {})
        self.settings.network.bt_enabled = bool(net.get("bt_enabled", True))
        self.settings.network.wifi_enabled = bool(net.get("wifi_enabled", True))
        wifi = net.get("wifi", {})
        self.settings.network.wifi.band = wifi.get("band", "auto")
        self.settings.network.wifi.scan_interval_sec = wifi.get("scan_interval_sec", 15)
        self.settings.network.wifi.ipv4_type = wifi.get("ipv4_type", "DHCP")
        # wake_on_wlan can appear at wifi level or network level
        wol = wifi.get("wake_on_wlan") or net.get("wake_on_wlan") or {}
        self.settings.network.wifi.wake_on_wlan = bool(wol.get("enabled", False))
        bt_net = net.get("bt", {})
        self.settings.network.bt_address = bt_net.get("address", "")

        # Software update
        sw = data.get("software_update", {})
        self.settings.software_update.check_for_updates = bool(sw.get("check_for_updates", True))
        self.settings.software_update.auto_update = bool(sw.get("auto_update", False))
        self.settings.software_update.ota_window_start = sw.get("ota_window_start", "02:00:00")
        self.settings.software_update.ota_window_end = sw.get("ota_window_end", "05:00:00")
        self.settings.software_update.channel = sw.get("channel", "STABLE")

        # Localization
        loc = data.get("localization", {})
        self.settings.localization.language_code = loc.get("language_code", "en_US")
        self.settings.localization.country_code = loc.get("country_code", "US")
        self.settings.localization.time_zone = loc.get("time_zone", "UTC")
        self.settings.localization.time_format_24h = bool(loc.get("time_format_24h", True))
        self.settings.localization.measurement_unit = loc.get("measurement_unit", "METRIC")

        # Bluetooth
        bt = data.get("bt", {})
        self.settings.bluetooth.peripheral_connections = bt.get("peripheral_connections", 1)
        self.settings.bluetooth.advertisement_name = bt.get("advertisement_name", "")
        self.settings.bluetooth.enable_hci_log = bool(bt.get("enable_hci_log", False))
        self.settings.bluetooth.enable_debug_port = bool(bt.get("enable_debug_port", False))
        self.settings.bluetooth.version = bt.get("version", "")

        # Profile
        profile = data.get("profile", {})
        self.settings.profile.has_admin_pin = bool(profile.get("has_admin_pin", False))

        # Voice
        voice = data.get("voice", {})
        self.settings.voice.microphone = bool(voice.get("microphone", False))
        self.settings.voice.voice_assistant = voice.get("voice_assistant", {})

        # Features
        self.settings.features = [
            Feature(
                id=f.get("id", ""),
                enabled=bool(f.get("enabled", False)),
                title=f.get("title", {}),
                description=f.get("description", {}),
                help_url=f.get("help_url", ""),
            )
            for f in data.get("features", [])
        ]

    async def _fetch_battery(self) -> None:
        data = await self.api.get_battery()
        self.state.battery_level = data.get("capacity", 0)
        self.state.battery_status = data.get("status", "")
        self.state.is_charging = bool(data.get("power_supply", False))

    async def _fetch_power(self) -> None:
        data = await self.api.get_power()
        self.state.power_mode = data.get("mode", PowerMode.NORMAL)

    async def _fetch_charger(self) -> None:
        data = await self.api.get_charger()
        self.flags.charging_options = data.get("features", [])
        self.state.is_wireless_charging = bool(data.get("wireless_charging", False))
        self.flags.wireless_charging_enabled = bool(data.get("wireless_charging_enabled", False))

    async def _fetch_ambient_light(self) -> None:
        data = await self.api.get_ambient_light()
        self.state.ambient_light_level = data.get("intensity", 0)

    async def _fetch_stats(self) -> None:
        data = await self.api.get_pub_status()
        mem = data.get("memory", {})
        self.stats.memory_total = mem.get("total_memory", 0) / 1048576
        self.stats.memory_available = mem.get("available_memory", 0) / 1048576
        fs = data.get("filesystem", {}).get("user_data", {})
        self.stats.storage_total = (fs.get("used", 0) + fs.get("available", 0)) / 1048576
        self.stats.storage_available = fs.get("available", 0) / 1048576
        load = data.get("load_avg", {})
        self.stats.cpu_load_one = load.get("one", 0.0)
        self.stats.cpu_load_five = load.get("five", 0.0)
        self.stats.cpu_load_fifteen = load.get("fifteen", 0.0)

    async def _fetch_update_info(self) -> None:
        try:
            if self.identity.is_simulator:
                return
            data = await self.api.get_system_update()
            self.update_info.latest_version = data.get("version", "")
            self.update_info.release_notes_url = data.get("release_notes_url", "")
            self.update_info.release_notes = data.get("release_notes", "")
            self.update_info.next_check_date = data.get("next_check_date", "")
            self.update_info.available = data.get("updates", [])
        except HTTPError:
            pass

    async def _fetch_activities(self) -> None:
        """Fetch activities and their button mappings."""
        self.activities = []
        raw = await self.api.get_activities()
        for item in raw:
            activity = Activity(item, self)
            self.activities.append(activity)

            # Fetch detailed activity data (included entities)
            try:
                detail = await self.api.get_activity(activity.id)
                included = detail.get("options", {}).get("included_entities", [])
                self._apply_included_entities(activity, included)
            except Exception:
                pass

            # Fetch button mappings
            try:
                buttons = await self.api.get_activity_buttons(activity.id)
                for btn in buttons:
                    activity._apply_button_mapping(btn.get("button", ""), btn.get("short_press"))
            except Exception:
                pass

    async def _fetch_activities_state(self) -> None:
        """Lightweight refresh of activity on/off states."""
        try:
            raw = await self.api.get_activities()
            for item in raw:
                for activity in self.activities:
                    if activity.id == item["entity_id"]:
                        activity._set_state(item["attributes"]["state"])
        except Exception as exc:
            _LOGGER.debug("_fetch_activities_state error: %s", exc)

    async def _fetch_activity_groups(self) -> None:
        self.activity_groups = []
        raw = await self.api.get_activity_groups()
        for item in raw:
            group = ActivityGroup(
                group_id=item["group_id"],
                name=self.get_text_for_locale(item.get("name", {}), default_text="Unnamed Group"),
                remote=self,
                state=item.get("state", "OFF"),
            )
            try:
                detail = await self.api.get_activity_group(item["group_id"])
                for act_ref in detail.get("activities", []):
                    for activity in self.activities:
                        if activity.id == act_ref.get("entity_id"):
                            group.activities.append(activity)
            except Exception:
                pass
            self.activity_groups.append(group)

    async def _fetch_docks(self) -> None:
        self.docks = []
        raw = await self.api.get_docks()
        for item in raw:
            dock = Dock.from_dict(
                item,
                api_key=self._api_key or "",
                remote_endpoint=self.endpoint,
                remote_configuration_url=self.configuration_url,
            )
            self.docks.append(dock)

    async def _fetch_ir_emitters(self) -> None:
        self.ir_emitters = []
        raw = await self.api.get_ir_emitters()
        for item in raw:
            self.ir_emitters.append(IREmitter(item, self))

    async def _fetch_ir_codesets(self) -> None:
        """Fetch codesets for all registered IR remotes."""
        self.ir_codesets = []
        try:
            remotes = await self.api.get_remotes()
            for remote in remotes:
                try:
                    raw = await self.api.get_remote_ir_codesets(remote.get("entity_id", ""))
                    self.ir_codesets.extend(IRCodeset.from_dict(c) for c in raw)
                except Exception:
                    pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Entity helpers
    # ------------------------------------------------------------------

    def _get_media_player(self, entity_id: str) -> MediaPlayerEntity:
        """Return the cached :class:`~unfurled.media_player.MediaPlayerEntity` or create one."""
        if entity_id not in self._entities:
            self._entities[entity_id] = MediaPlayerEntity(entity_id, self)
        return self._entities[entity_id]

    def _apply_included_entities(self, activity: Activity, included_entities: list[dict]) -> None:
        """Register media player entities from an activity's included entity list."""
        activity._included_entities = included_entities
        for entity_info in included_entities:
            if entity_info.get("entity_type") != "media_player":
                continue
            eid = entity_info.get("entity_id", "")
            if not eid:
                continue
            entity = self._get_media_player(eid)
            entity._name = self.get_text_for_locale(entity_info.get("name", {}), default_text=eid)
            entity._entity_commands = entity_info.get("entity_commands", [])
            entity._activity = activity
            activity.add_media_player_entity(entity)

    def find_activity(self, activity_id: str) -> Activity | None:
        """Return the activity with the given ID, or ``None`` if not found."""
        return next((a for a in self.activities if a.id == activity_id), None)

    def find_dock(self, dock_id: str) -> Dock | None:
        """Return the dock with the given ID, or ``None`` if not found."""
        return next((d for d in self.docks if d.id == dock_id), None)

    def get_all_entities_in_use(self, integration_id_filter: str = "") -> list[str]:
        """Return entity IDs referenced by any loaded activity.

        Reads from the already-loaded activity list; does not make an API call.

        Args:
            integration_id_filter: If non-empty, only return IDs that start
                with this string (e.g. ``"hass."`` to scope to one integration).
        """
        entity_ids: list[str] = []
        for activity in self.activities:
            for entity in activity.included_entities:
                eid = entity.get("entity_id", "")
                if integration_id_filter and not eid.startswith(integration_id_filter):
                    continue
                if eid and eid not in entity_ids:
                    entity_ids.append(eid)
        return entity_ids

    # ------------------------------------------------------------------
    # Locale helper
    # ------------------------------------------------------------------

    def get_text_for_locale(
        self,
        text: dict | str | None,
        *,
        locale: str | None = None,
        default_text: str = "Undefined",
    ) -> str:
        """Return the best match for the current locale from a text dict."""
        if not text:
            return default_text
        if isinstance(text, str):
            return text

        locale = locale or self.settings.localization.language_code

        for candidate in (locale, locale.split("_")[0] if "_" in locale else None, "en_US", "en"):
            if candidate and text.get(candidate):
                return text[candidate]

        for v in text.values():
            if v:
                return v

        return default_text

    # ------------------------------------------------------------------
    # Activity operations
    # ------------------------------------------------------------------

    async def get_active_activities(self) -> list[Activity]:
        """Return all currently active (ON) activities."""
        await self._fetch_activities_state()
        return [a for a in self.activities if a.is_on]

    async def send_button_command(
        self,
        button: str,
        *,
        activity: str | None = None,
        hold: bool = False,
        repeat: int = 1,
    ) -> None:
        """Send a predefined physical button command.

        Args:
            button: Button identifier (e.g. ``"VOLUME_UP"``).
            activity: Optional activity name to scope the command to.
            hold: Use the long-press mapping instead of short-press.
            repeat: Number of times to send the command.
        """
        await self._ensure_awake()

        activity_id: str | None = None
        if activity:
            act_obj = next((a for a in self.activities if a.name == activity), None)
            if act_obj:
                activity_id = act_obj.id
        else:
            active = [a for a in self.activities if a.is_on]
            if active:
                activity_id = active[0].id

        if not activity_id:
            raise NoActivityRunning

        try:
            btn_data = await self.api.get_activity_button(activity_id, button.upper())
        except HTTPError as exc:
            raise InvalidButtonCommand(str(exc)) from exc

        action = btn_data.get("long_press" if hold else "short_press", {})
        entity_id = action.get("entity_id", "")
        cmd_id = action.get("cmd_id", "")
        params = action.get("params")

        for _ in range(repeat):
            await self.api.put_entity_command(entity_id, cmd_id, params)

    # ------------------------------------------------------------------
    # System operations
    # ------------------------------------------------------------------

    async def post_system_command(self, cmd: str) -> None:
        """Send a system command (STANDBY, REBOOT, etc.)."""
        if cmd not in _SYSTEM_COMMANDS:
            raise SystemCommandNotFound(cmd)
        await self._ensure_awake()
        await self.api.post_system_command(cmd)

    async def get_update_status(self) -> dict:
        """Return the latest software update status."""
        return await self.api.get_system_update_latest()

    async def update_firmware(self, *, download_only: bool = False) -> str:
        """Trigger a firmware update.

        If *download_only* is ``True``, only downloads the update (does not
        install) – the update must be in ``PENDING`` or ``ERROR`` state first.
        """
        if download_only:
            status = await self.get_update_status()
            if status.get("state") not in ("PENDING", "ERROR"):
                return status.get("state", "UNKNOWN")

        data = await self.api.post_system_update_latest()
        return data.get("state", "UNKNOWN") if data else "OK"

    # ------------------------------------------------------------------
    # Settings operations
    # ------------------------------------------------------------------

    async def set_wireless_charging(self, *, enabled: bool) -> None:
        """Enable or disable wireless charging (if supported)."""
        await self._ensure_awake()
        await self.api.put_wireless_charging(enabled)
        self.flags.wireless_charging_enabled = enabled

    # ------------------------------------------------------------------
    # Standby inhibitors
    # ------------------------------------------------------------------

    async def refresh_standby_inhibitors(self) -> list[dict]:
        """Refresh the list of standby inhibitors."""
        await self._ensure_awake()
        self.state.standby_inhibitors = await self.api.get_standby_inhibitors()
        return self.state.standby_inhibitors

    async def set_standby_inhibitor(
        self, inhibitor_id: str, who: str, why: str, delay: int = 0
    ) -> None:
        """Set a standby inhibitor."""
        await self._ensure_awake()
        body: dict = {"id": inhibitor_id, "who": who, "why": why}
        if delay:
            body["delay"] = delay
        await self.api.post_standby_inhibitor(body)

    async def remove_standby_inhibitor(self, inhibitor_id: str) -> None:
        """Remove a standby inhibitor by ID."""
        await self._ensure_awake()
        await self.api.delete_standby_inhibitor(inhibitor_id)

    async def remove_all_standby_inhibitors(self) -> None:
        """Remove all standby inhibitors."""
        await self._ensure_awake()
        await self.api.delete_all_standby_inhibitors()

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    async def validate_connection(self) -> bool:
        """Check that the remote is reachable and credentials are valid.

        Returns:
            ``True`` if a HEAD request to ``/activities`` returns 200.

        Raises:
            :class:`~unfurled.exceptions.AuthenticationError`: on 401.
            :class:`~unfurled.exceptions.HTTPError`: on other non-200 responses.
        """
        session = await self.api._ensure_session()
        url = self.api._url("activities")
        async with session.head(url) as response:
            if response.status == 401:
                raise AuthenticationError("Invalid API key or PIN")
            return response.status == 200

    # ------------------------------------------------------------------
    # Lightweight polling refresh
    # ------------------------------------------------------------------

    async def polling_update(self) -> None:
        """Fetch only lightweight stats suitable for frequent polling.

        Refreshes CPU load, memory, and storage without touching settings
        or activity state (use :meth:`update` for a full refresh).
        """
        try:
            await self._fetch_stats()
        except Exception as exc:
            _LOGGER.debug("polling_update error: %s", exc)

    # ------------------------------------------------------------------
    # Firmware update – force check
    # ------------------------------------------------------------------

    async def force_update_check(self) -> dict:
        """Force the remote to check for firmware updates immediately.

        Returns:
            Update information dict from the remote.
        """
        return await self.api.put_system_update()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Release all resources: WebSocket, HTTP session, dock sessions."""
        await self.disconnect_websocket()
        await self.api.close()
        for dock in self.docks:
            await dock.close()

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Remote:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
