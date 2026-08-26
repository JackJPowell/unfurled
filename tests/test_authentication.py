"""Tests for authentication and external-token workflows."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from unfurled.helpers.exceptions import ApiKeyError, ApiKeyNotFound, HTTPError


class TestApiKeys:
    async def test_list_and_create_key_updates_both_remote_and_api(self, remote):
        remote.api.get_api_keys = AsyncMock(return_value=[{"key_id": "old", "name": "Old"}])
        remote.api.create_api_key = AsyncMock(return_value={"api_key": "new-key"})

        assert await remote.auth.list_keys() == [{"key_id": "old", "name": "Old"}]
        assert await remote.auth.create_key("Coverage") == "new-key"

        remote.api.create_api_key.assert_awaited_once_with(
            "Coverage", ["admin"], replace_existing=False
        )
        assert remote._api_key == "new-key"
        assert remote.api._api_key == "new-key"

    async def test_generate_key_replaces_an_existing_key_with_the_default_name(self, remote):
        remote.api.create_api_key = AsyncMock(return_value={"api_key": "new-key"})

        assert await remote.auth.generate_key() == "new-key"

        remote.api.create_api_key.assert_awaited_once_with(
            "pyUnfoldedCircle", ["admin"], replace_existing=True
        )

    async def test_revoke_key_deletes_matching_key(self, remote):
        remote.api.get_api_keys = AsyncMock(return_value=[{"key_id": "key-1", "name": "Coverage"}])
        remote.api.delete_api_key = AsyncMock()

        await remote.auth.revoke_key("Coverage")

        remote.api.delete_api_key.assert_awaited_once_with("key-1")

    async def test_revoke_missing_key_raises(self, remote):
        remote.api.get_api_keys = AsyncMock(return_value=[])

        with pytest.raises(ApiKeyNotFound, match="Coverage"):
            await remote.auth.revoke_key("Coverage")

    async def test_rotate_key_replaces_existing_key(self, remote):
        remote.api.get_api_keys = AsyncMock(return_value=[{"key_id": "old", "name": "Coverage"}])
        remote.api.delete_api_key = AsyncMock()
        remote.api.post_api_key = AsyncMock(return_value={"api_key": "new-key"})

        assert await remote.auth.rotate_key("Coverage") == "new-key"
        remote.api.delete_api_key.assert_awaited_once_with("old")

    async def test_rotate_key_wraps_revocation_error(self, remote):
        remote.api.get_api_keys = AsyncMock(side_effect=RuntimeError("offline"))

        with pytest.raises(ApiKeyError, match="revoke"):
            await remote.auth.rotate_key()


class TestExternalTokens:
    async def test_set_token_sends_optional_metadata(self, remote):
        remote.api.post_external_system_token = AsyncMock(return_value={"status": "created"})

        result = await remote.auth.set_external_token(
            "hass", "ws-ha-api", "secret", description="Home", url="http://ha.local"
        )

        assert result == {"status": "created"}
        remote.api.post_external_system_token.assert_awaited_once_with(
            "hass",
            {
                "token_id": "ws-ha-api",
                "name": "Integration",
                "token": "secret",
                "description": "Home",
                "url": "http://ha.local",
            },
        )

    async def test_set_token_falls_back_to_update_after_validation_error(self, remote):
        body = {"token_id": "ws-ha-api", "name": "Integration", "token": "secret"}
        remote.api.post_external_system_token = AsyncMock(side_effect=HTTPError(422, "exists"))
        remote.api.put_external_system_token = AsyncMock(return_value={"status": "updated"})

        assert await remote.auth.set_external_token("hass", "ws-ha-api", "secret") == {
            "status": "updated"
        }
        remote.api.put_external_system_token.assert_awaited_once_with("hass", "ws-ha-api", body)

    async def test_update_delete_and_token_queries(self, remote):
        remote._ensure_awake = AsyncMock()
        remote.api.put_external_system_token = AsyncMock(return_value={"status": "updated"})
        remote.api.delete_external_system_token = AsyncMock()
        remote.api.get_external_systems = AsyncMock(return_value=[{"system": "hass"}])
        remote.api.get_external_system = AsyncMock(return_value=[{"token_id": "ws-ha-api"}])

        assert await remote.auth.update_external_token("hass", "ws-ha-api", "new-secret") == {
            "status": "updated"
        }
        await remote.auth.delete_external_token("hass", "ws-ha-api")

        assert await remote.auth.has_system("hass") is True
        assert await remote.auth.has_system("other") is False
        assert await remote.auth.system_has_token("hass") is True
        remote.api.delete_external_system_token.assert_awaited_once_with("hass", "ws-ha-api")
