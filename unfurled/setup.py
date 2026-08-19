"""Typed integration setup flows for :mod:`unfurled`."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .submodules.integrations import Integrations


class SetupState(StrEnum):
    """States reported by a Remote integration setup flow."""

    SETUP = "SETUP"
    WAIT_USER_ACTION = "WAIT_USER_ACTION"
    OK = "OK"
    ERROR = "ERROR"


@dataclass(frozen=True)
class LocalizedText:
    """Core language text with a convenient locale-aware display method."""

    values: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_core(cls, value: Any) -> LocalizedText:
        if isinstance(value, str):
            return cls({"en": value})
        if not isinstance(value, dict):
            return cls()
        return cls({str(key): str(text) for key, text in value.items() if isinstance(text, str)})

    def text(self, locale: str | None = None, fallback: str = "") -> str:
        if locale:
            normalized = locale.replace("_", "-")
            candidates = (locale, normalized, normalized.split("-", 1)[0])
            for candidate in candidates:
                if candidate in self.values:
                    return self.values[candidate]
        return self.values.get("en") or next(iter(self.values.values()), fallback)


@dataclass(frozen=True)
class SetupOption:
    id: str
    label: LocalizedText


@dataclass(frozen=True)
class SetupField:
    id: str
    label: LocalizedText
    kind: str
    value: str | bool | LocalizedText | None = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    step: float | int | None = None
    decimals: int | None = None
    unit: LocalizedText = field(default_factory=LocalizedText)
    regex: str | None = None
    options: tuple[SetupOption, ...] = ()


@dataclass(frozen=True)
class NumberSetupField(SetupField):
    kind: Literal["number"] = field(init=False, default="number")


@dataclass(frozen=True)
class TextSetupField(SetupField):
    kind: Literal["text"] = field(init=False, default="text")


@dataclass(frozen=True)
class TextareaSetupField(SetupField):
    kind: Literal["textarea"] = field(init=False, default="textarea")


@dataclass(frozen=True)
class PasswordSetupField(SetupField):
    kind: Literal["password"] = field(init=False, default="password")


@dataclass(frozen=True)
class CheckboxSetupField(SetupField):
    kind: Literal["checkbox"] = field(init=False, default="checkbox")


@dataclass(frozen=True)
class DropdownSetupField(SetupField):
    kind: Literal["dropdown"] = field(init=False, default="dropdown")


@dataclass(frozen=True)
class LabelSetupField(SetupField):
    kind: Literal["label"] = field(init=False, default="label")


@dataclass(frozen=True)
class UnknownSetupField(SetupField):
    kind: Literal["unknown"] = field(init=False, default="unknown")


@dataclass(frozen=True)
class SetupPage:
    title: LocalizedText
    fields: tuple[SetupField, ...]


@dataclass(frozen=True)
class IntegrationSetupDefinition:
    """Static setup metadata published by an integration driver."""

    driver_id: str
    name: LocalizedText
    setup_data_schema: SetupPage | None = None


@dataclass(frozen=True)
class SetupAction:
    kind: str
    page: SetupPage | None = None
    title: LocalizedText = field(default_factory=LocalizedText)
    message1: LocalizedText = field(default_factory=LocalizedText)
    message2: LocalizedText = field(default_factory=LocalizedText)
    image: str | None = None


@dataclass(frozen=True)
class InputSetupAction(SetupAction):
    kind: Literal["input"] = field(init=False, default="input")


@dataclass(frozen=True)
class ConfirmationSetupAction(SetupAction):
    kind: Literal["confirmation"] = field(init=False, default="confirmation")


@dataclass(frozen=True)
class SetupResult:
    driver_id: str
    state: SetupState
    error: str = "NONE"
    action: SetupAction | None = None
    instance_id: str | None = None
    setup_id: str = ""


@dataclass(frozen=True)
class IntegrationEntity:
    id: str
    entity_type: str
    name: LocalizedText
    description: LocalizedText = field(default_factory=LocalizedText)
    area: str = ""
    device_class: str = ""
    icon: str = ""
    features: tuple[str, ...] = ()


def string_values(values: dict[str, Any] | None) -> dict[str, str]:
    """Convert UI-friendly values to Core's string setup-value contract."""
    if not isinstance(values, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, bool):
            result[key] = "true" if value else "false"
        elif value is None:
            result[key] = ""
        elif isinstance(value, (str, int, float)):
            result[key] = str(value)
    return result


def _field_from_core(setting: Any) -> SetupField | None:
    if not isinstance(setting, dict) or not isinstance(setting.get("field"), dict):
        return None
    field = setting["field"]
    field_id = str(setting.get("id") or "")
    label = LocalizedText.from_core(setting.get("label"))
    field_types = {
        "number": NumberSetupField,
        "text": TextSetupField,
        "textarea": TextareaSetupField,
        "password": PasswordSetupField,
        "checkbox": CheckboxSetupField,
        "dropdown": DropdownSetupField,
        "label": LabelSetupField,
    }
    for kind, field_type in field_types.items():
        spec = field.get(kind)
        if not isinstance(spec, dict):
            continue
        if kind == "dropdown":
            options = tuple(
                SetupOption(str(item.get("id") or ""), LocalizedText.from_core(item.get("label")))
                for item in spec.get("items", [])
                if isinstance(item, dict)
            )
            return field_type(
                field_id,
                label,
                "" if spec.get("value") is None else str(spec.get("value")),
                options=options,
            )
        if kind == "checkbox":
            return field_type(field_id, label, bool(spec.get("value", False)))
        if kind == "label":
            return field_type(
                field_id,
                label,
                LocalizedText.from_core(spec.get("value")),
            )
        return field_type(
            field_id,
            label,
            "" if spec.get("value") is None else str(spec.get("value")),
            minimum=spec.get("min"),
            maximum=spec.get("max"),
            step=spec.get("steps"),
            decimals=spec.get("decimals"),
            unit=LocalizedText.from_core(spec.get("unit")),
            regex=spec.get("regex") if isinstance(spec.get("regex"), str) else None,
        )
    return UnknownSetupField(field_id, label)


def page_from_core(value: Any) -> SetupPage | None:
    if not isinstance(value, dict):
        return None
    return SetupPage(
        LocalizedText.from_core(value.get("title")),
        tuple(
            field
            for setting in value.get("settings", [])
            if (field := _field_from_core(setting)) is not None
        ),
    )


def result_from_core(driver_id: str, value: Any) -> SetupResult:
    if not isinstance(value, dict):
        raise ValueError("Core returned an invalid integration setup response")
    state_value = str(value.get("state") or SetupState.SETUP)
    try:
        state = SetupState(state_value)
    except ValueError:
        state = SetupState.ERROR
    action_value = value.get("require_user_action")
    action: SetupAction | None = None
    if isinstance(action_value, dict) and isinstance(action_value.get("input"), dict):
        action = InputSetupAction(page=page_from_core(action_value["input"]))
    elif isinstance(action_value, dict) and isinstance(action_value.get("confirmation"), dict):
        confirmation = action_value["confirmation"]
        action = ConfirmationSetupAction(
            title=LocalizedText.from_core(confirmation.get("title")),
            message1=LocalizedText.from_core(confirmation.get("message1")),
            message2=LocalizedText.from_core(confirmation.get("message2")),
            image=confirmation.get("image") if isinstance(confirmation.get("image"), str) else None,
        )
    return SetupResult(
        driver_id,
        state,
        str(value.get("error") or "NONE"),
        action,
        setup_id=str(value.get("id") or driver_id),
    )


def entity_from_core(value: Any) -> IntegrationEntity | None:
    if not isinstance(value, dict):
        return None
    entity_id = str(value.get("entity_id") or "")
    entity_type = str(value.get("entity_type") or "")
    if not entity_id or not entity_type:
        return None
    return IntegrationEntity(
        entity_id,
        entity_type,
        LocalizedText.from_core(value.get("name")),
        LocalizedText.from_core(value.get("description")),
        str(value.get("area") or ""),
        str(value.get("device_class") or ""),
        str(value.get("icon") or ""),
        tuple(str(feature) for feature in value.get("features", []) if isinstance(feature, str)),
    )


class IntegrationSetupSession:
    """A reusable, stateful façade for one Remote integration setup flow."""

    def __init__(
        self, integrations: Integrations, driver_id: str, instance_id: str | None = None
    ) -> None:
        self._integrations = integrations
        self.driver_id = driver_id
        self.instance_id = instance_id

    async def start(
        self,
        *,
        setup_data: dict[str, Any] | None = None,
        reconfigure: bool = False,
        name: str | None = None,
    ) -> SetupResult:
        return await self._integrations.start_setup(
            self.driver_id, setup_data=setup_data, reconfigure=reconfigure, name=name
        )

    async def status(self) -> SetupResult:
        return await self._integrations.get_setup(self.driver_id)

    async def submit(self, values: dict[str, Any]) -> SetupResult:
        return await self._integrations.submit_setup_input(self.driver_id, values)

    async def confirm(self, value: bool = True) -> SetupResult:
        return await self._integrations.confirm_setup(self.driver_id, value)

    async def cancel(self) -> None:
        await self._integrations.cancel_setup(self.driver_id)

    async def wait_for_completion(
        self, *, attempts: int = 30, interval: float = 0.75
    ) -> SetupResult:
        return await self._integrations.wait_for_setup(
            self.driver_id, self.instance_id, attempts=attempts, interval=interval
        )

    async def available_entities(self) -> list[IntegrationEntity]:
        return await self._integrations.available_entities(await self._instance_id())

    async def configured_entities(self) -> list[IntegrationEntity]:
        return await self._integrations.configured_entities(await self._instance_id())

    async def add_entities(self, entity_ids: list[str]) -> list[str]:
        return await self._integrations.add_entities(await self._instance_id(), entity_ids)

    async def remove_entities(self, entity_ids: list[str]) -> list[str]:
        return await self._integrations.remove_entities(await self._instance_id(), entity_ids)

    async def resolve_instance(self) -> str:
        """Resolve and retain the integration instance targeted by this session."""
        return await self._instance_id()

    async def _instance_id(self) -> str:
        self.instance_id = await self._integrations.resolve_instance(
            self.driver_id, self.instance_id
        )
        return self.instance_id
