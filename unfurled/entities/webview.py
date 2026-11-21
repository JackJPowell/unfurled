"""Web view entities rendered and controlled by the Remote UI."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, cast

from .webview_options import WebViewOptions, WebViewUpdate

if TYPE_CHECKING:
    from ..remote import Remote


class WebView:
    """A user-defined or integration-provided web view (firmware 2.14.0+).

    Options contain the configured start URL, not the current page URL or title.
    Core responses omit secrets and expose has_* flags instead. Commands go to
    Core, which routes them to the Remote UI, including for external web views.
    """

    def __init__(self, data: dict, remote: Remote) -> None:
        self._remote = remote
        self._data: dict = {}
        self._apply_data(data)

    def _apply_data(self, data: dict) -> None:
        # Changed top-level fields are complete values, including options.
        # Replacing options also removes stale overrides and cleared options.
        self._data.update(deepcopy(data))

    @property
    def id(self) -> str:
        """Unique entity identifier."""
        return self._data.get("entity_id", "")

    @property
    def name(self) -> str:
        """Name in the Remote's locale."""
        return self._remote.settings.get_text_for_locale(
            self._data.get("name"), default_text=self.id
        )

    @property
    def description(self) -> str:
        """Description in the Remote's locale."""
        return self._remote.settings.get_text_for_locale(
            self._data.get("description"), default_text=""
        )

    @property
    def icon(self) -> str | None:
        """Configured icon, if any."""
        return self._data.get("icon")

    @property
    def integration_id(self) -> str:
        """Integration owning this entity."""
        return self._data.get("integration_id", "")

    @property
    def options(self) -> WebViewOptions:
        """Copy of options returned by Core, including secret-presence flags."""
        return cast(WebViewOptions, deepcopy(self._data.get("options", {})))

    @property
    def url(self) -> str:
        """Configured start page."""
        return self._data.get("options", {}).get("url", "")

    @property
    def editable(self) -> bool:
        """Whether this is a user-defined web view with editable options."""
        return bool(self._data.get("options", {}).get("editable", False))

    @property
    def state(self) -> str:
        """Optional availability state; ON does not mean the page is open."""
        return self._data.get("attributes", {}).get("state", "UNKNOWN")

    async def refresh(self) -> None:
        """Fetch the full definition from Core."""
        self._data = deepcopy(await self._remote.api.get_webview(self.id))

    async def update(self, body: WebViewUpdate | dict) -> None:
        """Update metadata/options, caching only Core's redacted response.

        For external web views only options.user overrides can be changed.
        Omitted fields and explicit nulls are passed unchanged to Core.
        """
        self._data = deepcopy(await self._remote.api.patch_webview(self.id, body))

    async def delete(self) -> None:
        """Delete this user web view, or unconfigure this external web view."""
        await self._remote.api.delete_webview(self.id)
        self._remote._webviews.pop(self.id, None)

    async def _command(self, command: str) -> None:
        await self._remote._ensure_awake()
        await self._remote.api.put_entity_command(self.id, f"webview.{command}")

    async def open(self) -> None:
        """Wake the display and show the start page fullscreen."""
        await self._command("open")

    async def reload(self) -> None:
        """Reload this web view if it is currently shown."""
        await self._command("reload")

    async def clear_cache(self) -> None:
        """Clear the shared HTTP cache of all web views and reload this view."""
        await self._command("clear_cache")

    async def clear_data(self) -> None:
        """Clear shared website data of all web views and reload this view."""
        await self._command("clear_data")
