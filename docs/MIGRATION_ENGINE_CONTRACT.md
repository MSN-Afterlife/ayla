# Migration Engine Contract

This is the Ayla-side contract for Minecraft migration UX.

## Auth

All requests use `Authorization: Bearer <MIGRATION_ENGINE_TOKEN>`.
Responses are JSON. Tokens must never appear in response bodies or logs.

## Enums

Presence: `ONLINE`, `OFFLINE_CONFIRMED`, `UNKNOWN`.
`UNKNOWN` is never safe for mutation.

Lock state: `ABSENT`, `UNLOCKED`, `LOCKING`, `MIGRATING`, `ROLLING_BACK`,
`RECOVERY_REQUIRED`, `CRITICAL_FAILURE`, `UNKNOWN`.
`LOCKING`, `MIGRATING`, `ROLLING_BACK`, `RECOVERY_REQUIRED`,
`CRITICAL_FAILURE`, and `UNKNOWN` block mutation.

Migration state: `INSPECTED`, `PLANNED`, `EXECUTING`, `EXECUTED`,
`ROLLING_BACK`, `ROLLED_BACK`, `FAILED`, `RECOVERY_REQUIRED`, `UNKNOWN`.

Error codes: `conflict`, `player_online`, `presence_unknown`, `lock_active`,
`not_found`, `already_executed`, `rollback_unavailable`, `auth_failed`,
`unavailable`, `invalid_response`.

## Endpoints

`POST /api/v1/migrations/inspect`

Request accepts one identity selector:

```json
{"discord_user_id":"123456789"}
```

or:

```json
{"canonical_uuid":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
```

or:

```json
{"platform":"java","external_id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
```

Response:

```json
{
  "identity": {
    "discord_user_id": "123456789",
    "canonical_uuid": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
    "canonical_name": "Player",
    "java": {"platform":"java","external_id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","username":"JavaName"},
    "bedrock": {"platform":"bedrock","external_id":"2533274791234567","username":"BedrockName"},
    "source_uuids": ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"]
  },
  "presence": "OFFLINE_CONFIRMED",
  "lock_state": "ABSENT",
  "detected_data": ["playerdata", "advancements"],
  "warnings": [],
  "blockers": []
}
```

`POST /api/v1/migrations/plan`

Request is the same selector as inspect plus optional `reason`.

Response:

```json
{
  "migration_id": "mig_123",
  "source_identities": ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
  "canonical_target": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
  "operations": [{"dataset":"playerdata","action":"rewrite_uuid","count":1,"detail":"copy source to canonical"}],
  "affected_files": ["world/playerdata/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.dat"],
  "affected_datasets": ["playerdata"],
  "conflicts": [],
  "warnings": [],
  "blockers": [],
  "rollback_available": true,
  "presence": "OFFLINE_CONFIRMED",
  "lock_state": "ABSENT"
}
```

`GET /api/v1/migrations/{migration_id}`

Response:

```json
{
  "migration_id": "mig_123",
  "state": "PLANNED",
  "created_at": "2026-09-06T12:00:00Z",
  "updated_at": "2026-09-06T12:00:30Z",
  "presence": "OFFLINE_CONFIRMED",
  "lock_state": "ABSENT",
  "verification_state": "ready",
  "rollback_available": true,
  "warnings": [],
  "blockers": []
}
```

`POST /api/v1/migrations/{migration_id}/execute`

Headers include `Idempotency-Key`. Request:

```json
{"operator_id":"123456789"}
```

Response:

```json
{
  "migration_id": "mig_123",
  "result": "executed",
  "migrated_datasets": ["playerdata"],
  "verifications": ["canonical_exists"],
  "snapshot_ref": "snap_456",
  "rollback_available": true,
  "presence": "OFFLINE_CONFIRMED",
  "lock_state": "UNLOCKED"
}
```

`POST /api/v1/migrations/{migration_id}/rollback`

Headers include `Idempotency-Key`. Request:

```json
{"operator_id":"123456789"}
```

Response:

```json
{
  "migration_id": "mig_123",
  "result": "rolled_back",
  "restored_data": ["playerdata"],
  "verifications": ["source_restored"],
  "presence": "OFFLINE_CONFIRMED",
  "lock_state": "UNLOCKED"
}
```

## Invariants

`execute` and `rollback` must reject `ONLINE`, `UNKNOWN`, active lock states,
blockers, stale plans, missing auth, and invalid idempotency.

Same `Idempotency-Key` plus same payload returns the same result. Same key plus
different payload returns a conflict.

The plan defines exact source identities, target identity, affected rows, and
affected files. The engine must not mutate identities outside that set.
