"""Typed web view payloads, using ordinary dictionaries on the wire.

Missing keys stay omitted; None is an explicit reset in update payloads. Core
performs validation, including ranges, URL origins and Remote-specific buttons.
Secrets are write-only; responses contain has_* flags instead of secret values.
"""

from __future__ import annotations

from typing import Literal, Required, TypedDict

WebViewButton = Literal[
    "BACK",
    "DPAD_UP",
    "DPAD_DOWN",
    "DPAD_LEFT",
    "DPAD_RIGHT",
    "DPAD_MIDDLE",
    "VOLUME_UP",
    "VOLUME_DOWN",
    "CHANNEL_UP",
    "CHANNEL_DOWN",
    "MUTE",
    "PREV",
    "PLAY",
    "NEXT",
    "STOP",
    "RECORD",
    "MENU",
    "RED",
    "GREEN",
    "YELLOW",
    "BLUE",
]

WebViewButtonFunction = Literal[
    "zoom_in",
    "zoom_out",
    "zoom_reset",
    "scroll_up",
    "scroll_down",
    "page_up",
    "page_down",
    "top",
    "bottom",
    "back",
    "forward",
    "back_or_close",
    "reload",
    "stop",
    "close",
    "toggle_navigation",
    "focus_next",
    "focus_previous",
    "none",
]

WebViewButtonKey = Literal[
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "Enter",
    "Escape",
    "Backspace",
    "Tab",
    "PageUp",
    "PageDown",
    "Home",
    "End",
    "ContextMenu",
    "BrowserBack",
    "BrowserForward",
    "BrowserRefresh",
    "BrowserStop",
    "Exit",
    "AudioVolumeUp",
    "AudioVolumeDown",
    "AudioVolumeMute",
    "ChannelUp",
    "ChannelDown",
    "MediaPlayPause",
    "MediaPlay",
    "MediaPause",
    "MediaStop",
    "MediaRecord",
    "MediaRewind",
    "MediaFastForward",
    "MediaTrackPrevious",
    "MediaTrackNext",
    "ColorF0Red",
    "ColorF1Green",
    "ColorF2Yellow",
    "ColorF3Blue",
]


class WebViewPassAction(TypedDict):
    """Keep the button's action from the underlying screen or activity."""

    action: Literal["pass"]


class WebViewFunctionAction(TypedDict):
    """Run a web view function."""

    action: Literal["function"]
    function: WebViewButtonFunction


class WebViewKeyAction(TypedDict):
    """Send a key event to the page."""

    action: Literal["key"]
    key: WebViewButtonKey


WebViewButtonAction = WebViewPassAction | WebViewFunctionAction | WebViewKeyAction
WebViewButtons = dict[WebViewButton, WebViewButtonAction | None]


class WebViewAuth(TypedDict, total=False):
    """Origin-scoped authentication; secrets are only sent in requests."""

    type: Literal["none", "basic", "bearer", "header"]
    username: str
    password: str
    token: str
    header_name: str
    header_value: str
    has_password: bool
    has_token: bool
    has_header_value: bool


class WebViewTLS(TypedDict, total=False):
    """Certificate exception for the start URL's host."""

    certificate_sha256: str
    allow_invalid_certificate: bool


class WebViewCookie(TypedDict, total=False):
    """Configured cookie; value is secret and may be omitted when updating."""

    name: Required[str]
    value: str
    has_value: bool
    domain: str
    path: str
    secure: bool
    http_only: bool
    expires: str


class WebViewUserOptions(TypedDict, total=False):
    """Allowed overrides for integration-provided web views."""

    zoom: int | None
    reload_interval: int | None
    show_navigation: bool | None
    buttons: WebViewButtons | None


class WebViewOptions(TypedDict, total=False):
    """Options for definitions, responses and partial updates.

    url is required when creating without clone_from and cannot be reset.
    editable and has_* flags are response-only. User-defined views cannot set
    user; integration views can only update user. Core enforces these rules.
    """

    url: str
    auth: WebViewAuth | None
    tls: WebViewTLS | None
    cookies: list[WebViewCookie] | None
    user_agent: str | None
    zoom: int | None
    background_color: str | None
    show_navigation: bool | None
    reload_interval: int | None
    buttons: WebViewButtons | None
    user: WebViewUserOptions | None
    editable: bool


class WebViewCreate(TypedDict, total=False):
    """Create a user-defined web view or clone one with a new name."""

    name: Required[dict[str, str]]
    options: WebViewOptions
    clone_from: str
    icon: str
    description: dict[str, str]


class WebViewUpdate(TypedDict, total=False):
    """Patch only the supplied fields of a web view."""

    name: dict[str, str]
    options: WebViewOptions
    icon: str
    description: dict[str, str]


class WebViewSettingsUpdate(TypedDict, total=False):
    """Patch global settings; None button actions reset to model defaults."""

    allow_invalid_certificates: bool
    color_scheme: Literal["light", "dark"]
    website_data: Literal["keep", "clear_cache_on_close", "clear_all_on_close"]
    buttons: WebViewButtons
