"""unfurled - Unfolded Circle Python API library.

Public interface::

    from unfurled import Remote, Dock, discover_remotes
    from unfurled.helpers.exceptions import RemoteIsSleeping, NoActivityRunning
    from unfurled.helpers.models import ActivityState, PowerMode, UpdateType
"""

from .api import CoreAPI
from .dock import Dock, ExternalPort
from .entities.activity import Activity, ActivityGroup
from .entities.ir import IR, IRCode, IRCodeset, IRCustomCode, IREmitter
from .entities.macro import Macro
from .entities.media_player import MediaPlayerEntity
from .helpers.discovery import DiscoveredDevice, discover_remotes, discover_remotes_sync
from .helpers.exceptions import (
    ApiKeyError,
    ApiKeyNotFound,
    AuthenticationError,
    ConnectionError,
    DiscoveryError,
    EntityCommandError,
    HTTPError,
    IntegrationInstanceAmbiguous,
    IntegrationNotFound,
    InvalidButtonCommand,
    InvalidEntitySelection,
    InvalidIRFormat,
    NoActivityRunning,
    NoEmitterFound,
    RemoteIsSleeping,
    SetupNotFound,
    SystemCommandNotFound,
    TokenRegistrationError,
    UnfurledError,
)
from .helpers.helpers import Helpers
from .helpers.models import (
    ActivityState,
    BluetoothSettings,
    ButtonSettings,
    DeviceInfo,
    DisplaySettings,
    Feature,
    HapticSettings,
    LocalizationInfo,
    MediaPlayerState,
    NetworkSettings,
    PowerMode,
    PowerSavingSettings,
    ProfileSettings,
    RemoteCommand,
    RemoteFeatureFlags,
    RemoteState,
    RemoteStats,
    SoftwareUpdateSettings,
    SoundSettings,
    UpdateInfo,
    UpdateType,
    VoiceSettings,
    WiFiSettings,
)
from .helpers.websocket import DockWebSocketClient, RemoteWebSocketClient, WebSocketClient
from .remote import Remote
from .setup import (
    CheckboxSetupField,
    ConfirmationSetupAction,
    DropdownSetupField,
    InputSetupAction,
    IntegrationEntity,
    IntegrationSetupDefinition,
    IntegrationSetupSession,
    LabelSetupField,
    LocalizedText,
    NumberSetupField,
    PasswordSetupField,
    SetupAction,
    SetupField,
    SetupOption,
    SetupPage,
    SetupResult,
    SetupState,
    TextareaSetupField,
    TextSetupField,
    UnknownSetupField,
)
from .submodules.authentication import Authentication
from .submodules.base import RemoteModule
from .submodules.integrations import Integrations
from .submodules.settings import Settings
from .submodules.systems import System

__version__ = "0.4.0"

__all__ = [
    # Core
    "Remote",
    "Dock",
    "ExternalPort",
    "CoreAPI",
    # Sub-objects
    "RemoteModule",
    "Authentication",
    "Integrations",
    "IntegrationSetupSession",
    "IntegrationSetupDefinition",
    "Settings",
    "System",
    "IR",
    "Helpers",
    # Domain
    "Activity",
    "ActivityGroup",
    "IREmitter",
    "IRCode",
    "IRCodeset",
    "IRCustomCode",
    "Macro",
    "MediaPlayerEntity",
    # WebSocket
    "WebSocketClient",
    "RemoteWebSocketClient",
    "DockWebSocketClient",
    # Discovery
    "discover_remotes",
    "discover_remotes_sync",
    "DiscoveredDevice",
    # Models
    "ActivityState",
    "MediaPlayerState",
    "PowerMode",
    "RemoteCommand",
    "UpdateType",
    "DeviceInfo",
    "UpdateInfo",
    "Settings",
    "RemoteState",
    "RemoteFeatureFlags",
    "RemoteStats",
    "DisplaySettings",
    "ButtonSettings",
    "SoundSettings",
    "HapticSettings",
    "PowerSavingSettings",
    "NetworkSettings",
    "WiFiSettings",
    "SoftwareUpdateSettings",
    "BluetoothSettings",
    "ProfileSettings",
    "Feature",
    "VoiceSettings",
    "LocalizationInfo",
    "LocalizedText",
    "NumberSetupField",
    "TextSetupField",
    "TextareaSetupField",
    "PasswordSetupField",
    "CheckboxSetupField",
    "DropdownSetupField",
    "LabelSetupField",
    "UnknownSetupField",
    "SetupState",
    "SetupOption",
    "SetupField",
    "SetupPage",
    "SetupAction",
    "InputSetupAction",
    "ConfirmationSetupAction",
    "SetupResult",
    "IntegrationEntity",
    # Exceptions
    "UnfurledError",
    "HTTPError",
    "AuthenticationError",
    "ConnectionError",
    "RemoteIsSleeping",
    "NoActivityRunning",
    "InvalidButtonCommand",
    "EntityCommandError",
    "InvalidIRFormat",
    "NoEmitterFound",
    "ApiKeyNotFound",
    "ApiKeyError",
    "IntegrationNotFound",
    "SetupNotFound",
    "IntegrationInstanceAmbiguous",
    "InvalidEntitySelection",
    "SystemCommandNotFound",
    "TokenRegistrationError",
    "DiscoveryError",
]
