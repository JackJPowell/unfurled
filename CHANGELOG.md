# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- Added `EntityPowerState`, `CoreAPI.put_entity_state`, and `Activity.set_state`
  for correcting an activity's runtime `ON` or `OFF` state without running an
  activity sequence.
- Added `remote.system.flags.entity_state_update_available`, which reports
  support for runtime entity-state correction on API version 2.10.1 or newer.

## [0.5.0]

### Added

- Added `RemoteKind` (`IR`, `BT`, and `EXTERNAL`) for filtering remote searches.
- Added `q` search support to `CoreAPI.get_remotes`.
- Added `CoreAPI.get_ir_remote(entity_id)` for retrieving the IR codeset assigned to
  one remote entity.
- Added remote-name command resolution to `IR.send`, including clear errors when a
  remote search has zero or multiple matches.

### Changed

- **Breaking:** Replaced the ambiguous `IR.send(device=...)` argument with explicit
  `remote_name=` and `manufacturer=` arguments.
- `IR.send(remote_name=...)` now resolves commands through the configured remote's
  IR detail and does not require a codeset; an optional `codeset=` verifies the
  returned codeset name or ID.
- `IR.get_remote_codeset` now resolves custom IR codes by codeset name.
- Custom IR codeset caching now uses `GET /ir/codes/custom`; the per-remote IR
  endpoint returns a single codeset object and is no longer treated as a collection.
- Updated IR usage examples and endpoint coverage.
