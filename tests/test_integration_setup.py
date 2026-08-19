"""Tests for the high-level typed integration setup API."""

from unittest.mock import AsyncMock

import pytest

from unfurled.helpers.exceptions import (
    HTTPError,
    IntegrationInstanceAmbiguous,
    InvalidEntitySelection,
    SetupTimeout,
)
from unfurled.setup import (
    ConfirmationSetupAction,
    LocalizedText,
    SetupState,
    TextSetupField,
    page_from_core,
    result_from_core,
)
from unfurled.submodules.integrations import Integrations


class _Remote:
    def __init__(self) -> None:
        self.api = type("API", (), {})()


def _integrations() -> tuple[Integrations, _Remote]:
    remote = _Remote()
    return Integrations(remote), remote


@pytest.mark.asyncio
async def test_start_setup_parses_typed_dynamic_input_page():
    integrations, remote = _integrations()
    remote.api.post_integration_setup = AsyncMock(
        return_value={
            "state": "WAIT_USER_ACTION",
            "require_user_action": {
                "input": {
                    "title": {"en": "Connect"},
                    "settings": [
                        {"id": "host", "label": {"en": "Host"}, "field": {"text": {"value": ""}}},
                        {
                            "id": "secure",
                            "label": {"en": "Secure"},
                            "field": {"checkbox": {"value": True}},
                        },
                    ],
                }
            },
        }
    )

    result = await integrations.start_setup(
        "demo", setup_data={"port": 8080, "secure": False}, name="Demo"
    )

    assert result.state == "WAIT_USER_ACTION"
    assert result.action and result.action.page
    assert result.action.page.title.text("en_US") == "Connect"
    assert result.action.page.fields[1].value is True
    assert result.raw["require_user_action"] == remote.api.post_integration_setup.return_value[
        "require_user_action"
    ]
    assert result.action.raw == result.raw["require_user_action"]
    remote.api.post_integration_setup.assert_awaited_once_with(
        {
            "driver_id": "demo",
            "reconfigure": False,
            "setup_data": {"port": "8080", "secure": "false"},
            "name": {"en": "Demo"},
        }
    )


@pytest.mark.asyncio
async def test_setup_definition_parses_driver_metadata_into_a_typed_model():
    integrations, remote = _integrations()
    remote.api.get_driver = AsyncMock(
        return_value={
            "name": {"en": "Demo"},
            "setup_data_schema": {
                "title": {"en": "Initial setup"},
                "settings": [],
            },
        }
    )

    definition = await integrations.get_setup_definition("demo")

    assert definition.driver_id == "demo"
    assert definition.name.text() == "Demo"
    assert definition.setup_data_schema
    assert definition.setup_data_schema.title.text() == "Initial setup"
    assert definition.raw["setup_data_schema"] == remote.api.get_driver.return_value[
        "setup_data_schema"
    ]


@pytest.mark.asyncio
async def test_session_recovers_completion_when_core_removes_setup():
    integrations, remote = _integrations()
    remote.api.get_integration_setup = AsyncMock(side_effect=HTTPError(404, "missing"))
    remote.api.get_integration_setups = AsyncMock(return_value=[])
    remote.api.get_integrations = AsyncMock(
        return_value=[{"driver_id": "demo", "integration_id": "demo.main"}]
    )

    result = await integrations.setup("demo").wait_for_update(attempts=1)

    assert result.state == "OK"
    assert result.instance_id == "demo.main"


@pytest.mark.asyncio
async def test_entity_methods_paginate_and_validate_removal():
    integrations, remote = _integrations()
    remote.api.get_integration_entities = AsyncMock(
        return_value=[
            {"entity_id": "light.kitchen", "entity_type": "light", "name": {"en": "Kitchen"}}
        ]
    )
    remote.api.get_entities = AsyncMock(
        return_value=[
            {"entity_id": "light.kitchen", "entity_type": "light", "name": {"en": "Kitchen"}}
        ]
    )
    remote.api.delete_entities = AsyncMock()
    remote.api.post_integration_entities = AsyncMock(return_value=["light.kitchen"])

    available = await integrations.available_entities("demo.main")
    configured = await integrations.configured_entities("demo.main")
    added = await integrations.add_entities("demo.main", ["light.kitchen"])
    removed = await integrations.remove_entities("demo.main", ["light.kitchen"])

    assert available[0].name.text() == "Kitchen"
    assert available[0].raw["entity_id"] == "light.kitchen"
    assert configured[0].id == "light.kitchen"
    assert added == ["light.kitchen"]
    assert removed == ["light.kitchen"]
    remote.api.delete_entities.assert_awaited_once_with(["light.kitchen"])
    with pytest.raises(InvalidEntitySelection):
        await integrations.add_entities("demo.main", [])
    with pytest.raises(InvalidEntitySelection):
        await integrations.add_entities("demo.main", ["light.missing"])


@pytest.mark.asyncio
async def test_wait_for_update_polls_setup_until_core_requires_user_action():
    integrations, remote = _integrations()
    remote.api.get_integration_setup = AsyncMock(
        side_effect=[
            {"state": "SETUP"},
            {
                "state": "WAIT_USER_ACTION",
                "require_user_action": {"confirmation": {"title": {"en": "Continue"}}},
            },
        ]
    )

    result = await integrations.setup("demo").wait_for_update(attempts=2, interval=0)

    assert result.state == SetupState.WAIT_USER_ACTION
    assert result.action
    assert remote.api.get_integration_setup.await_count == 2


def test_setup_models_parse_every_core_field_and_localization_variant():
    page = page_from_core(
        {
            "title": {"en": "Setup", "de": "Einrichtung"},
            "settings": [
                {
                    "id": "text",
                    "label": {"en": "Text"},
                    "field": {"text": {"value": "a", "regex": ".+"}},
                },
                {"id": "secret", "label": {"en": "Secret"}, "field": {"password": {"value": ""}}},
                {
                    "id": "port",
                    "label": {"en": "Port"},
                    "field": {
                        "number": {
                            "value": 8080,
                            "min": 1,
                            "max": 9999,
                            "steps": 1,
                            "decimals": 0,
                            "unit": {"en": "port"},
                        }
                    },
                },
                {
                    "id": "notes",
                    "label": {"en": "Notes"},
                    "field": {"textarea": {"value": "notes"}},
                },
                {"id": "secure", "label": {"en": "Secure"}, "field": {"checkbox": {"value": True}}},
                {
                    "id": "kind",
                    "label": {"en": "Kind"},
                    "field": {
                        "dropdown": {"value": "a", "items": [{"id": "a", "label": {"en": "A"}}]}
                    },
                },
                {
                    "id": "help",
                    "label": {"en": "Help"},
                    "field": {"label": {"value": {"en": "Read this"}}},
                },
                {"id": "other", "label": {"en": "Other"}, "field": {"unsupported": {}}},
            ],
        }
    )

    assert page and page.title.text("de-DE") == "Einrichtung"
    assert [field.kind for field in page.fields] == [
        "text",
        "password",
        "number",
        "textarea",
        "checkbox",
        "dropdown",
        "label",
        "unknown",
    ]
    assert page.fields[2].unit.text() == "port"
    assert isinstance(page.fields[0], TextSetupField)
    assert page.fields[5].options[0].label.text() == "A"
    assert isinstance(page.fields[6].value, LocalizedText)
    assert page.fields[6].value.text() == "Read this"
    assert page.raw["title"] == {"en": "Setup", "de": "Einrichtung"}
    assert page.fields[-1].raw == {
        "id": "other",
        "label": {"en": "Other"},
        "field": {"unsupported": {}},
    }


def test_setup_result_parses_confirmation_and_invalid_states_safely():
    result = result_from_core(
        "demo",
        {
            "id": "core-setup-id",
            "state": "WAIT_USER_ACTION",
            "require_user_action": {
                "confirmation": {
                    "title": {"en": "Continue?"},
                    "message1": {"en": "First"},
                    "message2": {"en": "Second"},
                    "image": "https://example.test/image.png",
                }
            },
        },
    )
    assert result.setup_id == "core-setup-id"
    assert isinstance(result.action, ConfirmationSetupAction)
    assert result.action.title.text() == "Continue?"
    assert result.action.image == "https://example.test/image.png"
    future = result_from_core(
        "demo",
        {"state": "future", "require_user_action": {"oauth": {"url": "https://example.test"}}},
    )
    assert future.state == SetupState.ERROR
    assert future.raw_state == "future"
    assert future.action is None
    assert future.raw["require_user_action"] == {"oauth": {"url": "https://example.test"}}


@pytest.mark.asyncio
async def test_start_setup_accepts_localized_name_without_changing_string_callers():
    integrations, remote = _integrations()
    remote.api.post_integration_setup = AsyncMock(return_value={"state": "SETUP"})

    await integrations.start_setup("demo", name={"en": "Demo", "de": "Beispiel"})

    remote.api.post_integration_setup.assert_awaited_once_with(
        {
            "driver_id": "demo",
            "reconfigure": False,
            "name": {"en": "Demo", "de": "Beispiel"},
        }
    )


@pytest.mark.asyncio
async def test_session_delegates_all_setup_actions_and_times_out_for_active_setup_404():
    integrations, remote = _integrations()
    remote.api.post_integration_setup = AsyncMock(return_value={"id": "demo", "state": "SETUP"})
    remote.api.get_integration_setup = AsyncMock(side_effect=HTTPError(404, "missing"))
    remote.api.get_integration_setups = AsyncMock(return_value=["demo"])
    remote.api.put_integration_setup = AsyncMock(return_value={"state": "WAIT_USER_ACTION"})
    remote.api.delete_integration_setup = AsyncMock()
    session = integrations.setup("demo")

    await session.start(reconfigure=True)
    await session.submit({"enabled": True})
    await session.confirm(False)
    await session.cancel()
    with pytest.raises(SetupTimeout):
        await session.wait_for_update(attempts=1)

    remote.api.post_integration_setup.assert_awaited_once_with(
        {"driver_id": "demo", "reconfigure": True}
    )
    remote.api.put_integration_setup.assert_any_await("demo", {"enabled": "true"})
    remote.api.put_integration_setup.assert_any_await("demo", confirm=False)
    remote.api.delete_integration_setup.assert_awaited_once_with("demo")


@pytest.mark.asyncio
async def test_instance_resolution_honors_reconfigure_target_and_ambiguous_instances():
    integrations, remote = _integrations()
    remote.api.get_integrations = AsyncMock(
        return_value=[
            {"driver_id": "demo", "integration_id": "demo.one"},
            {"driver_id": "demo", "integration_id": "demo.two"},
        ]
    )

    assert await integrations.resolve_instance("demo", "demo.two") == "demo.two"
    with pytest.raises(IntegrationInstanceAmbiguous):
        await integrations.resolve_instance("demo")
