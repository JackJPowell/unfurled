"""Activity-scoped entity cleanup without deleting configured entities."""

from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web

from unfurled import CoreAPI
from unfurled.helpers.exceptions import HTTPError

from .test_api import core_test_server
from .test_helpers import make_helpers


def activity_definition():
    return {
        "entity_id": "activity.home",
        "name": {"en": "Home"},
        "icon": "uc:home",
        "attributes": {"state": "ON"},
        "options": {
            "prevent_sleep": True,
            "ready_check": False,
            "error_policy": "abort",
            "included_entities": [
                {"entity_id": "removed", "available": False, "entity_commands": ["on"]},
                {"entity_id": "kept", "name": {"en": "Keep"}},
                {"entity_id": "other-orphan", "available": False},
            ],
            "sequences": {
                "on": [
                    {"type": "command", "command": {"entity_id": "removed", "cmd_id": "on"}},
                    {"type": "delay", "delay": 100},
                    {
                        "type": "command",
                        "command": {
                            "entity_id": "kept",
                            "cmd_id": "on",
                            "params": {"text": "removed"},
                        },
                    },
                ],
                "off": [{"type": "command", "command": {"entity_id": "removed", "cmd_id": "off"}}],
            },
            "button_mapping": [
                {
                    "button": "POWER",
                    "short_press": {"entity_id": "removed", "cmd_id": "on"},
                    "long_press": {"entity_id": "kept", "cmd_id": "off"},
                },
                {"button": "BACK", "long_press": {"entity_id": "removed", "cmd_id": "off"}},
                {"button": "PLAY", "short_press": {"entity_id": "kept", "cmd_id": "play"}},
            ],
            "touch_slider": {
                "enabled": True,
                "target": {"entity_id": "removed", "feature": "volume"},
            },
            "voice_assistant": {"target": {"entity_id": "removed", "profile_id": "default"}},
            "user_interface": {
                "pages": [
                    {
                        "page_id": "main",
                        "name": "Main",
                        "grid": {"width": 4, "height": 6},
                        "items": [
                            {
                                "type": "text",
                                "text": "Remove",
                                "command": {"entity_id": "removed", "cmd_id": "on"},
                            },
                            {"type": "media_player", "media_player_id": "removed"},
                            {"type": "sensor", "sensor": {"sensor_id": "removed"}},
                            {"type": "select", "select": {"select_id": "removed"}},
                            {"type": "number", "number": {"number_id": "removed"}},
                            {
                                "type": "slider",
                                "slider": {"entity_id": "removed", "feature": "volume"},
                            },
                            {"type": "webview", "webview": {"webview_id": "removed"}},
                            {"type": "text", "text": "removed"},
                            {"type": "media_player", "media_player_id": "kept"},
                        ],
                    },
                    {"page_id": "empty-after", "items": [{"media_player_id": "removed"}]},
                    {"page_id": "untouched", "items": [{"type": "text", "text": "Keep"}]},
                ]
            },
        },
    }


def cleanup_mocks(activity=None):
    helpers, api = make_helpers()
    api.get_activity = AsyncMock(
        return_value=activity if activity is not None else activity_definition()
    )
    api.post_activity_buttons = AsyncMock()
    api.patch_activity_ui_page = AsyncMock()
    api.patch_activity = AsyncMock(return_value={"entity_id": "activity.home", "saved": True})
    return helpers, api


async def test_removes_selected_references_and_preserves_everything_else():
    helpers, api = cleanup_mocks()
    fetched = api.get_activity.return_value
    before = deepcopy(fetched)
    order = Mock()
    for method in (
        "get_activity",
        "post_activity_buttons",
        "patch_activity_ui_page",
        "patch_activity",
    ):
        order.attach_mock(getattr(api, method), method)

    result = await helpers.remove_activity_entities(
        "activity.home", ["removed", "removed"], only_unused=False
    )

    assert result == {"entity_id": "activity.home", "saved": True}
    assert fetched == before  # Don't mutate a caller's or cache's fetched data.
    api.get_activity.assert_awaited_once_with("activity.home")
    api.post_activity_buttons.assert_awaited_once_with(
        "activity.home",
        [
            {"button": "POWER", "long_press": {"entity_id": "kept", "cmd_id": "off"}},
            {"button": "BACK"},
            {"button": "PLAY", "short_press": {"entity_id": "kept", "cmd_id": "play"}},
        ],
    )
    assert api.patch_activity_ui_page.await_args_list[0].args == (
        "activity.home",
        "main",
        {"items": before["options"]["user_interface"]["pages"][0]["items"][-2:]},
    )
    assert api.patch_activity_ui_page.await_args_list[1].args == (
        "activity.home",
        "empty-after",
        {"items": []},
    )
    assert api.patch_activity_ui_page.await_count == 2
    api.patch_activity.assert_awaited_once_with(
        "activity.home",
        {
            "options": {
                "entity_ids": ["kept", "other-orphan"],
                "sequences": {"on": before["options"]["sequences"]["on"][1:], "off": []},
                "touch_slider": {"enabled": False},
                "voice_assistant": {},
            }
        },
    )
    assert [call[0] for call in order.mock_calls] == [
        "get_activity",
        "post_activity_buttons",
        "patch_activity_ui_page",
        "patch_activity_ui_page",
        "patch_activity",
    ]
    api.delete_entity.assert_not_called()
    api.delete_entities.assert_not_called()
    helpers._remote._ensure_awake.assert_not_called()


@pytest.mark.parametrize("selected", [[], ["unknown"]])
async def test_noop_does_not_write(selected):
    helpers, api = cleanup_mocks()
    assert (
        await helpers.remove_activity_entities("activity.home", selected)
        == api.get_activity.return_value
    )
    api.post_activity_buttons.assert_not_awaited()
    api.patch_activity_ui_page.assert_not_awaited()
    api.patch_activity.assert_not_awaited()


async def test_removing_last_entity_sends_empty_id_and_sequence_arrays():
    helpers, api = cleanup_mocks(
        {
            "options": {
                "included_entities": [{"entity_id": "removed"}],
                "sequences": {"on": [{"command": {"entity_id": "removed", "cmd_id": "on"}}]},
                "touch_slider": {"enabled": True, "target": {"entity_id": "kept"}},
                "voice_assistant": {"target": {"entity_id": "kept"}},
            }
        }
    )
    await helpers.remove_activity_entities("activity.home", ["removed"], only_unused=False)
    api.patch_activity.assert_awaited_once_with(
        "activity.home",
        {
            "options": {
                "entity_ids": [],
                "sequences": {"on": []},
            }
        },
    )
    api.post_activity_buttons.assert_not_awaited()
    api.patch_activity_ui_page.assert_not_awaited()


async def test_cleans_dangling_references_even_if_entity_is_not_included():
    helpers, api = cleanup_mocks(
        {
            "options": {
                "included_entities": [{"entity_id": "kept"}],
                "button_mapping": [{"button": "BACK", "short_press": {"entity_id": "removed"}}],
            }
        }
    )
    first = api.get_activity.return_value
    final = {
        "options": {
            "included_entities": [{"entity_id": "kept"}],
            "button_mapping": [{"button": "BACK"}],
        }
    }
    api.get_activity.side_effect = [first, final]
    assert (
        await helpers.remove_activity_entities("activity.home", ["removed"], only_unused=False)
        == final
    )
    api.patch_activity.assert_not_awaited()


@pytest.mark.parametrize(
    "failure", ["get_activity", "post_activity_buttons", "patch_activity_ui_page", "patch_activity"]
)
async def test_errors_propagate_and_stop_further_writes(failure):
    helpers, api = cleanup_mocks()
    error = HTTPError(503, "offline")
    getattr(api, failure).side_effect = error
    with pytest.raises(HTTPError) as raised:
        await helpers.remove_activity_entities("activity.home", ["removed"], only_unused=False)
    assert raised.value is error
    if failure != "patch_activity":
        api.patch_activity.assert_not_awaited()
    if failure in {"get_activity", "post_activity_buttons"}:
        api.patch_activity_ui_page.assert_not_awaited()


async def test_invalid_page_stops_cleanup_before_any_writes():
    helpers, api = cleanup_mocks()
    del api.get_activity.return_value["options"]["user_interface"]["pages"][1]["page_id"]
    with pytest.raises(ValueError, match="page_id"):
        await helpers.remove_activity_entities("activity.home", ["removed"], only_unused=False)
    api.post_activity_buttons.assert_not_awaited()
    api.patch_activity_ui_page.assert_not_awaited()
    api.patch_activity.assert_not_awaited()


@pytest.mark.parametrize("selected", ["removed", [""], [None]])
async def test_rejects_invalid_entity_selections_before_requests(selected):
    helpers, api = cleanup_mocks()
    with pytest.raises(ValueError, match="entity_ids"):
        await helpers.remove_activity_entities("activity.home", selected)
    api.get_activity.assert_not_awaited()


@pytest.mark.parametrize(
    "references",
    [
        {"touch_slider": {"enabled": True, "target": {"entity_id": "used"}}},
        {"touch_slider": {"enabled": False, "target": {"entity_id": "used"}}},
        {"voice_assistant": {"target": {"entity_id": "used"}}},
        {"sequences": {"on": [{"command": {"entity_id": "used"}}]}},
        {"sequences": {"off": [{"command": {"entity_id": "used"}}]}},
        {"button_mapping": [{"button": "BACK", "short_press": {"entity_id": "used"}}]},
        {"button_mapping": [{"button": "BACK", "long_press": {"entity_id": "used"}}]},
        *[
            {"user_interface": {"pages": [{"page_id": "main", "items": [item]}]}}
            for item in [
                {"command": {"entity_id": "used"}},
                {"media_player_id": "used"},
                {"sensor": {"sensor_id": "used"}},
                {"select": {"select_id": "used"}},
                {"number": {"number_id": "used"}},
                {"slider": {"entity_id": "used"}},
                {"webview": {"webview_id": "used"}},
            ]
        ],
    ],
)
async def test_default_cleanup_and_unused_scanner_protect_each_reference_type(references):
    activity = {
        "entity_id": "activity.home",
        "options": {
            "included_entities": [
                {"entity_id": "used", "available": False},
                {"entity_id": "unused"},
            ],
            **references,
        },
    }
    helpers, api = cleanup_mocks(activity)
    before = deepcopy(activity)
    api.get_activities = AsyncMock(return_value=[{"entity_id": "activity.home"}])
    unused = await helpers.find_unused_activity_entities()
    assert [entity["entity_id"] for entity in unused] == ["unused"]

    await helpers.remove_activity_entities("activity.home", ["used", "unused"])
    api.patch_activity.assert_awaited_once_with(
        "activity.home", {"options": {"entity_ids": ["used"]}}
    )
    api.post_activity_buttons.assert_not_awaited()
    api.patch_activity_ui_page.assert_not_awaited()
    assert activity == before


async def test_default_skips_all_used_entities_without_writes():
    helpers, api = cleanup_mocks()
    result = await helpers.remove_activity_entities("activity.home", ["removed", "kept"])
    assert result == api.get_activity.return_value
    api.get_activity.assert_awaited_once_with("activity.home")
    api.patch_activity.assert_not_awaited()
    api.post_activity_buttons.assert_not_awaited()
    api.patch_activity_ui_page.assert_not_awaited()


async def test_default_removes_last_unused_entity_without_touching_controls():
    helpers, api = cleanup_mocks(
        {
            "options": {
                "included_entities": [{"entity_id": "unused"}],
                "touch_slider": {"enabled": True},
                "voice_assistant": {},
            }
        }
    )
    await helpers.remove_activity_entities("activity.home", ["unused"])
    api.patch_activity.assert_awaited_once_with("activity.home", {"options": {"entity_ids": []}})
    api.post_activity_buttons.assert_not_awaited()
    api.patch_activity_ui_page.assert_not_awaited()


async def test_new_endpoints_encode_ids_and_preserve_payloads():
    activity_id, page_id = "activity/../?#é", "page/../?#é"
    received = []

    async def buttons(request):
        assert request.match_info["activity_id"] == activity_id
        assert not request.query
        received.append(await request.json())
        return web.json_response(received[-1])

    async def page(request):
        assert request.match_info["activity_id"] == activity_id
        assert request.match_info["page_id"] == page_id
        assert not request.query
        received.append(await request.json())
        return web.json_response(received[-1])

    app = web.Application()
    app.router.add_post("/api/activities/{activity_id}/buttons", buttons)
    app.router.add_patch("/api/activities/{activity_id}/ui/pages/{page_id}", page)
    async with core_test_server(app) as base, CoreAPI(base) as api:
        assert await api.post_activity_buttons(activity_id, [{"button": "BACK"}]) == [
            {"button": "BACK"}
        ]
        assert await api.patch_activity_ui_page(activity_id, page_id, {"items": []}) == {
            "items": []
        }
    assert received == [[{"button": "BACK"}], {"items": []}]
