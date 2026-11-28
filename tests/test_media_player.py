"""Tests for media-player state updates and commands."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from unfurled.entities.activity import Activity
from unfurled.entities.media_player import MediaPlayerEntity
from unfurled.helpers.models import UpdateType


@pytest.fixture
def player(remote):
    remote._ensure_awake = AsyncMock()
    remote.api.put_entity_command = AsyncMock()
    return MediaPlayerEntity("media_player.tv", remote)


class TestMediaPlayerState:
    @pytest.mark.parametrize("key", ["media_duration", "media_position"])
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, 0.0),
            (120, 120.0),
            (12.5, 12.5),
            ("120", 120.0),
            (" 12.5 ", 12.5),
            ("", 0.0),
            (" ", 0.0),
            (None, 0.0),
            ("unknown", 0.0),
            (-1, 0.0),
            ("-1", 0.0),
            (True, 0.0),
            (False, 0.0),
            (float("nan"), 0.0),
            (float("inf"), 0.0),
            (float("-inf"), 0.0),
            ("NaN", 0.0),
            ("inf", 0.0),
            ("1e999", 0.0),
            (10**1000, 0.0),
            ([], 0.0),
            ({}, 0.0),
        ],
    )
    def test_progress_is_normalized(self, player, key, value, expected):
        player.update_attributes({key: 42})

        changed = player.update_attributes({key: value, "media_title": "TV Audio"})

        assert getattr(player, key) == expected
        assert isinstance(getattr(player, key), float)
        assert changed[key] == expected
        assert player.media_title == "TV Audio"

    def test_partial_update_preserves_progress(self, player):
        player.update_attributes({"media_duration": "120.5", "media_position": "12.5"})

        changed = player.update_attributes({"state": "PLAYING"})

        assert player.media_duration == 120.5
        assert player.media_position == 12.5
        assert "media_duration" not in changed
        assert "media_position" not in changed

    async def test_api_response_normalizes_progress(self, player, remote):
        remote.api.get_entity = AsyncMock(
            return_value={
                "attributes": {
                    "state": "PLAYING",
                    "media_title": "TV Audio",
                    "media_duration": "",
                    "media_position": "0",
                }
            }
        )

        await player.update_data()

        assert player.media_duration == 0.0
        assert player.media_position == 0.0
        assert player.media_title == "TV Audio"
        assert player.state == "PLAYING"

    async def test_websocket_update_clears_unknown_progress(self, player, remote):
        remote._entities[player.id] = player
        player.update_attributes({"media_duration": 120, "media_position": 42})

        await remote._handle_ws_message(
            json.dumps(
                {
                    "msg": "entity_change",
                    "msg_data": {
                        "entity_type": "media_player",
                        "entity_id": player.id,
                        "new_state": {
                            "attributes": {
                                "state": "PLAYING",
                                "media_title": "TV Audio",
                                "media_duration": "",
                                "media_position": None,
                            }
                        },
                    },
                }
            )
        )

        assert player.media_duration == 0.0
        assert player.media_position == 0.0
        assert player.media_title == "TV Audio"
        assert remote.last_update_type == UpdateType.MEDIA_PLAYER

    def test_update_attributes_updates_public_properties(self, player: MediaPlayerEntity):
        updated_at = datetime(2026, 8, 14, 12, 0)

        changed = player.update_attributes(
            {
                "state": "PLAYING",
                "media_image_url": "https://example.test/art.jpg",
                "source": "HDMI 1",
                "source_list": ["HDMI 1", "HDMI 2"],
                "media_duration": 3600,
                "media_artist": "Artist",
                "media_album": "Album",
                "media_title": "Title",
                "media_position": 120,
                "media_position_updated_at": updated_at,
                "media_type": "movie",
                "volume": "0.42",
                "muted": False,
            }
        )

        assert changed["entity_id"] == player.id
        assert player.name == "media_player.tv"
        assert player.state == "PLAYING"
        assert player.is_on is True
        assert player.source_list == ["HDMI 1", "HDMI 2"]
        assert player.current_source == "HDMI 1"
        assert player.media_image_url == "https://example.test/art.jpg"
        assert player.media_title == "Title"
        assert player.media_artist == "Artist"
        assert player.media_album == "Album"
        assert player.media_type == "movie"
        assert player.media_duration == 3600
        assert player.media_position == 120
        assert player.media_position_updated_at == updated_at
        assert player.volume == 0.42
        assert player.muted is False
        assert player.available_commands == []
        assert player.initialized is False

    def test_activity_state_overrides_off_entity_state(self, player: MediaPlayerEntity, remote):
        activity = Activity(
            {"entity_id": "act-001", "name": {}, "attributes": {"state": "ON"}}, remote
        )
        player._activity = activity

        player.update_attributes({"state": "OFF"})

        assert player.activity is activity
        assert player.state == "ON"

    async def test_update_data_is_cached_unless_forced(self, player: MediaPlayerEntity, remote):
        remote.api.get_entity = AsyncMock(return_value={"attributes": {"state": "ON"}})

        await player.update_data()
        await player.update_data()
        await player.update_data(force=True)

        assert player.initialized is True
        assert player.state == "ON"
        assert remote.api.get_entity.await_count == 2


class TestMediaPlayerCommands:
    async def test_direct_commands_delegate_to_the_entity(self, player: MediaPlayerEntity, remote):
        await player.turn_on()
        await player.turn_off()
        await player.volume_up()
        await player.volume_down()
        await player.mute_toggle()
        await player.volume_set(37)
        await player.play_pause()
        await player.stop()
        await player.next_track()
        await player.previous_track()
        await player.seek(42.5)
        await player.select_source("HDMI 2")

        calls = remote.api.put_entity_command.await_args_list
        assert len(calls) == 12
        assert calls[0].args == ("media_player.tv", "media_player.on")
        assert calls[1].args == ("media_player.tv", "media_player.off")
        assert calls[5].args == ("media_player.tv", "media_player.volume", {"volume": 37})
        assert calls[10].args == ("media_player.tv", "media_player.seek", {"media_position": 42.5})
        assert calls[11].args == (
            "media_player.tv",
            "media_player.select_source",
            {"source": "HDMI 2"},
        )
        assert player.state == "OFF"
        assert player.current_source == "HDMI 2"
        assert remote._ensure_awake.await_count == 12

    async def test_activity_mapping_is_preferred_for_commands(
        self, player: MediaPlayerEntity, remote
    ):
        activity = Activity(
            {"entity_id": "act-001", "name": {}, "attributes": {"state": "ON"}}, remote
        )
        activity._apply_button_mapping(
            "POWER",
            {
                "entity_id": "media_player.receiver",
                "cmd_id": "receiver.power",
                "params": {"on": True},
            },
        )
        player._activity = activity

        await player.turn_on()

        remote.api.put_entity_command.assert_awaited_once_with(
            "media_player.receiver", "receiver.power", {"on": True}
        )
