"""Settings sub-object – configuration for the remote device."""

from __future__ import annotations

from typing import TYPE_CHECKING

from packaging.version import Version

from ..helpers.models import (
    BluetoothSettings,
    ButtonSettings,
    DisplaySettings,
    Feature,
    HapticSettings,
    LocalizationInfo,
    NetworkSettings,
    PowerSavingSettings,
    ProfileSettings,
    SoftwareUpdateSettings,
    SoundSettings,
    VoiceSettings,
)
from .base import RemoteModule

if TYPE_CHECKING:
    from ..remote import Remote


class Settings(RemoteModule):
    """Manages all remote configuration settings.

    Accessed via ``remote.settings``. Populated automatically during
    :meth:`~unfurled.remote.Remote.init`. Individual sections can be updated
    with the ``update_*`` methods.

    Example::

        await remote.settings.update_display(brightness=80)
        await remote.settings.update_network(wake_on_wlan=True)
    """

    def __init__(self, remote: Remote) -> None:
        super().__init__(remote)
        self.display = DisplaySettings()
        self.button = ButtonSettings()
        self.sound = SoundSettings()
        self.haptic = HapticSettings()
        self.power_saving = PowerSavingSettings()
        self.network = NetworkSettings()
        self.software_update = SoftwareUpdateSettings()
        self.localization = LocalizationInfo()
        self.bluetooth = BluetoothSettings()
        self.profile = ProfileSettings()
        self.voice = VoiceSettings()
        self.features: list[Feature] = []

    # ------------------------------------------------------------------
    # Update methods
    # ------------------------------------------------------------------

    async def update_display(
        self,
        *,
        auto_brightness: bool | None = None,
        brightness: int | None = None,
    ) -> None:
        """Update display settings, patching only the supplied values.

        Args:
            auto_brightness: Enable or disable automatic brightness adjustment.
            brightness: Display brightness level (0–100).
        """
        body: dict = {
            "auto_brightness": self.display.auto_brightness,
            "brightness": self.display.brightness,
        }
        if auto_brightness is not None:
            body["auto_brightness"] = auto_brightness
        if brightness is not None:
            body["brightness"] = brightness
        await self._api.patch_display_settings(body)
        self.display.auto_brightness = bool(body["auto_brightness"])
        self.display.brightness = int(body["brightness"])

    async def update_button(
        self,
        *,
        auto_brightness: bool | None = None,
        brightness: int | None = None,
        static_color: dict | None = None,
    ) -> None:
        """Update button backlight settings.

        Args:
            auto_brightness: Enable or disable automatic button brightness.
            brightness: Button brightness level (0–100).
            static_color: RGB colour dict for static button illumination.
        """
        await self._ensure_awake()
        body: dict = {
            "auto_brightness": self.button.auto_brightness,
            "brightness": self.button.brightness,
        }
        if self.button.static_color is not None:
            body["static_color"] = self.button.static_color
        if auto_brightness is not None:
            body["auto_brightness"] = auto_brightness
        if brightness is not None:
            body["brightness"] = brightness
        if (
            static_color is not None
            and "RGB_COLOR" in self._remote.flags.button_features
            and static_color
        ):
            body["static_color"] = static_color
        await self._api.patch_button_settings(body)
        self.button.auto_brightness = bool(body["auto_brightness"])
        self.button.brightness = int(body["brightness"])
        self.button.static_color = body.get("static_color")

    async def update_sound(
        self,
        *,
        enabled: bool | None = None,
        volume: int | None = None,
    ) -> None:
        """Update sound effect settings.

        Args:
            enabled: Enable or disable UI sound effects.
            volume: Sound effects volume level (0–100).
        """
        await self._ensure_awake()
        body: dict = {
            "enabled": self.sound.enabled,
            "volume": self.sound.volume,
        }
        if enabled is not None:
            body["enabled"] = enabled
        if volume is not None:
            body["volume"] = volume
        await self._api.patch_sound_settings(body)
        self.sound.enabled = bool(body["enabled"])
        self.sound.volume = int(body["volume"])

    async def update_haptic(self, *, enabled: bool | None = None) -> None:
        """Enable or disable haptic feedback.

        Args:
            enabled: Enable or disable haptic feedback.
        """
        await self._ensure_awake()
        body: dict = {"enabled": self.haptic.enabled}
        if enabled is not None:
            body["enabled"] = enabled
        await self._api.patch_haptic_settings(body)
        self.haptic.enabled = bool(body["enabled"])

    async def update_power_saving(
        self,
        *,
        display_timeout: int | None = None,
        wakeup_sensitivity: int | None = None,
        sleep_timeout: int | None = None,
    ) -> None:
        """Update power-saving settings.

        Args:
            display_timeout: Seconds before display turns off (0–60).
            wakeup_sensitivity: Wake-up sensitivity level (0–3).
            sleep_timeout: Seconds before entering standby (0–1800).
        """
        await self._ensure_awake()
        body: dict = {
            "display_off_sec": self.power_saving.display_off_sec,
            "wakeup_sensitivity": self.power_saving.wakeup_sensitivity,
            "standby_sec": self.power_saving.standby_sec,
        }
        if display_timeout is not None:
            body["display_off_sec"] = display_timeout
        if wakeup_sensitivity is not None:
            body["wakeup_sensitivity"] = wakeup_sensitivity
        if sleep_timeout is not None:
            body["standby_sec"] = sleep_timeout
        await self._api.patch_power_saving_settings(body)
        self.power_saving.display_off_sec = int(body["display_off_sec"])
        self.power_saving.wakeup_sensitivity = int(body["wakeup_sensitivity"])
        self.power_saving.standby_sec = int(body["standby_sec"])

    async def update_network(
        self,
        *,
        bt_enabled: bool | None = None,
        wifi_enabled: bool | None = None,
        wake_on_wlan: bool | None = None,
    ) -> None:
        """Update network settings.

        Args:
            bt_enabled: Enable or disable Bluetooth.
            wifi_enabled: Enable or disable Wi-Fi.
            wake_on_wlan: Enable or disable Wake-on-WLAN.
        """
        await self._ensure_awake()
        body: dict = {
            "bt_enabled": self.network.bt_enabled,
            "wifi_enabled": self.network.wifi_enabled,
        }
        if bt_enabled is not None:
            body["bt_enabled"] = bt_enabled
        if wifi_enabled is not None:
            body["wifi_enabled"] = wifi_enabled
        if (
            wake_on_wlan is not None
            and self._remote.identity.sw_version
            and Version(self._remote.identity.sw_version) >= Version("2.0.0")
        ):
            body["wake_on_wlan"] = {"enabled": wake_on_wlan}
        await self._api.patch_network_settings(body)
        self.network.bt_enabled = bool(body["bt_enabled"])
        self.network.wifi_enabled = bool(body["wifi_enabled"])
        if wake_on_wlan is not None:
            self.network.wifi.wake_on_wlan = wake_on_wlan
