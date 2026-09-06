from __future__ import annotations

import argparse
import contextlib
import copy
import datetime
import gzip
import hashlib
import io
import json
import os
import shutil
import sqlite3
import struct
import time
from pathlib import Path

import yaml

from .adapters import AdapterError
from .ownership_policy import CHECK_NAME, OwnershipPolicy
from . import phase5g1 as p5
from .phase5h import SERVER_ROOT, completeness_check, required_registry_files

SOURCE = "555dd93f-a696-3e49-978b-398ad2208571"
SOURCE_NAME = "Mounkass"
TARGET = "2eab1746-315b-3e81-b640-7f8241d3f2cd"
TARGET_NAME = "Mounk"
WRONG_TARGET = "70f73129-6bc7-47af-9248-d0f2ec3a891d"
ENGINE = p5.ENGINE
AUDIT = Path("/opt/minecraft/migration-audit")
EXPECTED_UID = 1000
EXPECTED_GID = 0
SAFE_EMPTY_CHECK = "SAFE_EMPTY_RUNTIME_TARGET_CHECK"
WRONG_TARGET_POLICY = "PRESERVE_UNTIL_FINAL_HUMAN_VERIFY"


@contextlib.contextmanager
def final_identity():
    old = p5.identity_state()
    p5.configure_identity(SOURCE, TARGET, SOURCE_NAME, TARGET_NAME)
    try:
        yield
    finally:
        p5.configure_identity(old["source_uuid"], old["target_uuid"], old["source_name"], old["target_name"])


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rels_for_final_snapshot(root: Path) -> list[str]:
    root = Path(root)
    rels = set(required_registry_files())
    rels.update(
        {
            "plugins/LuckPerms/luckperms-h2-v2.mv.db",
            "plugins/LuckPerms/contexts.json",
            "plugins/UltimateTeams/UltimateTeamsData.db",
            "plugins/SimplePets/storage.db",
            "plugins/MarriageMaster/database.db",
            "plugins/SimpleLogin/passwords.db",
            "plugins/Multiverse-Inventories/groups.yml",
            "plugins/Multiverse-Inventories/playernames.json",
            "plugins/ImageFrame/config.yml",
            "plugins/ImageFrame/data/data.json",
            "plugins/ImageFrame/data/deletedMaps.bin",
        }
    )
    for folder, suffix in [("data", ".dat"), ("stats", ".json"), ("advancements", ".json")]:
        for u in [SOURCE, TARGET, WRONG_TARGET]:
            rels.add(f"world/players/{folder}/{u}{suffix}")
    for glob in [
        "plugins/AuraSkills/userdata/*.yml",
        "plugins/Quests/data/*.yml",
        "plugins/Multiverse-Inventories/**/*.json",
        "plugins/ImageFrame/data/**",
        "plugins/ImageFrame/players/*.json",
    ]:
        for path in root.glob(glob):
            if path.is_file():
                rels.add(str(path.relative_to(root)))
    return sorted(rels)

def _engine_safe(dst: Path) -> Path:
    """Accept any path strictly inside ENGINE (sandbox/ or snapshots/ etc.), no symlinks."""
    dst = Path(dst)
    engine = Path(ENGINE)
    if not dst.resolve().is_relative_to(engine) or any(
        x.is_symlink() for x in [dst, *dst.parents]
    ):
        raise AdapterError("destination must be inside migration engine directory")
    return dst


def copy_final_snapshot(root: Path, dst: Path) -> dict:
    root = Path(root)
    dst = _engine_safe(dst)
    if dst.exists():
        raise AdapterError("final sandbox destination already exists")
    policy = OwnershipPolicy.detect(ENGINE)
    copied, absent = [], []
    for rel in rels_for_final_snapshot(root):
        src = root / rel
        if not src.exists():
            absent.append(rel)
            continue
        if src.is_symlink():
            raise AdapterError("SNAPSHOT_REFUSED_SYMLINK: " + rel)
        policy.copy2(src, dst / rel)
        copied.append(rel)
    check = completeness_check(dst)
    if check["status"] != "PASS":
        raise AdapterError("SNAPSHOT_COMPLETENESS_CHECK failed: " + json.dumps(check["missing_files"]))
    return {"root": str(dst), "copied": len(copied), "absent_optional_files": absent, "completeness_check": check}



def _read_nbt_summary(data: bytes) -> dict:
    raw = gzip.decompress(data)
    f = io.BytesIO(raw)
    out = {"Inventory": 0, "EnderItems": 0, "XpLevel": 0, "XpTotal": 0, "XpP": 0.0}

    def read(n):
        b = f.read(n)
        if len(b) != n:
            raise AdapterError("truncated NBT")
        return b

    def number(fmt):
        return struct.unpack(fmt, read(struct.calcsize(fmt)))[0]

    def string():
        return read(number(">H")).decode("utf-8")

    def payload(tag, path):
        if tag == 1:
            read(1)
        elif tag == 2:
            read(2)
        elif tag == 3:
            value = number(">i")
            if path and path[-1] in ("XpLevel", "XpTotal"):
                out[path[-1]] = value
        elif tag == 4:
            read(8)
        elif tag == 5:
            value = number(">f")
            if path and path[-1] == "XpP":
                out["XpP"] = value
        elif tag == 6:
            read(8)
        elif tag == 7:
            read(number(">i"))
        elif tag == 8:
            string()
        elif tag == 9:
            typ = number(">B")
            n = number(">i")
            if path and path[-1] in ("Inventory", "EnderItems"):
                out[path[-1]] = n
            for i in range(n):
                payload(typ, path + (str(i),))
        elif tag == 10:
            while True:
                typ = number(">B")
                if typ == 0:
                    break
                name = string()
                payload(typ, path + (name,))
        elif tag == 11:
            read(number(">i") * 4)
        elif tag == 12:
            read(number(">i") * 8)
        else:
            raise AdapterError("invalid NBT tag")

    if number(">B") != 10:
        raise AdapterError("NBT root must be compound")
    string()
    payload(10, ())
    return out


def _json_nonempty_stats(path: Path) -> int:
    data = json.loads(path.read_text())
    total = 0
    for section in data.get("stats", {}).values():
        if isinstance(section, dict):
            total += sum(int(v) for v in section.values() if isinstance(v, int))
    return total


def _json_unexpected_stats(path: Path) -> list[str]:
    allowed_custom = {
        "minecraft:play_time",
        "minecraft:total_world_time",
        "minecraft:time_since_death",
        "minecraft:time_since_rest",
        "minecraft:leave_game",
    }
    data = json.loads(path.read_text())
    unexpected = []
    for section, values in data.get("stats", {}).items():
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            if not value:
                continue
            if section == "minecraft:custom" and key in allowed_custom:
                continue
            unexpected.append(f"{section}:{key}={value}")
    return unexpected


def _json_done_advancements(path: Path) -> int:
    data = json.loads(path.read_text())
    return sum(1 for v in data.values() if isinstance(v, dict) and v.get("done") is True)


def _db_rows(path: Path) -> dict[str, list[dict]]:
    if Path(str(path) + "-wal").exists() and Path(str(path) + "-wal").stat().st_size:
        raise AdapterError("offline SQLite copy contains uncheckpointed WAL: " + str(path))
    with sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True) as c:
        c.row_factory = sqlite3.Row
        return {
            t: [dict(r) for r in c.execute(f'SELECT * FROM "{t}"')]
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        }


def _row_mentions(row: dict, token: str) -> bool:
    values = []
    for value in row.values():
        if isinstance(value, bytes):
            values.append(value.hex())
            try:
                values.append(value.decode("utf-8"))
            except UnicodeDecodeError:
                pass
        else:
            values.append(str(value))
    return any(token in value for value in values)


def safe_empty_runtime_target_check(root: Path) -> dict:
    root = Path(root)
    evidence, blockers = [], []

    data = root / "world/players/data" / f"{TARGET}.dat"
    if data.exists():
        nbt = _read_nbt_summary(data.read_bytes())
        evidence.append({"adapter": "Vanilla", "path": str(data.relative_to(root)), "summary": nbt})
        if nbt["Inventory"] or nbt["EnderItems"] or nbt["XpLevel"] or nbt["XpTotal"] or nbt["XpP"] > 0:
            blockers.append("Vanilla target has inventory/ender/xp state")
    stats = root / "world/players/stats" / f"{TARGET}.json"
    if stats.exists():
        total = _json_nonempty_stats(stats)
        unexpected = _json_unexpected_stats(stats)
        evidence.append({"adapter": "VanillaStats", "path": str(stats.relative_to(root)), "stat_total": total, "unexpected": unexpected})
        if unexpected:
            blockers.append("Vanilla target has nontrivial stats")
    adv = root / "world/players/advancements" / f"{TARGET}.json"
    if adv.exists():
        done = _json_done_advancements(adv)
        evidence.append({"adapter": "VanillaAdvancements", "path": str(adv.relative_to(root)), "done_count": done})
        if done > 3:
            blockers.append("Vanilla target has nontrivial advancements")

    aura = root / "plugins/AuraSkills/userdata" / f"{TARGET}.yml"
    if aura.exists():
        d = yaml.safe_load(aura.read_text()) or {}
        progressed = []
        for skill, value in (d.get("skills") or {}).items():
            if (value or {}).get("level", 0) or float((value or {}).get("xp", 0) or 0) > 0:
                progressed.append(skill)
        evidence.append({"adapter": "AuraSkills", "path": str(aura.relative_to(root)), "progressed_skills": progressed})
        if progressed or float(d.get("mana", 20) or 20) > 20:
            blockers.append("AuraSkills target has skill progression")

    quests = root / "plugins/Quests/data" / f"{TARGET}.yml"
    if quests.exists():
        d = yaml.safe_load(quests.read_text()) or {}
        allowed = {"currentQuests": [], "currentStages": [], "quest-points": 0, "lastKnownName": TARGET_NAME}
        evidence.append({"adapter": "Quests", "path": str(quests.relative_to(root)), "keys": sorted(d)})
        if d != allowed:
            blockers.append("Quests target has non-default state")

    image_player = root / "plugins/ImageFrame/players" / f"{TARGET}.json"
    if image_player.exists():
        d = json.loads(image_player.read_text())
        evidence.append({"adapter": "ImageFrame", "path": str(image_player.relative_to(root)), "keys": sorted(d)})
        if set(d) != {"uuid", "preferences"}:
            blockers.append("ImageFrame target player file has unexpected state")

    db_checks = [
        ("EliteMobs", "plugins/EliteMobs/data/player_data.db", {"PlayerData": ["PlayerUUID"]}),
        ("HuskHomes", "plugins/HuskHomes/HuskHomesData.db", {"huskhomes_users": ["uuid"], "huskhomes_homes": ["owner_uuid"], "huskhomes_teleports": ["player_uuid"], "huskhomes_user_cooldowns": ["player_uuid"]}),
        ("Waypoints", "plugins/Waypoints/waypoints.db", {"player_data": ["id"], "player_data_typed": ["playerId"], "folders": ["owner"], "waypoints": ["owner"], "waypoint_meta": ["playerId"], "selected_waypoints": ["playerId"], "compass_storage": ["playerId"], "waypoint_shares": ["owner", "sharedWith"]}),
        ("UltimateTeams", "plugins/UltimateTeams/UltimateTeamsData.db", {"ultimateteams_users": ["uuid"], "ultimateteams_teams": ["data"]}),
        ("SimplePets", "plugins/SimplePets/storage.db", {"simplepets_players": ["uuid"]}),
        ("MarriageMaster", "plugins/MarriageMaster/database.db", {"marry_players": ["uuid"]}),
        ("SimpleLogin", "plugins/SimpleLogin/passwords.db", {}),
    ]
    for name, rel, keys in db_checks:
        path = root / rel
        if not path.exists():
            continue
        rows = _db_rows(path)
        target_rows = []
        tokens = [TARGET, TARGET.replace("-", "")]
        if name == "SimpleLogin":
            tokens.append(TARGET_NAME)
        for table, table_rows in rows.items():
            for row in table_rows:
                if any(_row_mentions(row, token) for token in tokens):
                    target_rows.append({"table": table, "row": row})
        evidence.append({"adapter": name, "path": rel, "target_rows": len(target_rows)})
        if name == "HuskHomes":
            bad = [r for r in target_rows if r["table"] != "huskhomes_users"]
        elif name == "Waypoints":
            bad = [r for r in target_rows if r["table"] != "player_data"]
        elif name == "UltimateTeams":
            bad = [r for r in target_rows if r["table"] != "ultimateteams_users"]
        elif name == "SimplePets":
            bad = []
            for entry in target_rows:
                row = entry["row"]
                for key in ["UnlockedPets", "PetName", "NeedsRespawn", "SavedPets"]:
                    if key in row:
                        try:
                            import base64

                            if json.loads(base64.b64decode(row[key], validate=True)) != []:
                                bad.append(entry)
                        except Exception:
                            bad.append(entry)
        elif name == "MarriageMaster":
            bad = [
                r for r in target_rows
                if r["table"] != "marry_players"
                or set(r["row"]) != {"player_id", "name", "uuid", "sharebackpack"}
                or r["row"].get("name") != TARGET_NAME
                or r["row"].get("uuid") != TARGET.replace("-", "")
                or r["row"].get("sharebackpack") not in (0, False)
            ]
        elif name == "SimpleLogin":
            bad = target_rows
        else:
            bad = []
        if bad:
            blockers.append(f"{name} target has nontrivial rows")

    status = "PASS" if not blockers else "FAIL"
    return {
        "name": SAFE_EMPTY_CHECK,
        "status": status,
        "classification": "SAFE_EMPTY_RUNTIME_TARGET" if status == "PASS" else "TARGET_HAS_RUNTIME_STATE",
        "target": TARGET,
        "evidence": evidence,
        "blockers": blockers,
    }


def scrub_safe_empty_target(root: Path) -> dict:
    root = p5.safe(root)
    check = safe_empty_runtime_target_check(root)
    if check["status"] != "PASS":
        raise AdapterError(SAFE_EMPTY_CHECK + " failed: " + json.dumps(check["blockers"]))
    policy = OwnershipPolicy.detect(root)
    removed = []
    for rel in [
        f"world/players/data/{TARGET}.dat",
        f"world/players/data/{TARGET}.dat_old",
        f"world/players/stats/{TARGET}.json",
        f"world/players/advancements/{TARGET}.json",
        f"plugins/AuraSkills/userdata/{TARGET}.yml",
        f"plugins/Quests/data/{TARGET}.yml",
        f"plugins/Multiverse-Inventories/players/{TARGET}.json",
        f"plugins/ImageFrame/players/{TARGET}.json",
    ]:
        path = root / rel
        if path.exists():
            policy.remove_file(path)
            removed.append(rel)
    for path in (root / "plugins/Multiverse-Inventories").rglob(f"{TARGET_NAME}.json"):
        if path.is_file():
            policy.remove_file(path)
            removed.append(str(path.relative_to(root)))
    playernames = root / "plugins/Multiverse-Inventories/playernames.json"
    if playernames.exists():
        d = json.loads(playernames.read_text())
        changed = False
        for uuid_key, name in list(d.items()):
            if uuid_key == TARGET or name == TARGET_NAME:
                del d[uuid_key]
                changed = True
        if changed:
            p5.atomic(playernames, p5.js(d))
            removed.append(str(playernames.relative_to(root)) + " entries:target-name-namespace")

    db_deletes = [
        ("plugins/EliteMobs/data/player_data.db", {"PlayerData": ["PlayerUUID"]}),
        ("plugins/HuskHomes/HuskHomesData.db", {"huskhomes_users": ["uuid"]}),
        ("plugins/Waypoints/waypoints.db", {"player_data": ["id"]}),
        ("plugins/UltimateTeams/UltimateTeamsData.db", {"ultimateteams_users": ["uuid"]}),
        ("plugins/SimplePets/storage.db", {"simplepets_players": ["uuid"]}),
    ]
    for rel, tables in db_deletes:
        path = root / rel
        if not path.exists():
            continue
        with sqlite3.connect(path) as c:
            c.execute("BEGIN IMMEDIATE")
            for table, keys in tables.items():
                for key in keys:
                    c.execute(f'DELETE FROM "{table}" WHERE "{key}"=?', (TARGET,))
            c.commit()
        policy.normalize_path(path)
        removed.append(rel + " safe-empty-target rows")
    return {"safe_empty_check": check, "removed_from_staging": sorted(set(removed))}


def final_offline_plan(root: Path) -> dict:
    with final_identity():
        return p5.OfflineRun(root).plan()


def run_final_sandbox(source_root: Path, workdir: Path) -> dict:
    workdir = p5.safe(workdir)
    if workdir.exists():
        raise AdapterError("sandbox workdir already exists")
    policy = OwnershipPolicy.detect(ENGINE)
    policy.ensure_dir(workdir)
    input_root = workdir / "input-root"
    apply_root = workdir / "apply-root"
    rollback_root = workdir / "rollback-root"
    idempotency_root = workdir / "idempotency-root"
    collision_root = workdir / "collision-root"
    copy_final_snapshot(source_root, input_root)
    for dst in [apply_root, rollback_root, idempotency_root, collision_root]:
        policy.copytree(input_root, dst)

    started = time.perf_counter()
    with final_identity():
        safe_check = scrub_safe_empty_target(apply_root)
        run = p5.OfflineRun(apply_root)
        plan = run.plan()
        verify = run.apply(workdir / "apply-snapshots")
        apply_elapsed = time.perf_counter() - started

        scrub_safe_empty_target(rollback_root)
        rb = p5.OfflineRun(rollback_root)
        before = p5.hashes(rollback_root)
        rb.apply(workdir / "rollback-snapshots")
        rollback_result = rb.rollback()
        rollback_ok = p5.hashes(rollback_root) == before

        scrub_safe_empty_target(idempotency_root)
        idem = p5.OfflineRun(idempotency_root)
        idem.apply(workdir / "idempotency-snapshots")
        idempotency = p5.OfflineRun(idempotency_root).plan()

        # Collision protection: a copied source playerdata at target UUID is real state.
        target_data = collision_root / "world/players/data" / f"{TARGET}.dat"
        policy.copy2(collision_root / "world/players/data" / f"{SOURCE}.dat", target_data)
        collision = safe_empty_runtime_target_check(collision_root)

    wrong_before = {str(p.relative_to(input_root)): sha256(p) for p in input_root.rglob(f"*{WRONG_TARGET}*") if p.is_file()}
    wrong_after = {str(p.relative_to(apply_root)): sha256(p) for p in apply_root.rglob(f"*{WRONG_TARGET}*") if p.is_file()}
    ownership = OwnershipPolicy.detect(apply_root).check_tree(apply_root)
    return {
        "source_root": str(source_root),
        "workdir": str(workdir),
        "safe_empty_acceptance": safe_check,
        "clean_plan": plan,
        "apply": "PASS" if all(verify.values()) else "FAIL",
        "verify": verify,
        "rollback": "PASS" if rollback_result and rollback_ok else "FAIL",
        "idempotency": idempotency["status"],
        "collision_protection": "PASS" if collision["status"] == "FAIL" else "FAIL",
        "ownership": "PASS" if ownership else "FAIL",
        "failure_injection": "covered_by_unit_tests: OfflineRun injected failure rollback",
        "wrong_target_70f_preservation": "PASS" if wrong_before == wrong_after else "REVIEW_SHARED_NAMESPACE_CHANGES",
        "wrong_target_policy": WRONG_TARGET_POLICY,
        "apply_elapsed_seconds": round(apply_elapsed, 3),
    }


def build_plan_artifact(sandbox: dict | None = None) -> dict:
    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": SOURCE,
        "source_name": SOURCE_NAME,
        "target": TARGET,
        "target_name": TARGET_NAME,
        "wrong_pilot_target": WRONG_TARGET,
        "architecture": "CANONICAL_OFFLINE_UUID",
        "gates": [
            "PREFLIGHT_ONLINE_READONLY",
            "SOURCE_TARGET_OFFLINE_CONFIRMATION",
            "MAINTENANCE_GATE",
            "SHUTDOWN_GATE",
            "FINAL_SNAPSHOT",
            "SNAPSHOT_COMPLETENESS_CHECK",
            CHECK_NAME,
            SAFE_EMPTY_CHECK,
            "WRONG_TARGET_70F_PRESERVATION",
            "APPLY",
            "VERIFY",
            "STARTUP",
            "FINAL_HUMAN_VERIFY",
            "COMMIT_FINALIZATION",
        ],
        "safe_empty_gate": {
            "target": TARGET,
            "allow": "fresh/default runtime only",
            "block_on": [
                "inventory",
                "xp",
                "EnderItems",
                "homes",
                "waypoints",
                "skills",
                "plugin progression",
                "unexpected credential",
                "nontrivial stats/advancements",
            ],
        },
        "ownership_gate": {"expected_uid": EXPECTED_UID, "expected_gid": EXPECTED_GID, "critical": True},
        "wrong_target_policy": WRONG_TARGET_POLICY,
        "simplelogin_strategy": "TRANSITION_PRESERVE; do not copy hash/secret during data migration",
        "sandbox": sandbox,
        "downtime_estimate": {
            "expected": {"shutdown": "30-60s", "snapshot": "5-15s", "apply": "2-8s", "verify": "5-15s", "startup": "45-90s", "total": "90-190s"},
            "safe_window": "10 minutes",
        },
        "production_changed": "NO",
        "restart_performed": "NO",
        "final_status": "READY_FOR_FINAL_MAINTENANCE" if sandbox and sandbox.get("apply") == "PASS" else "BLOCKED",
    }


def write_artifacts(plan: dict) -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    (AUDIT / "final-cutover-prep.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    lines = [
        "# Final Cutover Runbook",
        "",
        f"Source: `{SOURCE}` / `{SOURCE_NAME}`",
        f"Target: `{TARGET}` / `{TARGET_NAME}`",
        f"Wrong pilot target: `{WRONG_TARGET}`",
        "",
        "Do not run until the maintenance window. The command must be executed only after humans confirm no players are online and Paper has been stopped by the approved Crafty procedure.",
        "",
        "## Preflight Online",
        "",
        "1. Confirm production health and record online player list. This is read-only.",
        "2. Confirm source `Mounkass`/`555dd93f-a696-3e49-978b-398ad2208571` and target `Mounk`/`2eab1746-315b-3e81-b640-7f8241d3f2cd`.",
        "3. Confirm `70f73129-6bc7-47af-9248-d0f2ec3a891d` is classified as `WRONG_PILOT_TARGET` and policy is preserve.",
        "4. Confirm `2eab` still passes `SAFE_EMPTY_RUNTIME_TARGET_CHECK`; block if inventory, XP, EnderItems, homes, waypoints, skill/plugin progression, or credential appear.",
        "",
        "## Maintenance And Shutdown Gates",
        "",
        "1. Announce and enter maintenance using the approved human procedure.",
        "2. Stop Paper through Crafty. The migration engine must not stop/restart it.",
        "3. Verify shutdown: no Paper backend process, no Java file handles for target storage, no live source/target session.",
        "4. Refuse if maintenance state or shutdown gate fails.",
        "",
        "## Snapshot Gates",
        "",
        "1. Take final snapshot from the active server root.",
        "2. Run `SNAPSHOT_COMPLETENESS_CHECK` for registered configs and schema DBs.",
        "3. Run `OWNERSHIP_INTEGRITY_CHECK`; expected active server ownership is UID `1000`, GID `0`.",
        "4. Hash snapshot and staging restore; abort if any mismatch exists.",
        "",
        "## Apply And Verify",
        "",
        "1. In staging only, remove/replace verified safe-empty `2eab` runtime records.",
        "2. Preserve `70f` UUID-owned files/records in snapshot and staging. Shared `Mounk` namespace belongs to final `2eab` after cutover.",
        "3. Apply deterministic source-to-target transform.",
        "4. Verify every adapter and run final ownership verification on all touched paths.",
        "5. Abort and restore from snapshot on any failure.",
        "",
        "## Startup And Human Verification",
        "",
        "1. Start Paper through Crafty after successful verify.",
        "2. Human login as `Mounk`; confirm UUID in logs is `2eab1746-315b-3e81-b640-7f8241d3f2cd`.",
        "3. Verify inventory, XP, EnderChest, MVI inventory, skills, homes/waypoints, and plugin state.",
        "4. Only after human verification, mark finalization complete.",
        "",
        "## SimpleLogin",
        "",
        "Main migration strategy is `TRANSITION_PRESERVE`: do not copy password hashes or secrets. Source credential remains intact. After data verification, `Mounk`/`2eab` should register or have a password reset through the normal auth process. Ayla can replace SimpleLogin later only after a separate auth rollout; it is not part of the data cutover.",
        "",
        "## 70f Cleanup Policy",
        "",
        "`70f` is `PRESERVE_UNTIL_FINAL_HUMAN_VERIFY`. Cleanup is a later, separate operation: identify data created exclusively by the wrong pilot, prepare a cleanup plan, and require separate human confirmation. Do not clean up `70f` during the final cutover.",
        "",
        "```sh",
        "/opt/minecraft/migration-engine/bin/migrationctl final-cutover \\",
        f"  --source-uuid {SOURCE} --source-name {SOURCE_NAME} \\",
        f"  --target-uuid {TARGET} --target-name {TARGET_NAME} \\",
        f"  --wrong-target-uuid {WRONG_TARGET} \\",
        "  --confirm-final-cutover Mounk-2eab-final \\",
        "  --execute --allow-write",
        "```",
        "",
        "The command shown is the prepared maintenance-window entry point. In this prep session it was not executed against production.",
    ]
    (AUDIT / "final-cutover-runbook.md").write_text("\n".join(lines) + "\n")


def plan_only(args: argparse.Namespace) -> dict:
    sandbox = None
    if args.sandbox_root:
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        sandbox = run_final_sandbox(Path(args.sandbox_root), ENGINE / "sandbox" / f"final-cutover-{ts}")
    plan = build_plan_artifact(sandbox)
    write_artifacts(plan)
    return plan


# ---------------------------------------------------------------------------
# FINAL CUTOVER EXECUTION  (maintenance-window only; never called in prep)
# ---------------------------------------------------------------------------

def _assert_maintenance_mode(root: Path, source_uuid: str, target_uuid: str) -> None:
    """Block unless maintenance.json signals active maintenance for this cutover."""
    state_path = ENGINE / "state" / "maintenance.json"
    try:
        state = json.loads(state_path.read_text())
    except (FileNotFoundError, ValueError):
        raise AdapterError("MAINTENANCE_NOT_ACTIVE: no maintenance.json found")
    required = {
        "active": True,
        "source": source_uuid,
        "target": target_uuid,
        "server_root": str(SERVER_ROOT),
    }
    for k, v in required.items():
        if state.get(k) != v:
            raise AdapterError(
                f"MAINTENANCE_NOT_ACTIVE: field {k!r} expected {v!r} got {state.get(k)!r}"
            )
    if state.get("expires_at", 0) <= time.time():
        raise AdapterError("MAINTENANCE_NOT_ACTIVE: maintenance window expired")


def _assert_paper_offline(root: Path) -> None:
    """Block if any Java/Paper process appears to have the server root open."""
    import subprocess as _sp
    import socket as _socket

    # 1. Host process list
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            raw = (proc / "cmdline").read_bytes()
            parts = [os.fsdecode(x) for x in raw.split(b"\0") if x]
            if any("paper.jar" in p for p in parts):
                raise AdapterError(
                    f"SHUTDOWN_GATE_FAIL: Paper process still running (pid {proc.name})"
                )
        except (FileNotFoundError, PermissionError):
            continue

    # 2. Crafty Docker container
    res = _sp.run(
        ["docker", "exec", "crafty", "sh", "-lc",
         "ps -eo pid,args | grep '[p]aper.jar' || true"],
        capture_output=True, text=True,
    )
    if res.returncode == 0 and "paper.jar" in res.stdout:
        raise AdapterError("SHUTDOWN_GATE_FAIL: Paper process active inside Crafty container")

    # 3. Open file handles
    lsof = _sp.run(
        ["lsof", "+D", str(root / "plugins"), str(root / "world" / "players")],
        capture_output=True, text=True,
    )
    java_handles = [ln for ln in lsof.stdout.splitlines() if "java" in ln.lower()]
    if java_handles:
        raise AdapterError(
            "SHUTDOWN_GATE_FAIL: Java has open file handles to player/plugin storage: "
            + str(java_handles[:3])
        )

    # 4. TCP backend on 25565
    try:
        with _socket.create_connection(("127.0.0.1", 25565), timeout=1) as s:
            s.sendall(b"\xfe\x01")
            data = s.recv(16)
            if data:
                raise AdapterError(
                    "SHUTDOWN_GATE_FAIL: Something still accepts Minecraft connections on 25565"
                )
    except OSError:
        pass  # expected: port refused


def _verify_ownership_tree(root: Path) -> None:
    """Raise AdapterError if any file under root is not UID 1000 / GID 0."""
    OwnershipPolicy.explicit(root, EXPECTED_UID, EXPECTED_GID).check_tree(root)


def _assert_wrong_target_intact(snapshot_root: Path, staging: Path) -> None:
    """Block if 70f files in staging differ from the snapshot."""
    snap_files = {
        str(p.relative_to(snapshot_root)): sha256(p)
        for p in snapshot_root.rglob(f"*{WRONG_TARGET}*")
        if p.is_file()
    }
    staged_files = {
        str(p.relative_to(staging)): sha256(p)
        for p in staging.rglob(f"*{WRONG_TARGET}*")
        if p.is_file()
    }
    if snap_files != staged_files:
        raise AdapterError(
            "WRONG_TARGET_70F_INTEGRITY: 70f files changed after apply; "
            f"snapshot={sorted(snap_files)}, staging={sorted(staged_files)}"
        )


def execute_final_cutover(
    root: Path = SERVER_ROOT,
    *,
    dry_run_only: bool = False,
) -> dict:
    """
    Perform the final identity cutover 555dd93f -> 2eab1746 during a maintenance window.

    Gates (in order):
      1.  MAINTENANCE_MODE          - maintenance.json active + correct IDs + not expired
      2.  PAPER_OFFLINE             - no Java proc, no open handles, no port-25565 backend
      3+4.FINAL_SNAPSHOT+COMPLETENESS - copy root, verify required_registry_files present
      5.  OWNERSHIP_SNAPSHOT        - every snapshot file UID 1000 / GID 0
      6.  SAFE_EMPTY_RUNTIME_TARGET - 2eab still fresh/default; block on any real state
      7.  STAGING_CREATED           - policy.copytree(snap_root, staging)
      8.  SCRUB_SAFE_EMPTY          - remove default 2eab runtime records from staging
      9.  APPLY                     - OfflineRun.apply on staging (final_identity context)
     10.  VERIFY                    - every adapter verify() passes
     11.  SIMPLELOGIN_TRANSITION    - confirm no 2eab credential created
     12.  OWNERSHIP_POST_APPLY      - all staging files UID 1000 / GID 0
     13.  WRONG_TARGET_70F          - 70f files identical in staging vs snapshot
     14.  PAPER_OFFLINE_RECHECK     - re-confirm Paper still offline before publish
     15.  PUBLISH                   - atomic Publication(root, snap_root, staging).apply()
     16.  OWNERSHIP_POST_PUBLISH    - check all changed paths in live root

    dry_run_only=True runs gates 1-13 in sandbox and aborts before publish.
    Rollback is automatic on any gate failure after apply starts.
    """
    root = Path(root)
    _now = datetime.datetime.now(datetime.timezone.utc)
    ts = _now.strftime("%Y%m%dT%H%M%S") + f"{_now.microsecond:06d}Z"
    run_id = f"final-cutover-{ts}"
    run_dir = ENGINE / "runs" / run_id
    snap_root = ENGINE / "snapshots" / "final-cutover" / ts / "root"
    staging = ENGINE / "sandbox" / run_id / "staging"
    log: list[dict] = []
    policy = OwnershipPolicy.explicit(ENGINE, EXPECTED_UID, EXPECTED_GID)

    def gate(name: str, **info) -> dict:
        entry = {
            "gate": name,
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            **info,
        }
        log.append(entry)
        return entry

    run_obj: p5.OfflineRun | None = None
    apply_started = False

    try:
        # ---- Gate 1: MAINTENANCE MODE -------------------------------------- #
        _assert_maintenance_mode(root, SOURCE, TARGET)
        gate("MAINTENANCE_MODE", status="PASS")

        # ---- Gate 2: PAPER OFFLINE ---------------------------------------- #
        _assert_paper_offline(root)
        gate("PAPER_OFFLINE", status="PASS")

        # ---- Gate 3+4: FINAL SNAPSHOT + COMPLETENESS ----------------------- #
        snap_result = copy_final_snapshot(root, snap_root)
        gate("FINAL_SNAPSHOT", status="PASS", copied=snap_result["copied"])
        completeness = snap_result["completeness_check"]
        if completeness["status"] != "PASS":
            raise AdapterError(
                "SNAPSHOT_COMPLETENESS: missing " + json.dumps(completeness["missing_files"])
            )
        gate("SNAPSHOT_COMPLETENESS", status="PASS")

        # ---- Gate 5: OWNERSHIP on snapshot --------------------------------- #
        _verify_ownership_tree(snap_root)
        gate("OWNERSHIP_SNAPSHOT", status="PASS", uid=EXPECTED_UID, gid=EXPECTED_GID)

        # ---- Gate 6: SAFE EMPTY TARGET (re-run against live root) ---------- #
        safe_check = safe_empty_runtime_target_check(root)
        if safe_check["status"] != "PASS":
            raise AdapterError(
                "SAFE_EMPTY_RUNTIME_TARGET: " + json.dumps(safe_check["blockers"])
            )
        gate(
            "SAFE_EMPTY_RUNTIME_TARGET",
            status="PASS",
            classification=safe_check["classification"],
        )

        # ---- Gate 7: BUILD STAGING ---------------------------------------- #
        policy.copytree(snap_root, staging)
        gate("STAGING_CREATED", status="PASS", staging=str(staging))

        # ---- Gate 8: SCRUB SAFE EMPTY from staging ------------------------- #
        with final_identity():
            scrub = scrub_safe_empty_target(staging)
        gate("SCRUB_SAFE_EMPTY", status="PASS", removed=len(scrub["removed_from_staging"]))

        # ---- Gates 9+10: APPLY + VERIFY ------------------------------------ #
        snap_dir = ENGINE / "sandbox" / run_id / "adapter-snapshots"
        verify_result: dict = {}
        with final_identity():
            run_obj = p5.OfflineRun(staging)
            plan = run_obj.plan()
            if plan.get("blockers"):
                raise AdapterError(
                    "APPLY_PREFLIGHT: structural blockers " + str(plan["blockers"])
                )
            apply_started = True
            verify_result = run_obj.apply(snap_dir)

        gate("APPLY", status="PASS")
        failed_verify = [k for k, v in verify_result.items() if not v]
        if failed_verify:
            raise AdapterError("VERIFY: adapters failed: " + str(failed_verify))
        gate("VERIFY", status="PASS", adapters=verify_result)

        # ---- Gate 11: SIMPLELOGIN TRANSITION_PRESERVE ---------------------- #
        sl_db = staging / "plugins/SimpleLogin/passwords.db"
        if sl_db.exists():
            rows = _db_rows(sl_db)
            target_credential = [
                r
                for table_rows in rows.values()
                for r in table_rows
                if any(
                    TARGET in str(v) or TARGET_NAME in str(v)
                    for v in r.values()
                )
            ]
            if target_credential:
                raise AdapterError(
                    "SIMPLELOGIN_TRANSITION_PRESERVE: credential found for 2eab after apply"
                )
        gate("SIMPLELOGIN_TRANSITION_PRESERVE", status="PASS")

        # ---- Gate 12: OWNERSHIP after apply (staging) ---------------------- #
        _verify_ownership_tree(staging)
        gate("OWNERSHIP_POST_APPLY", status="PASS", uid=EXPECTED_UID, gid=EXPECTED_GID)

        # ---- Gate 13: 70F PRESERVATION ------------------------------------- #
        _assert_wrong_target_intact(snap_root, staging)
        gate("WRONG_TARGET_70F_PRESERVATION", status="PASS")

        if dry_run_only:
            gate(
                "DRY_RUN_COMPLETE",
                status="PASS",
                note="publish skipped: dry_run_only=True",
            )
            result = _build_exec_result(run_id, log, "DRY_RUN_COMPLETE", verify_result)
            _write_exec_result(run_dir, result)
            return result

        # ---- Gate 14: PAPER OFFLINE (re-check before publish) -------------- #
        _assert_paper_offline(root)
        gate("PAPER_OFFLINE_RECHECK", status="PASS")

        # ---- Gate 15: PUBLISH --------------------------------------------- #
        from .pilot import Publication

        publisher = Publication(root, snap_root, staging)
        publish_result = publisher.apply(lambda: _assert_paper_offline(root))
        gate("PUBLISH", status="PASS", changed_paths=publish_result["changed_paths"])

        # ---- Gate 16: OWNERSHIP on live changed paths ---------------------- #
        live_policy = OwnershipPolicy.explicit(root, EXPECTED_UID, EXPECTED_GID)
        for rel in publish_result["changed_paths"]:
            live_policy.check_path(root / rel)
        gate("OWNERSHIP_POST_PUBLISH", status="PASS", uid=EXPECTED_UID, gid=EXPECTED_GID)

        gate("WRONG_TARGET_70F_LIVE", status="PRESERVED", policy=WRONG_TARGET_POLICY)

        result = _build_exec_result(run_id, log, "CUTOVER_COMPLETE", verify_result)
        _write_exec_result(run_dir, result)
        return result

    except AdapterError as exc:
        gate("ROLLBACK_TRIGGERED", reason=str(exc))
        rollback_status = "SKIPPED_PRE_APPLY"
        if apply_started and run_obj is not None and hasattr(run_obj, "active"):
            try:
                with final_identity():
                    run_obj.rollback()
                rollback_status = "PASS"
            except Exception as rb_exc:
                rollback_status = f"FAIL: {rb_exc}"
        gate("ROLLBACK", status=rollback_status)
        result = _build_exec_result(run_id, log, "FAILED", {}, error=str(exc))
        _write_exec_result(run_dir, result)
        raise


def _build_exec_result(
    run_id: str,
    log: list[dict],
    final_status: str,
    verify: dict,
    *,
    error: str | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": SOURCE,
        "source_name": SOURCE_NAME,
        "target": TARGET,
        "target_name": TARGET_NAME,
        "wrong_pilot_target": WRONG_TARGET,
        "wrong_target_policy": WRONG_TARGET_POLICY,
        "simplelogin_strategy": "TRANSITION_PRESERVE",
        "ownership": {"expected_uid": EXPECTED_UID, "expected_gid": EXPECTED_GID},
        "verify": verify,
        "gates": log,
        "final_status": final_status,
        **({"error": error} if error else {}),
    }


def _write_exec_result(run_dir: Path, result: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "result.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    try:
        OwnershipPolicy.explicit(
            ENGINE, EXPECTED_UID, EXPECTED_GID
        ).normalize_path(run_dir, recursive=True)
    except Exception:
        pass  # best-effort; do not mask primary result
