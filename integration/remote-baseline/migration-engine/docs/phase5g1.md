# Phase 5G.2 — offline adapters

The existing engine now uses `lib/phase5g1.py` for offline pilot preflight.
All adapter writes are restricted to `/opt/minecraft/migration-engine/sandbox`.
The source is exclusively `555dd93f-a696-3e49-978b-398ad2208571`; the target is
`70f73129-6bc7-47af-9248-d0f2ec3a891d`. No other identity is a source.

Validated migrations: Vanilla root NBT UUID and Bukkit last-known name;
MVI name files, player metadata and name index; AuraSkills UUID; EliteMobs
PlayerData UUID/name; HuskHomes UUID/name and all foreign references; Waypoints
player key and ownership, settings, visit metadata, compass and sharing keys;
ImageFrame JSON map creator authority.
Source files are preserved for Vanilla/MVI/AuraSkills. SQLite identities move.
All non-identity columns are verified against an independent pre-apply state.

Quests has exactly empty active quests/stages and zero points. UltimateTeams
has no teams. SimplePets has four empty base64-encoded JSON arrays. LuckPerms
has a default primary-group user cache row and zero assigned nodes. These are
NO_DATA for this source, and the original storage remains untouched. SimpleLogin
remains TRANSITION_PRESERVE. MarriageMaster remains NO_DATA.

ImageFrame was resolved with a read-only supplement from the active plugin
metadata. The supplement contains config, player preference JSON, global
metadata and every `data/<index>/data.json`; it excludes image PNG/cache/upload
payloads. The adapter changes only functional authority: `creator` and explicit
`hasAccess` entries. Player JSON is preference-only and remains preserved.

Reproduce the sandbox validations (writes only in engine/audit):

```sh
cd /opt/minecraft/migration-engine
TMPDIR=/opt/minecraft/migration-engine/sandbox python3 tools/phase5g1/validate.py
```

The existing run's manifest now points to the restored clean Phase 5G.2 sandbox
for `pilot-preflight`. Its approval nonce remains in run state. `pilot-apply`
requires explicit write authorization and a valid run-bound nonce, rejects
structural/runtime blockers and invalid fingerprints/versions, and dispatches to the prepared staging/publication implementation only after
all runtime gates pass. It has not been executed in this phase.
The current structural preflight is `READY_FOR_CONTROLLED_PILOT_PENDING_MAINTENANCE`.

Version validation covers captured configs, exact SQLite schemas and the runtime
binary baseline at `/opt/minecraft/migration-engine/baselines/mounk-pilot-versions.json`.
The baseline records Paper plus every relevant plugin jar with filename, size,
version metadata and SHA-256. Preflight verifies those hashes read-only against
the active server root and binds the baseline SHA-256 into the approval nonce.

The LuckPerms helper compiles against API 5.5 and Bukkit API 1.21.8 (Java 21).
It is not installed or tested inside Paper. Its internal migration service copies
persistent API Node objects, preserves source, refuses target collisions and
has logical snapshots/restore. Tests cover the actual zero-node source fixture;
nonempty migration/restore is implemented but not integration-certified.
An isolated compatible standalone artifact was not obtained; no standalone
runtime test is claimed. SQL is used exclusively for read-only inspection.

## Prepared dispatcher follow-up

`lib/pilot.py` now implements the dispatch and atomic publication path. The CLI
is wired to it after write flag, nonce, structural blockers and fingerprints.
**It has not been executed**.
It requires an expiring maintenance record bound to run/server/source/target,
no running JVM, all plugin/Paper JAR hashes, an exclusive publication lock,
successful complete snapshots, a fresh all-adapter plan without bypass, and
per-file/global verification. A consumed approval record prevents replay.
The publication primitive was tested only against copied snapshot directories.
It restores changed files if publication or verification fails.

The maintenance/JVM gate never stops Paper; it only refuses execution when the
required condition is not met.
