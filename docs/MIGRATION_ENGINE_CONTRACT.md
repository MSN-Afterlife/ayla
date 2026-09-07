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

`POST /api/v1/migrations/preview`

Used by Discord UX to inspect and compare data profiles (playerdata, advancements,
stats) between sources (e.g. Java and Bedrock) before choosing a source-of-truth
or dataset policy. This endpoint is strictly read-only, idempotent, and never
creates canonical identities, mappings, operations, or migration locks.

Request:

```json
{
  "sources": [
    {"platform": "java", "external_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "username": "PlayerJava"},
    {"platform": "bedrock", "external_id": "2533274791234567", "username": "PlayerBedrock"}
  ],
  "target": {
    "discord_user_id": "123456789"
  }
}
```

Response:

```json
{
  "sources": [
    {
      "platform": "java",
      "external_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
      "username": "PlayerJava",
      "playerdata": {
        "available": true,
        "inventory": {
          "occupied_slots": 24,
          "total_items": 312,
          "items": [
            {"id": "minecraft:diamond", "count": 13, "enchantments": []},
            {"id": "minecraft:diamond_sword", "count": 1, "enchantments": ["sharpness:5"]}
          ]
        },
        "ender_chest": {
          "occupied_slots": 5,
          "total_items": 45,
          "items": [
            {"id": "minecraft:netherite_ingot", "count": 2, "enchantments": []}
          ]
        },
        "equipment": {
          "mainhand": {"id": "minecraft:diamond_sword", "count": 1, "enchantments": ["sharpness:5"]},
          "offhand": {"id": "minecraft:shield", "count": 1, "enchantments": []},
          "armor": {
            "head": {"id": "minecraft:diamond_helmet", "count": 1, "enchantments": []},
            "chest": {"id": "minecraft:diamond_chestplate", "count": 1, "enchantments": []},
            "legs": {"id": "minecraft:diamond_leggings", "count": 1, "enchantments": []},
            "feet": {"id": "minecraft:diamond_boots", "count": 1, "enchantments": []}
          }
        },
        "xp_level": 30,
        "xp_total": 1395,
        "xp_progress": 0.5,
        "health": 20.0,
        "food_level": 20,
        "dimension": "minecraft:overworld",
        "position": [100.5, 64.0, -200.0]
      },
      "advancements": {
        "available": true,
        "total_completed": 45,
        "highlights": ["minecraft:story/mine_diamond"]
      },
      "stats": {
        "available": true,
        "play_time_seconds": 18450,
        "deaths": 2,
        "mob_kills": 120,
        "player_kills": 0,
        "blocks_mined": 4500,
        "distance_walked": 12000
      }
    }
  ],
  "warnings": []
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

`GET /api/v1/migrations`

Used by Discord UX for autocomplete and safe identity-based resolution. The
canonical key remains `migration_id`; this endpoint only returns references.

Query parameters:

- `query`: free text for operator autocomplete.
- `discord_user_id`, `canonical_uuid`, or `platform` + `external_id`: identity selector.
- `state`: optional migration state filter.
- `rollback_available`: optional `true`/`false`.
- `limit`: maximum references, capped by the server.

Response:

```json
{
  "migrations": [
    {
      "migration_id": "mig_123",
      "state": "EXECUTED",
      "canonical_uuid": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
      "player_name": "Player",
      "discord_user_id": "123456789",
      "created_at": "2026-09-06T12:00:00Z",
      "updated_at": "2026-09-06T12:03:00Z",
      "rollback_available": true
    }
  ]
}
```

For identity-based status, return migrations ordered newest/relevant first. For
rollback by identity, the Discord client will only auto-select when exactly one
reference matches `state=EXECUTED` and `rollback_available=true`; multiple
matches are treated as ambiguous and require explicit operator selection.

`GET /api/v1/minecraft/players`

Used by Discord UX to search players by nick/name with approximate matching.
Staff must not need VPS access, UUID lookup, or manual file inspection.

Query parameters:

- `query`: partial or approximate nick typed by staff.
- `limit`: maximum references, capped by the server.

The server should search known canonical names, Java usernames, Bedrock
gamertags, and any safe indexed aliases available to the migration stack. It
must return likely matches ordered by confidence/relevance. The Discord client
only auto-selects when exactly one player is returned; multiple matches are
treated as ambiguous.

Response:

```json
{
  "players": [
    {
      "canonical_uuid": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
      "player_name": "Player",
      "discord_user_id": "123456789",
      "java_external_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
      "bedrock_external_id": "2533274791234567",
      "confidence": 0.94
    }
  ]
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
