"""Helper functions for scanning activities on the Remote for orphaned or unused entities."""

import logging
from copy import deepcopy
from typing import Any

from ..submodules.base import RemoteModule
from .exceptions import UnfurledError

_LOG = logging.getLogger(__name__)


class Helpers(RemoteModule):
    """Manages all remote helper functions.

    Accessed via ``remote.helpers``. Populated automatically during
    :meth:`~unfurled.remote.Remote.init`. Individual helper functions can be used
    directly.

    Example::

        await remote.helpers.find_orphaned_entities(remote_url="http://192.168.1.100",
        api_key="your-api-key-here")
    """

    async def remove_activity_entities(
        self, activity_id: str, entity_ids: list[str], *, only_unused: bool = True
    ) -> dict:
        """Remove selected unused entities from one activity by default.

        Usage is checked against the freshly fetched activity. Referenced
        entities are skipped unless only_unused=False explicitly permits their
        removal. Forced removal changes sequences and controls; use with care.

        Fetches the latest definition, replaces changed button mappings and page
        items, then patches sequences, entity_ids and targeted slider settings.
        Unrelated settings and pages are preserved; configured Remote entities
        are never deleted. A selected voice-assistant target is also cleared.

        Returns the updated activity, or the fetched definition for a no-op.
        No power or activity commands are sent. Requests are not transactional:
        an API error propagates and earlier writes may already have succeeded.
        Retrying fetches current state and resumes any remaining cleanup.
        """
        if isinstance(entity_ids, str) or any(
            not isinstance(entity_id, str) or not entity_id for entity_id in entity_ids
        ):
            raise ValueError("entity_ids must be a list of non-empty entity ID strings")
        selected = set(entity_ids)
        activity = await self._api.get_activity(activity_id)
        if only_unused:
            selected -= self._extract_used_entity_ids(activity)
        if not selected:
            return activity

        options = deepcopy(activity.get("options", {}))
        changes: dict[str, Any] = {}
        included = options.get("included_entities", [])
        if any(entity["entity_id"] in selected for entity in included):
            # GET includes enriched descriptors; PATCH expects only ID strings.
            changes["entity_ids"] = [
                entity["entity_id"] for entity in included if entity["entity_id"] not in selected
            ]

        sequences = options.get("sequences", {})
        cleaned_sequences = {
            name: [
                step for step in steps if not self._targets_entity(step.get("command"), selected)
            ]
            for name, steps in sequences.items()
        }
        if cleaned_sequences != sequences:
            changes["sequences"] = cleaned_sequences

        mappings = options.get("button_mapping", [])
        cleaned_mappings = deepcopy(mappings)
        for mapping in cleaned_mappings:
            for press in ("short_press", "long_press"):
                if self._targets_entity(mapping.get(press), selected):
                    del mapping[press]

        page_updates: list[tuple[str, list[dict]]] = []
        for page in options.get("user_interface", {}).get("pages", []):
            items = page.get("items", [])
            cleaned_items = [item for item in items if not self._ui_item_targets(item, selected)]
            if cleaned_items != items:
                # Validate the complete plan before performing any writes.
                if not page.get("page_id"):
                    raise ValueError("Cannot update an activity UI page without a page_id")
                page_updates.append((page["page_id"], cleaned_items))

        slider = options.get("touch_slider", {})
        if self._targets_entity(slider.get("target"), selected):
            # Disabling clears the target in Core and avoids automatic fallback.
            changes["touch_slider"] = {"enabled": False}
        voice = options.get("voice_assistant", {})
        if self._targets_entity(voice.get("target"), selected):
            changes["voice_assistant"] = {}

        # Clear references while their entities are still included, then remove
        # membership and sequence references together in the final PATCH.
        if cleaned_mappings != mappings:
            await self._api.post_activity_buttons(activity_id, cleaned_mappings)
        for page_id, items in page_updates:
            await self._api.patch_activity_ui_page(activity_id, page_id, {"items": items})
        if changes:
            return await self._api.patch_activity(activity_id, {"options": changes})
        if cleaned_mappings != mappings or page_updates:
            return await self._api.get_activity(activity_id)
        return activity

    @staticmethod
    def _targets_entity(value: Any, entity_ids: set[str]) -> bool:
        """Match an entity reference, without inspecting arbitrary command params."""
        return isinstance(value, dict) and value.get("entity_id") in entity_ids

    @classmethod
    def _ui_item_targets(cls, item: dict, entity_ids: set[str]) -> bool:
        """Recognize entity-backed widgets and command buttons."""
        return bool(cls._ui_item_entity_ids(item) & entity_ids)

    @staticmethod
    def _ui_item_entity_ids(item: dict) -> set[str]:
        """Share widget reference rules between usage detection and removal."""
        used: set[str] = set()
        command = item.get("command")
        if isinstance(command, dict) and command.get("entity_id"):
            used.add(command["entity_id"])
        if item.get("media_player_id"):
            used.add(item["media_player_id"])
        for field, id_key in (
            ("sensor", "sensor_id"),
            ("select", "select_id"),
            ("number", "number_id"),
            ("slider", "entity_id"),
            ("webview", "webview_id"),
        ):
            widget = item.get(field)
            if isinstance(widget, dict) and widget.get(id_key):
                used.add(widget[id_key])
        return used

    async def find_orphaned_entities(
        self,
    ) -> list[dict[str, Any]]:
        """
        Find orphaned entities in activities on the Remote.

        Scans all activities and identifies entities that are marked as unavailable
        (available=false). These are typically entities that were deleted or renamed
        but still referenced in activity configurations.

        Authentication can be done via PIN (Basic Auth) or API key (Bearer token).
        One of `pin` or `api_key` must be provided. API key is preferred over PIN.

        :return: List of orphaned entity dictionaries (with entity_commands
        and simple_commands removed)
        :raises UnfurledError: If any request or activity processing fails.
            Failed scans never return empty or partial results.

        Example:
            orphaned = await find_orphaned_entities()

            for entity in orphaned:
                print(f"Orphaned entity: {entity['entity_id']} in activity {entity['activity_id']}")
        """

        orphaned_entities: list[dict[str, Any]] = []

        try:
            activities_list = await self._api.get_activities()

            # Step 2: Fetch full activity details and check for orphaned entities
            for activity_summary in activities_list:
                activity_id = activity_summary.get("entity_id")
                if not activity_id:
                    continue

                # Get full activity details
                activity = await self._api.get_activity(activity_id)

                # Get activity name - try summary first, then full activity
                activity_name = activity_summary.get("name") or activity.get("name", {})

                _LOG.debug(
                    "Processing activity %s, name: %s",
                    activity_id,
                    activity_name.get("en", "no name")
                    if isinstance(activity_name, dict)
                    else activity_name,
                )

                # Check included_entities for orphaned entities
                options = activity.get("options", {})
                included_entities = options.get("included_entities", [])

                for entity in included_entities:
                    # Check if entity is marked as unavailable
                    # Note: 'available' property only exists when it's False
                    if "available" in entity and entity["available"] is False:
                        # Create a copy of the entity dict without entity_commands
                        # and simple_commands
                        orphaned_entity = {
                            k: v
                            for k, v in entity.items()
                            if k not in ("entity_commands", "simple_commands")
                        }
                        # Add activity context for reference
                        orphaned_entity["activity_id"] = activity_id
                        orphaned_entity["activity_name"] = activity_name

                        orphaned_entities.append(orphaned_entity)
                        _LOG.debug(
                            "Found orphaned entity: %s in activity %s (%s)",
                            entity.get("entity_id"),
                            activity_name.get("en", activity_id)
                            if isinstance(activity_name, dict)
                            else activity_id,
                            activity_id,
                        )

            _LOG.info("Found %d orphaned entities", len(orphaned_entities))
            return orphaned_entities

        except Exception as err:  # pylint: disable=broad-except
            raise UnfurledError("Failed to scan activities for orphaned entities") from err

    def _extract_used_entity_ids(self, activity: dict[str, Any]) -> set[str]:
        """
        Extract all entity IDs that are actively used in an activity's sequences,
        button_mapping, user_interface, touch slider and voice-assistant targets.

        :param activity: Full activity dict from GET /api/activities/{id}
        :return: Set of entity_id strings that are referenced
        """
        used: set[str] = set()
        options = activity.get("options", {})

        # A configured target remains a reference even if its control is disabled.
        for field in ("touch_slider", "voice_assistant"):
            config = options.get(field) or {}
            target = config.get("target")
            if isinstance(target, dict) and target.get("entity_id"):
                used.add(target["entity_id"])

        # sequences: on/off lists of steps; each step may have command.entity_id
        sequences = options.get("sequences", {})
        for steps in sequences.values():
            if not isinstance(steps, list):
                continue
            for step in steps:
                cmd = step.get("command", {})
                if isinstance(cmd, dict) and cmd.get("entity_id"):
                    used.add(cmd["entity_id"])

        # button_mapping: list of {short_press, long_press} each with entity_id
        for mapping in options.get("button_mapping", []):
            for press_key in ("short_press", "long_press"):
                press = mapping.get(press_key, {})
                if isinstance(press, dict) and press.get("entity_id"):
                    used.add(press["entity_id"])

        # user_interface: pages → items; entity IDs hide in several fields
        ui = options.get("user_interface", {})
        for page in ui.get("pages", []):
            for item in page.get("items", []):
                used.update(self._ui_item_entity_ids(item))

        return used

    async def find_unused_activity_entities(
        self,
    ) -> list[dict[str, Any]]:
        """
        Find entities included in activities that are never actually used.

        An entity is "unused" when it appears in ``included_entities`` but has no
        reference in sequences, button_mapping, touch_slider, voice_assistant,
        or user_interface (commands, media player, sensor, select, number, slider
        and web view widgets).

        Authentication can be done via PIN (Basic Auth) or API key (Bearer token).
        One of ``pin`` or ``api_key`` must be provided.

        :param remote_url: The Remote's base URL (e.g., "http://192.168.1.100")
        :param pin: Remote's web-configurator PIN for Basic Auth
        :param api_key: Remote's API key for Bearer token authentication
        :return: List of dicts with activity context and the unused entity info
        :raises UnfurledError: If any request or activity processing fails.
            Failed scans never return empty or partial results.
        """
        unused: list[dict[str, Any]] = []

        try:
            activities_list = await self._api.get_activities()

            for activity_summary in activities_list:
                activity_id = activity_summary.get("entity_id")
                if not activity_id:
                    continue

                activity = await self._api.get_activity(activity_id)

                activity_name = activity_summary.get("name") or activity.get("name", {})
                options = activity.get("options", {})
                included_entities = options.get("included_entities", [])

                if not included_entities:
                    continue

                included_ids = {e["entity_id"] for e in included_entities if e.get("entity_id")}
                used_ids = self._extract_used_entity_ids(activity)
                truly_unused = included_ids - used_ids

                for entity in included_entities:
                    eid = entity.get("entity_id")
                    if eid in truly_unused:
                        record = {
                            k: v
                            for k, v in entity.items()
                            if k not in ("entity_commands", "simple_commands")
                        }
                        record["activity_id"] = activity_id
                        record["activity_name"] = activity_name
                        unused.append(record)
                        _LOG.debug("Unused entity %s in activity %s", eid, activity_id)

            _LOG.info("Found %d unused activity entities", len(unused))
            return unused

        except Exception as err:  # pylint: disable=broad-except
            raise UnfurledError("Failed to scan activities for unused entities") from err

    async def find_orphaned_ir_codesets(self) -> list[dict[str, Any]]:
        """Return custom IR codesets that are not assigned to an IR remote.

        Each result contains ``device_id`` and ``device_name``.  The helper
        follows all pages exposed by the Remote, so callers do not need to
        reproduce pagination or codeset-association rules.
        """
        try:
            associated_codeset_ids: set[str] = set()
            page = 1
            while True:
                remotes = await self._api.get_remotes(kind="IR", page=page)
                if not remotes:
                    break
                for remote in remotes:
                    remote_id = remote.get("entity_id")
                    if not remote_id:
                        continue
                    detail = await self._api.get_remote(remote_id)
                    codeset = detail.get("options", {}).get("ir", {}).get("codeset")
                    if isinstance(codeset, dict) and codeset.get("id"):
                        associated_codeset_ids.add(codeset["id"])
                if len(remotes) < 100:
                    break
                page += 1

            all_codesets: list[dict[str, Any]] = []
            page = 1
            while True:
                codesets = await self._api.get_ir_custom_codes(page=page)
                if not codesets:
                    break
                all_codesets.extend(codesets)
                if len(codesets) < 100:
                    break
                page += 1

            return [
                {
                    "device_id": codeset["device_id"],
                    "device_name": codeset.get("device", codeset["device_id"]),
                }
                for codeset in all_codesets
                if codeset.get("device_id") not in associated_codeset_ids
                and codeset.get("device_id")
            ]
        except Exception as err:  # pylint: disable=broad-except
            _LOG.error("Unexpected error while scanning for orphaned IR codesets: %s", err)
            return []
