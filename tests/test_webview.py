"""Web view REST contracts, commands, cache updates and global settings."""

from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aioresponses import aioresponses

from unfurled import (
    CoreAPI,
    RemoteFeatureFlags,
    WebView,
    WebViewCreate,
    WebViewOptions,
    WebViewSettings,
    WebViewUpdate,
)
from unfurled.helpers.exceptions import HTTPError
from unfurled.helpers.models import ConfigurationChangeEvent, UpdateType, parse_ws_message

from .test_api import core_test_server


def definition() -> dict:
    return {
        "entity_id": "uc_web.main.dashboard",
        "entity_type": "webview",
        "integration_id": "uc_web.main",
        "name": {"en": "Dashboard", "de": "Übersicht"},
        "description": {"en": "Home status"},
        "icon": "uc:home",
        "options": {
            "editable": True,
            "url": "https://home.example/dashboard",
            "auth": {"type": "bearer", "has_token": True},
            "zoom": 80,
        },
        "attributes": {"state": "ON"},
    }


async def test_rest_contracts_and_path_encoding():
    received = []
    entity_id = "integration/../dashboard ?#%é"

    async def handler(request):
        received.append((request.method, dict(request.query)))
        if request.method == "HEAD":
            return web.Response(headers={"Pagination-Count": "12"})
        if "entity_id" in request.match_info:
            assert request.match_info["entity_id"] == entity_id
            assert not request.query
        if request.method in {"POST", "PATCH"}:
            received.append(await request.json())
        return web.json_response(definition())

    app = web.Application()
    app.router.add_route("*", "/api/webviews", handler)
    app.router.add_route("*", "/api/webviews/{entity_id}", handler)
    async with core_test_server(app) as base, CoreAPI(base) as api:
        assert await api.get_webview_count(kind="USER", q="home & away") == 12
        await api.get_webviews(
            25, page=2, kind="EXTERNAL", q="home & away", sort="name", order="asc", lang="de"
        )
        await api.post_webview({"name": {"en": "Clone"}, "clone_from": "uc_web.main.original"})
        await api.get_webview(entity_id)
        patch = {"options": {"user": {"zoom": None, "buttons": {"BACK": None}}}}
        await api.patch_webview(entity_id, patch)
        await api.delete_webview(entity_id)
        await api.delete_webviews()
    assert received == [
        ("HEAD", {"kind": "USER", "q": "home & away"}),
        (
            "GET",
            {
                "limit": "25",
                "page": "2",
                "kind": "EXTERNAL",
                "q": "home & away",
                "sort": "name",
                "order": "asc",
                "lang": "de",
            },
        ),
        ("POST", {}),
        {"name": {"en": "Clone"}, "clone_from": "uc_web.main.original"},
        ("GET", {}),
        ("PATCH", {}),
        patch,
        ("DELETE", {}),
        ("DELETE", {}),
    ]


async def test_settings_rest_contracts():
    async with CoreAPI("http://remote/api/") as api:
        with aioresponses() as mocked:
            mocked.get(
                "http://remote/api/cfg/webview?default=true", payload={"color_scheme": "dark"}
            )
            assert await api.get_webview_settings(default=True) == {"color_scheme": "dark"}
            mocked.get(
                "http://remote/api/cfg/webview/button_presets", payload=[{"id": "page_keys"}]
            )
            assert await api.get_webview_button_presets() == [{"id": "page_keys"}]
            mocked.patch("http://remote/api/cfg/webview", payload={"buttons": {}})
            body = {"buttons": {"BACK": None}, "allow_invalid_certificates": False}
            await api.patch_webview_settings(body)
            sent = next(
                calls[-1] for (method, _), calls in mocked.requests.items() if method == "PATCH"
            )
            assert sent.kwargs["json"] == body


async def test_create_update_refresh_and_delete_use_server_responses(remote):
    remote.api.post_webview = AsyncMock(return_value=definition())
    body = {"name": {"en": "Dashboard"}, "options": {"auth": {"token": "secret"}}}
    view = await remote.create_webview(body)
    assert isinstance(view, WebView)
    assert view.options["auth"] == {"type": "bearer", "has_token": True}
    assert "secret" not in repr(view._data)
    assert (view.url, view.editable, view.state) == ("https://home.example/dashboard", True, "ON")
    assert (view.icon, view.description, view.integration_id) == (
        "uc:home",
        "Home status",
        "uc_web.main",
    )
    remote.settings.localization.language_code = "de_DE"
    assert view.name == "Übersicht"
    copied_options = view.options
    copied_options["auth"]["has_token"] = False
    assert view.options["auth"]["has_token"] is True

    updated = definition()
    updated["options"] = {"editable": True, "url": view.url}
    remote.api.patch_webview = AsyncMock(return_value=updated)
    patch = {"options": {"auth": {"type": "none"}, "zoom": None}}
    await view.update(patch)
    remote.api.patch_webview.assert_awaited_once_with(view.id, patch)
    assert "auth" not in view.options and "zoom" not in view.options

    remote.api.get_webview = AsyncMock(return_value=definition())
    assert await remote.get_webview(view.id) is view
    assert view.options["zoom"] == 80
    remote.api.get_webview.return_value = updated
    await view.refresh()
    assert "zoom" not in view.options
    remote.api.delete_webview = AsyncMock()
    await view.delete()
    assert view.id not in remote._webviews


@pytest.mark.parametrize("command", ["open", "reload", "clear_cache", "clear_data"])
async def test_commands_route_to_core_for_external_views(remote, command):
    data = definition()
    data["options"]["editable"] = False
    view = WebView(data, remote)
    remote._ensure_awake = AsyncMock()
    remote.api.put_entity_command = AsyncMock()
    await getattr(view, command)()
    remote._ensure_awake.assert_awaited_once()
    remote.api.put_entity_command.assert_awaited_once_with(view.id, f"webview.{command}")
    assert view.state == "ON"


@pytest.mark.parametrize("status", [403, 408, 422, 503])
async def test_core_errors_propagate_without_mutating_cache(remote, status):
    view = WebView(definition(), remote)
    remote.api.patch_webview = AsyncMock(side_effect=HTTPError(status, "refused"))
    before = deepcopy(view._data)
    with pytest.raises(HTTPError):
        await view.update({"options": {"zoom": None}})
    assert view._data == before
    remote._ensure_awake = AsyncMock()
    remote.api.put_entity_command = AsyncMock(side_effect=HTTPError(status, "refused"))
    with pytest.raises(HTTPError):
        await view.open()
    assert view._data == before


async def test_websocket_state_and_options_changes(remote):
    remote.api.get_webview = AsyncMock(return_value=definition())
    view = await remote.get_webview(definition()["entity_id"])
    listener = AsyncMock()
    remote._state_change_callbacks.append(listener)
    raw = json.dumps(
        {
            "msg": "entity_change",
            "msg_data": {
                "entity_type": "webview",
                "entity_id": view.id,
                "new_state": {"attributes": {"state": "UNAVAILABLE"}},
            },
        }
    )
    assert parse_ws_message(raw).entity_id == view.id
    await remote._handle_ws_message(raw)
    assert view.state == "UNAVAILABLE"
    assert view.url == "https://home.example/dashboard"
    assert remote._last_update_type == UpdateType.WEBVIEW
    listener.assert_awaited_once()
    await remote._handle_ws_message(
        json.dumps(
            {
                "msg": "entity_change",
                "msg_data": {
                    "entity_type": "webview",
                    "entity_id": view.id,
                    "new_state": {"options": {"url": "https://new.example", "editable": False}},
                },
            }
        )
    )
    assert view.options == {"url": "https://new.example", "editable": False}


async def test_global_settings_full_partial_and_failed_updates(remote):
    remote.api.get_configuration = AsyncMock(return_value={})
    await remote.settings._fetch_configuration()
    assert remote.settings.webview == WebViewSettings()
    remote.api.get_configuration.return_value = {
        "webview": {
            "color_scheme": "light",
            "website_data": "clear_all_on_close",
            "buttons": {"BACK": {"action": "function", "function": "close"}},
        }
    }
    await remote.settings._fetch_configuration()
    remote.settings._on_configuration_change(
        ConfigurationChangeEvent(
            {
                "webview": {"allow_invalid_certificates": True},
            }
        )
    )
    assert remote.settings.webview.color_scheme == "light"
    assert remote.settings.webview.allow_invalid_certificates is True
    remote._ensure_awake = AsyncMock()
    result = {"buttons": {"BACK": {"action": "function", "function": "close"}}}
    remote.api.patch_webview_settings = AsyncMock(return_value=result)
    await remote.settings.update_webview(buttons={"BACK": None})
    remote.api.patch_webview_settings.assert_awaited_once_with({"buttons": {"BACK": None}})
    assert remote.settings.webview.buttons == result["buttons"]
    remote.api.patch_webview_settings.side_effect = HTTPError(403, "admin required")
    with pytest.raises(HTTPError):
        await remote.settings.update_webview(color_scheme="dark")
    assert remote.settings.webview.color_scheme == "light"
    remote.api.get_webview_settings = AsyncMock(return_value={"color_scheme": "dark"})
    assert (await remote.settings.refresh_webview()).color_scheme == "dark"


async def test_webview_widget_prevents_false_unused_entity_report(remote):
    remote.api.get_activities = AsyncMock(return_value=[{"entity_id": "activity.home"}])
    remote.api.get_activity = AsyncMock(
        return_value={
            "options": {
                "included_entities": [
                    {"entity_id": "webview.used", "entity_type": "webview"},
                    {"entity_id": "webview.unused", "entity_type": "webview"},
                ],
                "user_interface": {
                    "pages": [
                        {
                            "items": [
                                {"type": "webview", "webview": {"webview_id": "webview.used"}},
                            ]
                        }
                    ]
                },
            },
        }
    )
    unused = await remote.helpers.find_unused_activity_entities()
    assert [entity["entity_id"] for entity in unused] == ["webview.unused"]


@pytest.mark.parametrize("model", ["UCR2", "UCR3"])
@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("2.13.9", False),
        ("2.14.0", True),
        ("2.15.0", True),
        ("2.14.0-rc.1", False),
        ("invalid", False),
        ("", False),
    ],
)
def test_webview_firmware_availability(remote, model, version, expected):
    assert isinstance(remote.system.flags, RemoteFeatureFlags)
    remote.device.model_number = model
    # Verify an invalid or older version also clears a previously true flag.
    remote.system.flags.webview_available = True
    remote.device.sw_version = version
    remote._apply_firmware_capabilities()
    assert remote.system.flags.webview_available is expected


async def test_webview_configuration_event_dispatches_without_fetching(remote):
    remote.api.get_webview_settings = AsyncMock()
    remote.api.get_configuration = AsyncMock()
    listener = AsyncMock()
    remote.on_state_change(listener)
    await remote._handle_ws_message(
        json.dumps(
            {
                "msg": "configuration_change",
                "msg_data": {
                    "new_state": {
                        "webview": {
                            "color_scheme": "light",
                            "allow_invalid_certificates": False,
                            "buttons": {"BACK": {"action": "key", "key": "Escape"}},
                        }
                    }
                },
            }
        )
    )
    assert remote.settings.webview.color_scheme == "light"
    assert remote.settings.webview.buttons["BACK"]["key"] == "Escape"
    assert remote.last_update_type == UpdateType.CONFIGURATION
    listener.assert_awaited_once()
    remote.api.get_webview_settings.assert_not_awaited()
    remote.api.get_configuration.assert_not_awaited()


async def test_typed_options_send_omissions_and_explicit_resets_unchanged(remote):
    options: WebViewOptions = {
        "url": "https://home.example/dashboard",
        "auth": {"type": "bearer", "token": "secret"},
        "tls": {"certificate_sha256": "ab" * 32},
        "cookies": [{"name": "layout", "value": "compact", "secure": True}],
        "buttons": {"BACK": {"action": "function", "function": "back_or_close"}},
    }
    body: WebViewCreate = {"name": {"en": "Dashboard"}, "options": options}
    remote.api.post_webview = AsyncMock(return_value=definition())
    view = await remote.create_webview(body)
    remote.api.post_webview.assert_awaited_once_with(body)
    assert "reload_interval" not in body["options"]
    patch: WebViewUpdate = {"options": {"zoom": None, "buttons": {"BACK": None}}}
    remote.api.patch_webview = AsyncMock(return_value=definition())
    await view.update(patch)
    remote.api.patch_webview.assert_awaited_once_with(view.id, patch)
    assert "secret" not in repr(view.options)


async def test_webview_availability_does_not_trigger_requests(remote):
    remote.api.get_webviews = AsyncMock()
    remote.api.get_webview = AsyncMock()
    remote.device.sw_version = "2.14.0"
    remote._apply_firmware_capabilities()
    assert remote.system.flags.webview_available
    remote.api.get_webviews.assert_not_awaited()
    remote.api.get_webview.assert_not_awaited()
