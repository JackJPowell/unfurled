"""Tests for software update download state and progress."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from unfurled import Remote
from unfurled.helpers.models import UpdateInfo, UpdateType

from .conftest import ws_software_update_progress, ws_software_update_start


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
        ({}, None, 0, 0, False),
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
