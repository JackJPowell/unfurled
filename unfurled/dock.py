"""Dock domain class."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .api import CoreAPI
from .helpers.exceptions import HTTPError
from .helpers.models import DockCommand
from .helpers.websocket import DockWebSocketClient

if TYPE_CHECKING:
    from .helpers.websocket import MessageCallback

_LOGGER = logging.getLogger(__name__)

_SIMULATOR_NAMES = {"Remote Two Simulator", "Remote 3 Simulator"}


@dataclass
class DockUpdateInfo:
    """Firmware update state for a Dock, from ``GET /docks/{id}/update``."""

    in_progress: bool = False
    update_percent: int = 0
    available: list[dict] = field(default_factory=list)
    latest_version: str = ""
    release_notes_url: str = ""
    release_notes: str = ""
    check_for_updates: bool = False
    auto_update: bool = False


class Dock:
    """Represents an Unfolded Circle dock device.

    A ``Dock`` can be constructed directly (e.g. from ``Remote.docks``)
    or discovered independently.  It uses the remote's REST API for
    most operations and its own WebSocket for real-time events.
    """

    def __init__(
        self,
        *,
        dock_id: str,
        api_key: str,
        remote_endpoint: str,
        remote_configuration_url: str = "",
        name: str = "",
        ws_url: str = "",
        is_active: bool = False,
        model_number: str = "",
        hardware_revision: str = "",
        serial_number: str = "",
        led_brightness: int = 0,
        ethernet_led_brightness: int = 0,
        software_version: str = "",
        state: str = "",
        is_learning_active: bool = False,
    ) -> None:
        # Identity
        self._id = dock_id
        self._name = name
        self._model_number = model_number
        self._hardware_revision = hardware_revision
        self._serial_number = serial_number
        self._manufacturer = "Unfolded Circle"
        self._software_version = software_version

        # Derived from dock_id: "uc-dock-AA:BB:CC:DD:EE:FF" → MAC
        self._mac_address = dock_id.lower().removeprefix("uc-dock-")
        self._ip_address = ""
        self._host_name = ""

        # State
        self._is_active = is_active
        self._led_brightness = led_brightness
        self._ethernet_led_brightness = ethernet_led_brightness
        self._state = state
        self._is_learning_active = is_learning_active
        self._learned_code: dict = {}

        # Update state
        self._update = DockUpdateInfo()

        # Auth / connection
        self._api_key = api_key
        self._remote_configuration_url = remote_configuration_url
        self._ws_url = ws_url

        # REST layer (talks via the remote's proxied dock endpoint)
        self.api = CoreAPI(remote_endpoint, api_key=api_key)

        # Native WebSocket (direct to dock)
        self._ws_client: DockWebSocketClient | None = None
        self._ws_password: str = ""

        # IR data
        self._codesets: list[dict] = []
        self._ir_remotes: list[dict] = []

    # ------------------------------------------------------------------
    # Class method: construct from API dict
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(
        cls,
        data: dict,
        *,
        api_key: str,
        remote_endpoint: str,
        remote_configuration_url: str = "",
    ) -> Dock:
        """Create a Dock from the dict returned by ``GET /docks``."""
        return cls(
            dock_id=data.get("entity_id", ""),
            api_key=api_key,
            remote_endpoint=remote_endpoint,
            remote_configuration_url=remote_configuration_url,
            name=data.get("name", ""),
            ws_url=data.get("ws_url", ""),
            is_active=data.get("active", False),
            model_number=data.get("model_number", ""),
            hardware_revision=data.get("hardware_revision", ""),
            serial_number=data.get("serial_number", ""),
            led_brightness=data.get("led_brightness", 0),
            ethernet_led_brightness=data.get("ethernet_led_brightness", 0),
            software_version=data.get("software_version", ""),
            state=data.get("state", ""),
            is_learning_active=data.get("learning_active", False),
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        """Unique device identifier for this dock."""
        return self._id

    @property
    def name(self) -> str:
        """Human-readable dock name."""
        return self._name or "Unfolded Circle Dock"

    @property
    def model_number(self) -> str:
        """Raw model number string (e.g. ``\"UCD2\"``)."""
        return self._model_number

    @property
    def model_name(self) -> str:
        """Marketing model name derived from the model number."""
        if self._model_number == "UCD2":
            return "Dock Two"
        if self._model_number == "UCD3":
            return "Dock 3"
        return self._model_number or "Unfolded Circle Dock"

    @property
    def hardware_revision(self) -> str:
        """Hardware revision string."""
        return self._hardware_revision

    @property
    def serial_number(self) -> str:
        """Device serial number."""
        return self._serial_number

    @property
    def software_version(self) -> str:
        """Currently running firmware version."""
        return self._software_version

    @property
    def manufacturer(self) -> str:
        """Manufacturer name."""
        return self._manufacturer

    @property
    def mac_address(self) -> str:
        """Primary MAC address."""
        return self._mac_address

    @property
    def ip_address(self) -> str:
        """Current IP address."""
        return self._ip_address

    @property
    def host_name(self) -> str:
        """mDNS hostname."""
        return self._host_name

    @property
    def is_active(self) -> bool:
        """``True`` when the dock is in an active/connected state."""
        return self._is_active

    @property
    def state(self) -> str:
        """Current dock state string."""
        return self._state

    @property
    def led_brightness(self) -> int:
        """IR LED brightness level (0-100)."""
        return self._led_brightness

    @property
    def ethernet_led_brightness(self) -> int:
        """Ethernet port LED brightness level (0-100)."""
        return self._ethernet_led_brightness

    @property
    def is_learning_active(self) -> bool:
        """``True`` when an IR learning session is currently in progress."""
        return self._is_learning_active

    @property
    def learned_code(self) -> dict:
        """Most recently learned IR code (populated during a learning session)."""
        return self._learned_code

    @property
    def update_info(self) -> DockUpdateInfo:
        """Aggregated firmware update information for this dock."""
        return self._update

    @property
    def codesets(self) -> list[dict]:
        """Custom IR codesets stored on this dock."""
        return self._codesets

    @property
    def configuration_url(self) -> str:
        """URL to the dock's configuration page on the remote."""
        return self._remote_configuration_url

    @property
    def ws_url(self) -> str:
        """WebSocket URL for a direct connection to the dock."""
        return self._ws_url

    @property
    def is_connected(self) -> bool:
        """``True`` when an active WebSocket connection to the dock is open."""
        return self._ws_client is not None and self._ws_client.is_connected

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    async def connect_websocket(
        self,
        password: str,
        *,
        message_callback: MessageCallback | None = None,
        reconnect_delay: float = 10.0,
    ) -> None:
        """Open a native WebSocket connection to the dock.

        Args:
            password: The dock token / password.
            message_callback: Optional async callable(raw_message: str).
            reconnect_delay: Seconds between reconnection attempts.
        """
        if not self._ws_url:
            _LOGGER.warning("Dock %s has no ws_url - cannot open WebSocket", self._id)
            return

        self._ws_password = password
        self._ws_client = DockWebSocketClient(
            self._ws_url, password, reconnect_delay=reconnect_delay
        )
        self._ws_client.on_message(self._handle_ws_message)
        if message_callback:
            self._ws_client.on_message(message_callback)

        await self._ws_client.connect()

    async def disconnect_websocket(self) -> None:
        """Close the native WebSocket connection."""
        if self._ws_client:
            await self._ws_client.disconnect()
            self._ws_client = None

    async def _handle_ws_message(self, raw: str) -> None:
        """Update dock state from received WebSocket messages."""

        try:
            data = json.loads(raw)
        except Exception:
            return

        msg_type = data.get("type") or data.get("msg", "")

        if msg_type == "dock_state":
            state = data.get("msg_data", {}).get("state", "")
            if state:
                self._state = state

        if msg_type == "software_update":
            msg_data = data.get("msg_data", {})
            event = msg_data.get("event_type", "")
            if event == "START":
                self._update.in_progress = True
            elif event == "PROGRESS":
                progress = msg_data.get("progress", {})
                self._update.update_percent = int(progress.get("current_percent", 0))
            elif event in ("DONE", "SUCCESS"):
                self._update.in_progress = False
                self._update.update_percent = 0

        if msg_type == "ir_learn":
            self._learned_code = data.get("msg_data", {})

    # ------------------------------------------------------------------
    # REST operations
    # ------------------------------------------------------------------

    async def send_command(self, command: DockCommand, **params: object) -> dict:
        """Send a control command to the dock via the remote's API.

        Args:
            command: A :class:`~unfurled.models.DockCommand` value.
            **params: Additional parameters included in the request body.
        """
        body: dict = {"cmd": command.value, **params}
        return await self.api._post(f"docks/{self._id}/cmd", json=body)

    async def set_led_brightness(self, brightness: int) -> None:
        """Set the dock LED brightness.

        Args:
            brightness: Brightness level 0-100.
        """
        await self.send_command(DockCommand.SET_LED_BRIGHTNESS, brightness=brightness)
        self._led_brightness = brightness

    async def identify(self) -> None:
        """Flash the dock LEDs to visually identify this unit."""
        await self.send_command(DockCommand.IDENTIFY)

    async def reboot(self) -> None:
        """Reboot the dock."""
        await self.send_command(DockCommand.REBOOT)

    async def get_info(self) -> dict:
        """Fetch detailed device information from the remote and update local state.

        Returns:
            Raw device info dict from ``GET /docks/devices/{id}``.
        """
        info = await self.api.get_dock_detail(self._id)
        self._name = info.get("name", self._name)
        self._ws_url = info.get("resolved_ws_url", self._ws_url)
        self._is_active = bool(info.get("active", self._is_active))
        self._model_number = info.get("model", self._model_number)
        self._hardware_revision = info.get("revision", self._hardware_revision)
        self._serial_number = info.get("serial", self._serial_number)
        self._led_brightness = int(info.get("led_brightness", self._led_brightness))
        self._ethernet_led_brightness = int(
            info.get("eth_led_brightness", self._ethernet_led_brightness)
        )
        self._software_version = info.get("version", self._software_version)
        self._state = info.get("state", self._state)
        self._is_learning_active = bool(info.get("learning_active", self._is_learning_active))
        return info

    async def get_update_status(self) -> dict:
        """Fetch firmware update status and update local state.

        Returns:
            Raw update status dict from ``GET /docks/devices/{id}/update``.
        """
        info = await self.api.get_dock_update_status(self._id)
        self._update.latest_version = info.get("version", "")
        self._update.available = info.get("update_available", [])
        self._update.check_for_updates = bool(info.get("update_check_enabled", False))
        return info

    async def update_firmware(self) -> dict:
        """Trigger a firmware update for the dock.

        Returns:
            Response dict which includes a ``state`` key.  The state may be
            ``"DOWNLOADING"``, ``"NO_BATTERY"``, or the response from the
            firmware update endpoint on success.
        """
        try:
            info = await self.api.post_dock_update(self._id)
            self._update.in_progress = True
            return info
        except HTTPError as exc:
            if exc.status_code == 409:
                return {"state": "DOWNLOADING"}
            if exc.status_code == 503:
                return {"state": "NO_BATTERY"}
            raise

    async def validate_connection(self) -> bool:
        """Check that the dock is reachable via the remote proxy.

        Returns:
            ``True`` if the dock device info can be retrieved successfully.
        """
        try:
            await self.api.get_dock_detail(self._id)
            return True
        except Exception:
            return False

    async def start_ir_learning(self) -> dict:
        """Start an IR learning session on this dock.

        Returns:
            Response dict from ``PUT /ir/emitters/{id}/learn``.
        """
        result = await self.api.put_ir_emitter_learn(self._id)
        self._is_learning_active = True
        return result

    async def stop_ir_learning(self) -> None:
        """Stop an active IR learning session on this dock."""
        await self.api.delete_ir_emitter_learn(self._id)
        self._is_learning_active = False

    async def get_remotes(self) -> list[dict]:
        """Return enabled IR remote definitions stored on the remote.

        Populates :attr:`_ir_remotes` with dicts containing ``name`` and
        ``entity_id`` for each enabled remote.

        Returns:
            List of ``{"name": ..., "entity_id": ...}`` dicts.
        """
        self._ir_remotes = []
        raw = await self.api.get_remotes()
        for remote in raw:
            if remote.get("enabled"):
                name_field = remote.get("name", {})
                name = name_field.get("en") if isinstance(name_field, dict) else str(name_field)
                self._ir_remotes.append({"name": name, "entity_id": remote.get("entity_id")})
        return self._ir_remotes

    async def get_remotes_complete(self) -> list[dict]:
        """Return full IR remote definitions (including codeset data).

        Fetches the list of remotes via :meth:`get_remotes` then retrieves
        detailed info for each one.

        Returns:
            List of full remote definition dicts.
        """
        if not self._ir_remotes:
            await self.get_remotes()

        complete: list[dict] = []
        for remote in self._ir_remotes:
            entity_id = remote.get("entity_id", "")
            if entity_id:
                info = await self.api.get_remote(entity_id)
                complete.append(info)
        return complete

    async def get_custom_codesets(self) -> list[dict]:
        """Return user-defined custom IR codesets from the remote.

        Populates :attr:`_codesets` and returns the raw list.
        """
        self._codesets = await self.api.get_ir_custom_codes()
        return self._codesets

    async def delete_custom_codeset(self, codeset_device_id: str) -> None:
        """Delete a custom IR codeset.

        Args:
            codeset_device_id: The device ID of the codeset to delete.
        """
        await self.api.delete_ir_custom_code(codeset_device_id)

    async def create_remote(
        self,
        name: str,
        device: str,
        description: str,
        icon: str = "uc:movie",
    ) -> dict:
        """Create a new IR remote definition on the remote.

        Args:
            name: Human-readable name for the remote.
            device: Device name for the custom codeset.
            description: Short description of the remote.
            icon: Icon identifier (default ``"uc:movie"``).

        Returns:
            Newly created remote definition dict.
        """
        body = {
            "name": {"en": name},
            "icon": icon,
            "description": {"en": description},
            "custom_codeset": {
                "manufacturer_id": "custom",
                "device_name": device,
                "device_type": "various",
            },
        }
        return await self.api.post_remote(body)

    async def add_remote_command_to_codeset(
        self,
        remote_entity_id: str,
        command_id: str,
        value: str,
        ir_format: str,
        *,
        update_if_exists: bool = True,
    ) -> dict:
        """Add an IR command to an existing remote codeset.

        If the command already exists and *update_if_exists* is ``True``,
        the command is updated instead of raising an error.

        Args:
            remote_entity_id: The remote entity ID.
            command_id: The command identifier.
            value: The IR code value string.
            ir_format: Format string (e.g. ``"HEX"`` or ``"PRONTO"``).
            update_if_exists: Whether to update if the command already exists.

        Returns:
            The added or updated command dict.
        """
        body = {"value": value, "format": ir_format}
        try:
            return await self.api.post_remote_ir_command(remote_entity_id, command_id, body)
        except HTTPError as exc:
            if exc.status_code == 422 and update_if_exists:
                return await self.update_remote_command_in_codeset(
                    remote_entity_id, command_id, value, ir_format
                )
            raise

    async def update_remote_command_in_codeset(
        self,
        remote_entity_id: str,
        command_id: str,
        value: str,
        ir_format: str,
    ) -> dict:
        """Update an existing IR command in a remote codeset.

        Args:
            remote_entity_id: The remote entity ID.
            command_id: The command identifier.
            value: The new IR code value string.
            ir_format: Format string (e.g. ``"HEX"`` or ``"PRONTO"``).

        Returns:
            The updated command dict.
        """
        body = {"value": value, "format": ir_format}
        return await self.api.patch_remote_ir_command(remote_entity_id, command_id, body)

    async def update(self) -> None:
        """Refresh dock state by fetching info and update status."""
        try:
            await self.get_info()
        except Exception as exc:
            _LOGGER.debug("Dock.update get_info error: %s", exc)
        try:
            await self.get_update_status()
        except Exception as exc:
            _LOGGER.debug("Dock.update get_update_status error: %s", exc)

    async def close(self) -> None:
        """Release all resources held by this dock."""
        await self.disconnect_websocket()
        await self.api.close()
