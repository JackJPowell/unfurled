"""Integrations sub-object - integration instances and driver lifecycle."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from ..api import IntegrationInstanceCommand
from ..helpers.exceptions import (
    HTTPError,
    IntegrationInstanceAmbiguous,
    IntegrationNotFound,
    InvalidEntitySelection,
    SetupNotFound,
)
from ..setup import (
    IntegrationEntity,
    IntegrationSetupDefinition,
    IntegrationSetupSession,
    LocalizedName,
    LocalizedText,
    SetupResult,
    SetupState,
    entity_from_core,
    localized_name_values,
    page_from_core,
    result_from_core,
    string_values,
)
from .base import RemoteModule


class Integrations(RemoteModule):
    """Manages integration instances and driver setup flows.

    Accessed via ``remote.integrations``.

    Example::

        instance = await remote.integrations.get_by_driver("hass")
        await remote.integrations.send_command(instance["id"], IntegrationInstanceCommand.CONNECT)
    """

    async def get_by_driver(self, driver_id: str) -> dict:
        """Return the integration instance for the given driver ID.

        Args:
            driver_id: Driver identifier (e.g. ``"hass"``).

        Raises:
            :class:`~unfurled.exceptions.IntegrationNotFound`: if no matching instance exists.
        """
        instances = await self._api.get_integrations()
        match = next((i for i in instances if i.get("driver_id") == driver_id), None)
        if not match:
            raise IntegrationNotFound(f"No integration for driver '{driver_id}'")
        return match

    async def send_command(
        self,
        integration_id: str,
        cmd: IntegrationInstanceCommand | None = None,
    ) -> dict:
        """Send a lifecycle command to an integration instance.

        Args:
            integration_id: The integration instance ID.
            cmd: Command to send (e.g. ``CONNECT``, ``DISCONNECT``).
        """
        return await self._api.put_integration(integration_id, cmd)

    async def get_entities(self, integration_id: str, *, reload: bool = False) -> list[dict]:
        """Return entities configured for an integration instance."""
        return await self._api.get_integration_entities(integration_id, reload=reload)

    async def configure_entities(self, integration_id: str, entity_ids: list[str]) -> list[str]:
        """Replace an integration instance's configured entity IDs."""
        return await self._api.post_integration_entities(integration_id, entity_ids)

    def setup(self, driver_id: str, instance_id: str | None = None) -> IntegrationSetupSession:
        """Create a stateful setup session for one integration driver."""
        return IntegrationSetupSession(self, driver_id, instance_id)

    async def get_setup_definition(self, driver_id: str) -> IntegrationSetupDefinition:
        """Return the typed static setup metadata for a driver."""
        driver = await self._api.get_driver(driver_id)
        return IntegrationSetupDefinition(
            driver_id,
            LocalizedText.from_core(driver.get("name")),
            page_from_core(driver.get("setup_data_schema")),
            raw=dict(driver),
        )

    async def start_setup(
        self,
        driver_id: str,
        *,
        reconfigure: bool = False,
        setup_data: dict[str, Any] | None = None,
        name: LocalizedName = None,
    ) -> SetupResult:
        """Start an integration driver setup flow.

        Args:
            driver_id: The driver to set up.
            reconfigure: If ``True``, reconfigure an existing instance.
            setup_data: Optional key/value pairs passed to the driver.
        """
        body: dict[str, Any] = {"driver_id": driver_id, "reconfigure": reconfigure}
        if setup_data is not None:
            body["setup_data"] = string_values(setup_data)
        if localized_name := localized_name_values(name):
            body["name"] = localized_name
        return result_from_core(driver_id, await self._api.post_integration_setup(body))

    async def get_setup(self, driver_id: str) -> SetupResult:
        """Return the current setup state for a driver."""
        try:
            return result_from_core(driver_id, await self._api.get_integration_setup(driver_id))
        except HTTPError as error:
            if error.status_code == 404:
                raise SetupNotFound(f"No active setup for driver '{driver_id}'") from error
            raise

    async def submit_setup_input(self, driver_id: str, values: dict[str, Any]) -> SetupResult:
        """Submit a dynamic setup form step."""
        return result_from_core(
            driver_id,
            await self._api.put_integration_setup(driver_id, string_values(values)),
        )

    async def confirm_setup(self, driver_id: str, value: bool = True) -> SetupResult:
        """Respond to a setup confirmation step."""
        return result_from_core(
            driver_id, await self._api.put_integration_setup(driver_id, confirm=value)
        )

    async def cancel_setup(self, driver_id: str) -> None:
        """Cancel an active setup session."""
        try:
            await self._api.delete_integration_setup(driver_id)
        except HTTPError as error:
            if error.status_code == 404:
                raise SetupNotFound(f"No active setup for driver '{driver_id}'") from error
            raise

    async def wait_for_setup(
        self,
        driver_id: str,
        preferred_instance_id: str | None = None,
        *,
        attempts: int = 30,
        interval: float = 0.75,
    ) -> SetupResult:
        """Read setup status, recovering success after Core removes its session."""
        for attempt in range(attempts):
            try:
                result = await self.get_setup(driver_id)
                if result.state != SetupState.OK:
                    return result
                instance_id = await self.resolve_instance(driver_id, preferred_instance_id)
                return replace(result, instance_id=instance_id)
            except SetupNotFound:
                active = await self._api.get_integration_setups()
                if driver_id in active:
                    if attempt + 1 < attempts:
                        await asyncio.sleep(interval)
                        continue
                    raise
                try:
                    instance_id = await self.resolve_instance(driver_id, preferred_instance_id)
                except IntegrationNotFound:
                    if attempt + 1 < attempts:
                        await asyncio.sleep(interval)
                        continue
                    raise
                return SetupResult(
                    driver_id,
                    state=SetupState.OK,
                    instance_id=instance_id,
                    setup_id=driver_id,
                )
        raise SetupNotFound(f"No active setup for driver '{driver_id}'")

    async def resolve_instance(
        self, driver_id: str, preferred_instance_id: str | None = None
    ) -> str:
        """Resolve a setup's configured instance without silently choosing one."""
        candidates = await self._driver_instances(driver_id)
        if preferred_instance_id and any(
            item.get("integration_id") == preferred_instance_id for item in candidates
        ):
            return preferred_instance_id
        if len(candidates) == 1:
            return str(candidates[0]["integration_id"])
        main_id = f"{driver_id}.main"
        if any(item.get("integration_id") == main_id for item in candidates):
            return main_id
        if not candidates:
            raise IntegrationNotFound(f"No integration instance for driver '{driver_id}'")
        raise IntegrationInstanceAmbiguous(
            f"Multiple integration instances found for driver '{driver_id}'"
        )

    async def available_entities(self, integration_id: str) -> list[IntegrationEntity]:
        """Return all unconfigured entities produced by an integration."""
        return await self._integration_entities(integration_id, filter="NEW", reload=True)

    async def configured_entities(self, integration_id: str) -> list[IntegrationEntity]:
        """Return all configured entities belonging to an integration."""
        entities: list[IntegrationEntity] = []
        page = 1
        while page <= 1000:
            batch = await self._api.get_entities(integration_ids=[integration_id], page=page)
            entities.extend(
                entity for item in batch if (entity := entity_from_core(item)) is not None
            )
            if len(batch) < 100:
                return entities
            page += 1
        raise RuntimeError("Configured entity list exceeded the supported pagination limit")

    async def add_entities(self, integration_id: str, entity_ids: list[str]) -> list[str]:
        """Add explicitly selected available entities to an integration."""
        if not entity_ids:
            raise InvalidEntitySelection("Select at least one entity to add")
        available_ids = {entity.id for entity in await self.available_entities(integration_id)}
        selected = [entity_id for entity_id in entity_ids if entity_id in available_ids]
        if not selected:
            raise InvalidEntitySelection("Select at least one available entity to add")
        return await self._api.post_integration_entities(integration_id, selected)

    async def remove_entities(self, integration_id: str, entity_ids: list[str]) -> list[str]:
        """Remove explicitly selected configured entities from an integration."""
        if not entity_ids:
            raise InvalidEntitySelection("Select at least one entity to remove")
        configured_ids = {entity.id for entity in await self.configured_entities(integration_id)}
        removable = [entity_id for entity_id in entity_ids if entity_id in configured_ids]
        if not removable:
            raise InvalidEntitySelection("Select at least one configured entity to remove")
        await self._api.delete_entities(removable)
        return removable

    async def _integration_entities(
        self, integration_id: str, *, filter: str, reload: bool
    ) -> list[IntegrationEntity]:
        entities: list[IntegrationEntity] = []
        page = 1
        while page <= 1000:
            batch = await self._api.get_integration_entities(
                integration_id, filter=filter, reload=reload and page == 1, page=page
            )
            entities.extend(
                entity for item in batch if (entity := entity_from_core(item)) is not None
            )
            if len(batch) < 100:
                return entities
            page += 1
        raise RuntimeError("Available entity list exceeded the supported pagination limit")

    async def _driver_instances(self, driver_id: str) -> list[dict]:
        """Read every instance page so resolution never depends on page one."""
        instances: list[dict] = []
        page = 1
        while page <= 1000:
            batch = await self._api.get_integrations(driver_id=driver_id, page=page)
            instances.extend(item for item in batch if item.get("integration_id"))
            if len(batch) < 100:
                return instances
            page += 1
        raise RuntimeError("Integration instance list exceeded the supported pagination limit")
