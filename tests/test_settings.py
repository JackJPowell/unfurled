"""Tests for configuration updates and setting mutations."""

from __future__ import annotations

from unittest.mock import AsyncMock

from unfurled.helpers.models import ConfigurationChangeEvent, UpdateType


class TestConfigurationChanges:
    def test_configuration_change_updates_each_section(self, remote):
        remote.system.flags.button_features = ["RGB_COLOR"]

        remote.settings._on_configuration_change(
            ConfigurationChangeEvent(
                {
                    "display": {"auto_brightness": True, "brightness": 75},
                    "button": {
                        "auto_brightness": True,
                        "brightness": 60,
                        "static_color": {"red": 1, "green": 2, "blue": 3},
                    },
                    "sound": {"enabled": False, "volume": 30},
                    "haptic": {"enabled": False},
                    "software_update": {
                        "check_for_updates": False,
                        "auto_update": True,
                        "ota_window_start": "03:00:00",
                        "ota_window_end": "04:00:00",
                        "channel": "BETA",
                    },
                    "power_saving": {
                        "display_off_sec": 10,
                        "wakeup_sensitivity": 3,
                        "standby_sec": 120,
                    },
                    "network": {
                        "bt_enabled": False,
                        "wifi_enabled": False,
                        "wifi": {
                            "band": "5GHz",
                            "scan_interval_sec": 20,
                            "ipv4_type": "STATIC",
                            "wake_on_wlan": {"enabled": True, "available": True},
                        },
                    },
                    "localization": {
                        "language_code": "de_DE",
                        "country_code": "DE",
                        "time_zone": "Europe/Berlin",
                        "time_format_24h": False,
                        "measurement_unit": "METRIC",
                    },
                    "bt": {
                        "peripheral_connections": 2,
                        "advertisement_name": "Remote",
                        "enable_hci_log": True,
                        "enable_debug_port": True,
                        "version": "5.4",
                    },
                    "device": {"name": "Living Room"},
                    "profile": {"has_admin_pin": True},
                    "voice": {"microphone": True, "voice_assistant": {"id": "alexa"}},
                    "features": [
                        {
                            "id": "internal_ir",
                            "enabled": True,
                            "title": {"en": "IR"},
                            "description": {},
                            "help_url": "https://example.test/help",
                        }
                    ],
                }
            )
        )

        assert remote.settings.display.brightness == 75
        assert remote.settings.button.static_color == {"red": 1, "green": 2, "blue": 3}
        assert remote.settings.sound.enabled is False
        assert remote.settings.power_saving.standby_sec == 120
        assert remote.settings.network.wifi.wake_on_wlan is True
        assert remote.settings.network.wifi.wake_on_wlan_available is True
        assert remote.settings.localization.language_code == "de_DE"
        assert remote.settings.bluetooth.version == "5.4"
        assert remote.device.name == "Living Room"
        assert remote.settings.profile.has_admin_pin is True
        assert remote.settings.voice.voice_assistant == {"id": "alexa"}
        assert remote.settings.internal_ir_enabled is True
        assert remote.last_update_type is UpdateType.CONFIGURATION


class TestSettingsUpdates:
    async def test_update_methods_patch_and_apply_local_state(self, remote):
        remote._ensure_awake = AsyncMock()
        remote.device.sw_version = "2.0.0"
        remote.system.flags.button_features = ["RGB_COLOR"]
        remote.api.patch_display_settings = AsyncMock()
        remote.api.patch_button_settings = AsyncMock()
        remote.api.patch_sound_settings = AsyncMock()
        remote.api.patch_haptic_settings = AsyncMock()
        remote.api.patch_power_saving_settings = AsyncMock()
        remote.api.patch_network_settings = AsyncMock()

        await remote.settings.update_display(auto_brightness=True, brightness=80)
        await remote.settings.update_button(
            auto_brightness=True,
            brightness=70,
            static_color={"red": 1, "green": 2, "blue": 3},
        )
        await remote.settings.update_sound(enabled=False, volume=20)
        await remote.settings.update_haptic(enabled=False)
        await remote.settings.update_power_saving(
            display_timeout=15, wakeup_sensitivity=1, sleep_timeout=600
        )
        await remote.settings.update_network(
            bt_enabled=False, wifi_enabled=False, wake_on_wlan=True
        )

        remote.api.patch_display_settings.assert_awaited_once_with(
            {"auto_brightness": True, "brightness": 80}
        )
        remote.api.patch_button_settings.assert_awaited_once_with(
            {
                "auto_brightness": True,
                "brightness": 70,
                "static_color": {"red": 1, "green": 2, "blue": 3},
            }
        )
        remote.api.patch_network_settings.assert_awaited_once_with(
            {
                "bt_enabled": False,
                "wifi_enabled": False,
                "wake_on_wlan": {"enabled": True},
            }
        )
        assert remote.settings.sound.volume == 20
        assert remote.settings.haptic.enabled is False
        assert remote.settings.power_saving.display_off_sec == 15
        assert remote.settings.network.wifi.wake_on_wlan is True
        assert remote._ensure_awake.await_count == 5
