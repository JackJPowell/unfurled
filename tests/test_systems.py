"""Tests for software update download state and progress."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from aioresponses import aioresponses

from unfurled import Remote
from unfurled.helpers.exceptions import HTTPError
from unfurled.helpers.models import UpdateInfo, UpdateType

from .conftest import BASE_URL, ws_software_update_progress, ws_software_update_start


def software_update_progress(progress: dict) -> str:
    return json.dumps(
        {
            "msg": "software_update",
            "msg_data": {"event_type": "PROGRESS", "progress": progress},
        }
    )


def test_download_state_defaults_to_none():
    assert UpdateInfo().download_state is None


@pytest.mark.parametrize(
    "progress",
    [
        {"state": "DOWNLOAD", "download_percent": 45, "download_bytes": 4500},
        {"state": "DOWNLOAD", "download_percent": 100},
    ],
)
async def test_download_progress(remote: Remote, progress: dict):
    await remote._handle_ws_message(software_update_progress(progress))

    assert remote.system.update_info.download_state == "DOWNLOAD"
    assert remote.system.update_info.download_percent == progress["download_percent"]
    assert remote._last_update_type == UpdateType.SOFTWARE


async def test_download_failure(remote: Remote):
    await remote._handle_ws_message(ws_software_update_start())
    await remote._handle_ws_message(
        software_update_progress({"state": "DOWNLOAD", "download_percent": 45})
    )
    remote.system.update_info.update_percent = 20

    await remote._handle_ws_message(software_update_progress({"state": "FAILURE"}))

    assert remote.system.update_info.download_state == "FAILURE"
    assert remote.system.update_info.download_percent == 45
    assert remote.system.update_info.in_progress is False
    assert remote.system.update_info.update_percent == 0
    assert remote._last_update_type == UpdateType.SOFTWARE


async def test_download_retry_after_failure(remote: Remote):
    await remote._handle_ws_message(software_update_progress({"state": "FAILURE"}))
    assert remote.system.update_info.download_state == "FAILURE"

    await remote._handle_ws_message(
        software_update_progress({"state": "DOWNLOAD", "download_percent": 10})
    )
    assert remote.system.update_info.download_state == "DOWNLOAD"
    assert remote.system.update_info.download_percent == 10

    await remote._handle_ws_message(
        software_update_progress({"state": "DOWNLOAD", "download_percent": 100})
    )
    assert remote.system.update_info.download_state == "DOWNLOAD"
    assert remote.system.update_info.download_percent == 100


@pytest.mark.parametrize(
    "message",
    [
        ws_software_update_start(),
        ws_software_update_progress("START"),
        ws_software_update_progress("RUN"),
        ws_software_update_progress("PROGRESS"),
    ],
)
async def test_installation_clears_download_failure(remote: Remote, message: str):
    await remote._handle_ws_message(software_update_progress({"state": "FAILURE"}))

    await remote._handle_ws_message(message)

    assert remote.system.update_info.download_state is None


@pytest.mark.parametrize(
    ("status", "expected_state", "download_percent", "update_percent", "in_progress"),
    [
        (
            {"state": "PENDING", "progress": {"state": "DOWNLOAD", "download_percent": 45}},
            "DOWNLOAD",
            45,
            0,
            False,
        ),
        (
            {"state": "PENDING", "progress": {"state": "DOWNLOAD", "download_percent": 100}},
            "DOWNLOAD",
            100,
            0,
            False,
        ),
        ({"state": "ERROR", "progress": {"state": "FAILURE"}}, "FAILURE", 0, 0, False),
        ({"state": "DOWNLOAD", "download_percent": 45}, "DOWNLOAD", 45, 0, False),
        ({"state": "FAILURE"}, "FAILURE", 0, 0, False),
        ({"state": "START"}, None, 0, 0, True),
        (
            {"state": "INSTALLING", "progress": {"state": "RUN", "update_percent": 30}},
            None,
            0,
            30,
            True,
        ),
        ({"state": "UPDATING", "update_percent": 70}, None, 0, 70, True),
        ({}, "FAILURE", 0, 0, False),
    ],
)
async def test_get_update_status(
    remote: Remote,
    status: dict,
    expected_state: str | None,
    download_percent: int,
    update_percent: int,
    in_progress: bool,
):
    remote.system.update_info.download_state = "FAILURE"
    remote.api.get_system_update_latest = AsyncMock(return_value=status)

    result = await remote.system.get_update_status()

    assert result is status
    assert remote.system.update_info.download_state == expected_state
    assert remote.system.update_info.download_percent == download_percent
    assert remote.system.update_info.update_percent == update_percent
    assert remote.system.update_info.in_progress is in_progress
    remote.api.get_system_update_latest.assert_awaited_once_with()


async def test_update_firmware_downloads_available_undownloaded_update(remote: Remote):
    update = {"id": "ota-2.8.0", "version": "2.8.0", "state": "PENDING"}
    with aioresponses() as m:
        m.put(f"{BASE_URL}system/update", payload={"available": [update]})
        m.post(f"{BASE_URL}system/update/{update['id']}", payload={"state": "PENDING"})

        metadata = await remote.system.force_update_check()
        selected_id = remote.system.update_info.available[0]["id"]
        result = await remote.system.update_firmware(update_id=selected_id)

    assert metadata["available"] == [update]
    assert remote.system.update_info.available == [update]
    assert result == "PENDING"


async def test_get_update_status_uses_concrete_id(remote: Remote):
    with aioresponses() as m:
        m.get(f"{BASE_URL}system/update/ota-2.8.0", payload={"state": "PENDING"})
        result = await remote.system.get_update_status(update_id="ota-2.8.0")

    assert result == {"state": "PENDING"}


@pytest.mark.parametrize("state", ["PENDING", "ERROR", "DOWNLOADED", "INSTALLING"])
async def test_download_only_uses_same_id_for_status_and_post(remote: Remote, state: str):
    with aioresponses() as m:
        m.get(f"{BASE_URL}system/update/selected-id", payload={"state": state})
        if state in {"PENDING", "ERROR"}:
            m.post(f"{BASE_URL}system/update/selected-id", payload={"state": "PENDING"})

        result = await remote.system.update_firmware(update_id="selected-id", download_only=True)

        assert len(m.requests) == (2 if state in {"PENDING", "ERROR"} else 1)
    assert result == ("PENDING" if state in {"PENDING", "ERROR"} else state)


@pytest.mark.parametrize("download_only", [False, True])
async def test_update_firmware_without_id_keeps_latest_path(remote: Remote, download_only: bool):
    with aioresponses() as m:
        if download_only:
            m.get(f"{BASE_URL}system/update/latest", payload={"state": "PENDING"})
        m.post(f"{BASE_URL}system/update/latest", status=204)

        result = await remote.system.update_firmware(download_only=download_only)

    assert result == "OK"


@pytest.mark.parametrize("update_id", [None, "selected-id"])
@pytest.mark.parametrize("operation", ["get", "post", "download_status", "download_post"])
@pytest.mark.parametrize("status_code", [404, 409, 500])
async def test_update_http_errors_propagate_without_fallback(
    remote: Remote, update_id: str | None, operation: str, status_code: int
):
    url = f"{BASE_URL}system/update/{update_id if update_id is not None else 'latest'}"
    with aioresponses() as m:
        if operation == "download_post":
            m.get(url, payload={"state": "PENDING"})
        method = m.get if operation in {"get", "download_status"} else m.post
        method(
            url, status=status_code, payload={"code": "UPDATE_ERROR", "message": "Cannot update"}
        )

        with pytest.raises(HTTPError) as exc_info:
            if operation == "get":
                await remote.system.get_update_status(update_id=update_id)
            else:
                await remote.system.update_firmware(
                    update_id=update_id, download_only=operation.startswith("download_")
                )

        assert len(m.requests) == (2 if operation == "download_post" else 1)
    assert exc_info.value.status_code == status_code
    assert "Cannot update" in exc_info.value.message


@pytest.mark.parametrize(
    "status",
    [{}, {"progress": {}}, {"state": "PENDING"}, {"progress": {"update_percent": 20}}],
)
@pytest.mark.parametrize("state", ["DOWNLOAD", "FAILURE"])
async def test_partial_status_preserves_websocket_progress(
    remote: Remote, status: dict, state: str
):
    await remote._handle_ws_message(
        software_update_progress({"state": "DOWNLOAD", "download_percent": 45})
    )
    if state == "FAILURE":
        await remote._handle_ws_message(software_update_progress({"state": "FAILURE"}))
    remote.system.update_info.update_percent = 20
    remote.api.get_system_update_by_id = AsyncMock(return_value=status)

    await remote.system.get_update_status(update_id="selected-id")

    assert remote.system.update_info.download_state == state
    assert remote.system.update_info.download_percent == 45
    assert remote.system.update_info.update_percent == 20


async def test_partial_status_preserves_installation_progress(remote: Remote):
    await remote._handle_ws_message(ws_software_update_start())
    await remote._handle_ws_message(ws_software_update_progress("PROGRESS", current_percent=70))
    remote.api.get_system_update_by_id = AsyncMock(
        return_value={"progress": {"download_percent": 0}}
    )

    await remote.system.get_update_status(update_id="selected-id")

    assert remote.system.update_info.in_progress is True
    assert remote.system.update_info.update_percent == 70


@pytest.mark.parametrize("nested", [False, True])
async def test_explicit_zero_progress_updates_cache(remote: Remote, nested: bool):
    remote.system.update_info.download_percent = 45
    remote.system.update_info.update_percent = 70
    progress = {"download_percent": 0, "update_percent": 0}
    status = {"progress": progress} if nested else progress
    remote.api.get_system_update_by_id = AsyncMock(return_value=status)

    await remote.system.get_update_status(update_id="selected-id")

    assert remote.system.update_info.download_percent == 0
    assert remote.system.update_info.update_percent == 0


async def test_nested_progress_takes_precedence_including_zero(remote: Remote):
    remote.api.get_system_update_by_id = AsyncMock(
        return_value={
            "download_percent": 45,
            "update_percent": 70,
            "progress": {"download_percent": 0, "update_percent": 0},
        }
    )

    await remote.system.get_update_status(update_id="selected-id")

    assert remote.system.update_info.download_percent == 0
    assert remote.system.update_info.update_percent == 0


@pytest.mark.parametrize("force_check", [False, True])
@pytest.mark.parametrize("state", ["DOWNLOAD", "FAILURE"])
async def test_metadata_refresh_preserves_websocket_progress(
    remote: Remote, force_check: bool, state: str
):
    await remote._handle_ws_message(ws_software_update_start())
    await remote._handle_ws_message(
        software_update_progress({"state": "DOWNLOAD", "download_percent": 45})
    )
    if state == "FAILURE":
        await remote._handle_ws_message(software_update_progress({"state": "FAILURE"}))
    update = {"id": "selected-id", "version": "2.8.0"}
    metadata = {"installed_version": "2.7.0", "available": [update]}
    with aioresponses() as m:
        method = m.put if force_check else m.get
        method(f"{BASE_URL}system/update", payload=metadata)
        if force_check:
            assert await remote.system.force_update_check() == metadata
        else:
            await remote.system._fetch_update_info()

    assert remote.system.update_info.available == [update]
    assert remote.system.update_info.download_state == state
    assert remote.system.update_info.download_percent == 45
    assert remote.system.update_info.in_progress is (state == "DOWNLOAD")
