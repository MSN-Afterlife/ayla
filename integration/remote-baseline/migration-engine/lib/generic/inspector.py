from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .domain import (
    MigrationDataFinding,
    MigrationIdentity,
    MigrationInspection,
    now_iso,
)
from .lock import PlayerPresenceChecker, PresenceState


class GenericInspector:
    def __init__(self, server_root: Path | str, presence_checker: PlayerPresenceChecker | None = None) -> None:
        self.server_root = Path(server_root).resolve()
        self.presence_checker = presence_checker or PlayerPresenceChecker(self.server_root)

    def inspect(self, identity: MigrationIdentity) -> MigrationInspection:
        identity.validate()
        findings: list[MigrationDataFinding] = []
        warnings: list[str] = []
        blockers: list[str] = []

        if not identity.legacy_uuid:
            blockers.append("LEGACY_UUID_MISSING: O jogador não possui legacy_uuid cadastrado.")
        if not identity.canonical_uuid:
            blockers.append("CANONICAL_UUID_MISSING: O jogador não possui canonical_uuid cadastrado.")

        # 1. Vanilla Data (playerdata, advancements, stats)
        vanilla_findings = self._inspect_vanilla(identity)
        findings.extend(vanilla_findings)

        # 2. Multiverse-Inventories
        mvi_finding = self._inspect_mvi(identity)
        findings.append(mvi_finding)

        # 3. AuraSkills
        auraskills_finding = self._inspect_auraskills(identity)
        findings.append(auraskills_finding)

        # 4. Quests
        quests_finding = self._inspect_quests(identity)
        findings.append(quests_finding)

        # 5. ImageFrame
        imageframe_finding = self._inspect_imageframe(identity)
        findings.append(imageframe_finding)

        # 6. Relational SQLite stores
        sqlite_findings = self._inspect_sqlite_all(identity)
        findings.extend(sqlite_findings)

        # 7. LuckPerms (H2 limited inspection)
        lp_finding = self._inspect_luckperms(identity)
        findings.append(lp_finding)

        # 8. Online status check
        is_online, online_warn = self._check_online(identity)
        if online_warn:
            warnings.append(online_warn)

        # Aggregate blockers and warnings from findings
        for f in findings:
            if f.conflict and f.blocker:
                blockers.append(f.blocker)
            elif f.warning:
                warnings.append(f.warning)

        # TOCTOU Fingerprint
        fingerprint = self._calculate_fingerprint(identity, findings)

        # Status computation
        if blockers:
            inspection_status = "BLOCKED"
        elif not identity.legacy_uuid or not identity.canonical_uuid:
            inspection_status = "INCOMPLETE"
        else:
            inspection_status = "READY"

        return MigrationInspection(
            identity=identity,
            server_root=str(self.server_root),
            status=inspection_status,
            findings=findings,
            warnings=sorted(set(warnings)),
            blockers=sorted(set(blockers)),
            is_online=is_online,
            inspection_fingerprint=fingerprint,
            inspected_at=now_iso(),
        )

    def _inspect_vanilla(self, identity: MigrationIdentity) -> list[MigrationDataFinding]:
        results = []
        leg_u = identity.legacy_uuid
        can_u = identity.canonical_uuid

        targets = [
            ("vanilla_playerdata", ["world/players/data", "world/playerdata"], ".dat"),
            ("vanilla_advancements", ["world/players/advancements", "world/advancements"], ".json"),
            ("vanilla_stats", ["world/players/stats", "world/stats"], ".json"),
        ]

        for adapter_name, candidate_dirs, ext in targets:
            src_path = None
            tgt_path = None
            for rel_dir in candidate_dirs:
                sp = self.server_root / rel_dir / f"{leg_u}{ext}" if leg_u else None
                tp = self.server_root / rel_dir / f"{can_u}{ext}" if can_u else None
                if sp and sp.is_file():
                    src_path = sp
                if tp and tp.is_file():
                    tgt_path = tp
                if not src_path and (self.server_root / rel_dir).is_dir():
                    src_path = sp
                if not tgt_path and (self.server_root / rel_dir).is_dir():
                    tgt_path = tp

            src_exists = bool(src_path and src_path.is_file())
            tgt_exists = bool(tgt_path and tgt_path.is_file())
            conflict = src_exists and tgt_exists

            tgt_file = f"{can_u}{ext}" if can_u else "target"
            blocker = f"TARGET_COLLISION: {adapter_name} já existe para o target {tgt_file}" if conflict else None
            status = "FOUND" if src_exists else "NOT_FOUND"

            details = {
                "source_path": str(src_path.relative_to(self.server_root)) if src_path and src_exists else None,
                "target_path": str(tgt_path.relative_to(self.server_root)) if tgt_path else None,
                "source_size_bytes": src_path.stat().st_size if src_exists and src_path else 0,
            }

            results.append(
                MigrationDataFinding(
                    adapter=adapter_name,
                    status=status,
                    source_found=src_exists,
                    target_found=tgt_exists,
                    source_records=1 if src_exists else 0,
                    target_records=1 if tgt_exists else 0,
                    details=details,
                    conflict=conflict,
                    blocker=blocker,
                    requires_player_offline=True,
                    requires_paper_offline_for_write=False,
                    migration_strategy="MOVE_TO_TARGET",
                )
            )

        return results

    def _inspect_mvi(self, identity: MigrationIdentity) -> MigrationDataFinding:
        base = self.server_root / "plugins/Multiverse-Inventories"
        name = identity.legacy_name or identity.canonical_name
        target_name = identity.canonical_name

        if not base.is_dir():
            return MigrationDataFinding(
                adapter="multiverse_inventories",
                status="NOT_FOUND",
                source_found=False,
                target_found=False,
                migration_strategy="RENAME_OR_MOVE",
            )

        leg_u = identity.legacy_uuid
        can_u = identity.canonical_uuid
        src_set = set()
        if name:
            src_set.update(base.rglob(f"{name}.json"))
        if leg_u:
            src_set.update(base.rglob(f"{leg_u}.json"))
        src_matches = sorted(list(src_set))

        tgt_set = set()
        if target_name and target_name != name:
            tgt_set.update(base.rglob(f"{target_name}.json"))
        if can_u and can_u != leg_u:
            tgt_set.update(base.rglob(f"{can_u}.json"))
        tgt_matches = sorted(list(tgt_set))

        src_exists = len(src_matches) > 0
        tgt_exists = len(tgt_matches) > 0
        conflict = src_exists and tgt_exists

        warning = "Multiverse-Inventories possui cache em memória com TTL de 60s pós-logout." if src_exists else None
        blocker = "MVI_TARGET_COLLISION: Inventário já existe no Multiverse para o target." if conflict else None

        return MigrationDataFinding(
            adapter="multiverse_inventories",
            status="FOUND" if src_exists else "NOT_FOUND",
            source_found=src_exists,
            target_found=tgt_exists,
            source_records=len(src_matches),
            target_records=len(tgt_matches),
            details={"source_files": [str(p.relative_to(self.server_root)) for p in src_matches]},
            conflict=conflict,
            warning=warning,
            blocker=blocker,
            requires_player_offline=True,
            requires_paper_offline_for_write=False,
            migration_strategy="RENAME_OR_MOVE",
        )

    def _inspect_auraskills(self, identity: MigrationIdentity) -> MigrationDataFinding:
        leg_u = identity.legacy_uuid
        can_u = identity.canonical_uuid
        base = self.server_root / "plugins/AuraSkills/userdata"

        src_file = base / f"{leg_u}.yml" if leg_u else None
        tgt_file = base / f"{can_u}.yml" if can_u else None

        src_exists = bool(src_file and src_file.is_file())
        tgt_exists = bool(tgt_file and tgt_file.is_file())
        conflict = src_exists and tgt_exists

        return MigrationDataFinding(
            adapter="auraskills",
            status="FOUND" if src_exists else "NOT_FOUND",
            source_found=src_exists,
            target_found=tgt_exists,
            source_records=1 if src_exists else 0,
            target_records=1 if tgt_exists else 0,
            details={
                "file": str(src_file.relative_to(self.server_root)) if src_exists and src_file else None,
                "target_file": str(tgt_file.relative_to(self.server_root)) if tgt_file else None,
            },
            conflict=conflict,
            blocker="AURASKILLS_TARGET_COLLISION: Arquivo de skills do target já existe." if conflict else None,
            requires_player_offline=True,
            requires_paper_offline_for_write=False,
            migration_strategy="MOVE_TO_TARGET",
        )

    def _inspect_quests(self, identity: MigrationIdentity) -> MigrationDataFinding:
        leg_u = identity.legacy_uuid
        can_u = identity.canonical_uuid
        base = self.server_root / "plugins/Quests/data"

        src_file = base / f"{leg_u}.yml" if leg_u else None
        tgt_file = base / f"{can_u}.yml" if can_u else None

        src_exists = bool(src_file and src_file.is_file())
        tgt_exists = bool(tgt_file and tgt_file.is_file())
        conflict = src_exists and tgt_exists

        return MigrationDataFinding(
            adapter="quests",
            status="FOUND" if src_exists else "NOT_FOUND",
            source_found=src_exists,
            target_found=tgt_exists,
            source_records=1 if src_exists else 0,
            target_records=1 if tgt_exists else 0,
            details={
                "file": str(src_file.relative_to(self.server_root)) if src_exists and src_file else None,
                "target_file": str(tgt_file.relative_to(self.server_root)) if tgt_file else None,
            },
            conflict=conflict,
            blocker="QUESTS_TARGET_COLLISION: Arquivo de quests do target já existe." if conflict else None,
            requires_player_offline=True,
            requires_paper_offline_for_write=False,
            migration_strategy="MOVE_TO_TARGET",
        )

    def _inspect_imageframe(self, identity: MigrationIdentity) -> MigrationDataFinding:
        leg_u = identity.legacy_uuid
        can_u = identity.canonical_uuid
        base = self.server_root / "plugins/ImageFrame"

        player_src = base / "players" / f"{leg_u}.json" if leg_u else None
        player_tgt = base / "players" / f"{can_u}.json" if can_u else None

        src_exists = bool(player_src and player_src.is_file())
        tgt_exists = bool(player_tgt and player_tgt.is_file())

        maps_found = 0
        data_dir = base / "data"
        if data_dir.is_dir() and leg_u:
            for p in data_dir.glob("*/data.json"):
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                    if leg_u in text or leg_u.replace("-", "") in text:
                        maps_found += 1
                except OSError:
                    pass

        total_src = (1 if src_exists else 0) + maps_found
        conflict = src_exists and tgt_exists

        return MigrationDataFinding(
            adapter="imageframe",
            status="FOUND" if total_src > 0 else "NOT_FOUND",
            source_found=total_src > 0,
            target_found=tgt_exists,
            source_records=total_src,
            target_records=1 if tgt_exists else 0,
            details={"player_file": src_exists, "maps_creator_count": maps_found},
            conflict=conflict,
            blocker="IMAGEFRAME_TARGET_COLLISION: Perfil do target já existe no ImageFrame." if conflict else None,
            requires_player_offline=True,
            requires_paper_offline_for_write=False,
            migration_strategy="UPDATE_JSON_REFS",
        )

    def _inspect_sqlite_all(self, identity: MigrationIdentity) -> list[MigrationDataFinding]:
        results = []
        leg_u = identity.legacy_uuid
        can_u = identity.canonical_uuid

        # 1. HuskHomes
        results.append(
            self._query_sqlite(
                adapter="huskhomes",
                db_rel="plugins/HuskHomes/HuskHomesData.db",
                queries=[
                    ("SELECT COUNT(*) FROM huskhomes_users WHERE uuid = ?", leg_u, can_u),
                    ("SELECT COUNT(*) FROM huskhomes_homes WHERE owner_uuid = ?", leg_u, can_u),
                    ("SELECT COUNT(*) FROM huskhomes_saved_positions WHERE player_uuid = ?", leg_u, can_u),
                ],
            )
        )

        # 2. EliteMobs
        elite_rel = (
            "plugins/EliteMobs/data/player_data.db"
            if (self.server_root / "plugins/EliteMobs/data/player_data.db").is_file()
            else "plugins/EliteMobs/elitemobs.db"
        )
        results.append(
            self._query_sqlite(
                adapter="elitemobs",
                db_rel=elite_rel,
                queries=[
                    ("SELECT COUNT(*) FROM PlayerData WHERE PlayerUUID = ?", leg_u, can_u),
                    ("SELECT COUNT(*) FROM player_data WHERE player_uuid = ?", leg_u, can_u),
                ],
            )
        )

        # 3. Waypoints
        results.append(
            self._query_sqlite(
                adapter="waypoints",
                db_rel="plugins/Waypoints/waypoints.db",
                queries=[
                    ("SELECT COUNT(*) FROM waypoints WHERE owner = ?", leg_u, can_u),
                    ("SELECT COUNT(*) FROM folders WHERE owner = ?", leg_u, can_u),
                    ("SELECT COUNT(*) FROM waypoints WHERE player_uuid = ?", leg_u, can_u),
                ],
            )
        )

        # 4. SimplePets
        results.append(
            self._query_sqlite(
                adapter="simplepets",
                db_rel="plugins/SimplePets/storage.db",
                queries=[
                    ("SELECT COUNT(*) FROM simplepets_players WHERE uuid = ?", leg_u, can_u),
                    ("SELECT COUNT(*) FROM pet_data WHERE owner_uuid = ?", leg_u, can_u),
                ],
            )
        )

        # 5. MarriageMaster
        results.append(
            self._query_sqlite(
                adapter="marriagemaster",
                db_rel="plugins/MarriageMaster/database.db",
                queries=[
                    ("SELECT COUNT(*) FROM marry_players WHERE uuid = ?", leg_u, can_u),
                    ("SELECT COUNT(*) FROM marry_partners WHERE player1 = ? OR player2 = ?", leg_u, can_u),
                    ("SELECT COUNT(*) FROM marriages WHERE player1 = ? OR player2 = ?", leg_u, can_u),
                ],
            )
        )

        # 6. UltimateTeams
        results.append(
            self._query_sqlite(
                adapter="ultimateteams",
                db_rel="plugins/UltimateTeams/UltimateTeamsData.db",
                queries=[
                    ("SELECT COUNT(*) FROM ultimateteams_users WHERE uuid = ?", leg_u, can_u),
                    ("SELECT COUNT(*) FROM team_members WHERE player_uuid = ?", leg_u, can_u),
                ],
            )
        )

        # 7. SimpleLogin (read-only inspection, transition-preserve)
        sl_db = self.server_root / "plugins/SimpleLogin/passwords.db"
        sl_exists = sl_db.is_file()
        sl_records = 0
        if sl_exists and leg_u:
            try:
                with sqlite3.connect(f"file:{sl_db}?mode=ro", uri=True) as conn:
                    for tbl in ["users", "passwords", "accounts"]:
                        try:
                            row = conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE uuid = ?", (leg_u,)).fetchone()
                            if row:
                                sl_records += row[0]
                        except sqlite3.OperationalError:
                            pass
            except Exception:
                pass

        results.append(
            MigrationDataFinding(
                adapter="simplelogin",
                status="FOUND" if sl_records > 0 else ("PRESENT_INSPECTION_ONLY" if sl_exists else "NOT_FOUND"),
                source_found=sl_records > 0,
                target_found=False,
                source_records=sl_records,
                target_records=0,
                details={"policy": "TRANSITION_PRESERVE", "note": "Credenciais não são copiadas; Ayla assume auth."},
                conflict=False,
                requires_player_offline=True,
                requires_paper_offline_for_write=False,
                migration_strategy="TRANSITION_PRESERVE",
            )
        )

        return results

    def _query_sqlite(
        self, adapter: str, db_rel: str, queries: list[tuple[str, str | None, str | None]]
    ) -> MigrationDataFinding:
        db_path = self.server_root / db_rel
        if not db_path.is_file():
            return MigrationDataFinding(
                adapter=adapter,
                status="NOT_FOUND",
                source_found=False,
                target_found=False,
                migration_strategy="SQL_UPDATE_INVERSE_DML",
            )

        src_count = 0
        tgt_count = 0

        try:
            # Strictly READ-ONLY URI mode
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3.0) as conn:
                for sql, src_param, tgt_param in queries:
                    if src_param:
                        params = (src_param, src_param) if sql.count("?") == 2 else (src_param,)
                        try:
                            row = conn.execute(sql, params).fetchone()
                            if row:
                                src_count += int(row[0])
                        except sqlite3.OperationalError:
                            pass

                    if tgt_param:
                        params = (tgt_param, tgt_param) if sql.count("?") == 2 else (tgt_param,)
                        try:
                            row = conn.execute(sql, params).fetchone()
                            if row:
                                tgt_count += int(row[0])
                        except sqlite3.OperationalError:
                            pass
        except Exception as err:
            return MigrationDataFinding(
                adapter=adapter,
                status="LIMITED",
                source_found=False,
                target_found=False,
                warning=f"Falha ao ler {adapter} em modo somente leitura: {err}",
                migration_strategy="SQL_UPDATE_INVERSE_DML",
            )

        conflict = src_count > 0 and tgt_count > 0
        blocker = f"{adapter.upper()}_TARGET_COLLISION: target já possui {tgt_count} registro(s) no banco." if conflict else None

        return MigrationDataFinding(
            adapter=adapter,
            status="FOUND" if src_count > 0 else "NOT_FOUND",
            source_found=src_count > 0,
            target_found=tgt_count > 0,
            source_records=src_count,
            target_records=tgt_count,
            details={"database": db_rel},
            conflict=conflict,
            blocker=blocker,
            requires_player_offline=True,
            requires_paper_offline_for_write=False,
            migration_strategy="SQL_UPDATE_INVERSE_DML",
        )

    def _inspect_luckperms(self, identity: MigrationIdentity) -> MigrationDataFinding:
        lp_file = self.server_root / "plugins/LuckPerms/luckperms-h2-v2.mv.db"
        exists = lp_file.is_file()

        warning = (
            "LuckPerms utiliza base H2 com lock exclusivo em tempo de execução. "
            "Escrita segura exige console Bukkit ('lp user clone') ou janela de manutenção offline."
        ) if exists else None

        return MigrationDataFinding(
            adapter="luckperms",
            status="LIMITED" if exists else "NOT_FOUND",
            source_found=exists,
            target_found=False,
            source_records=1 if exists else 0,
            target_records=0,
            details={
                "storage_type": "H2_MVSTORE",
                "file": "plugins/LuckPerms/luckperms-h2-v2.mv.db" if exists else None,
                "strategy": "WRITE_REQUIRES_OFFLINE_OR_CONSOLE",
            },
            conflict=False,
            warning=warning,
            requires_player_offline=True,
            requires_paper_offline_for_write=True,
            migration_strategy="CONSOLE_CLONE_OR_OFFLINE",
        )

    def _check_online(self, identity: MigrationIdentity) -> tuple[bool, str | None]:
        state = self.presence_checker.presence_state(identity)
        if state == PresenceState.UNKNOWN:
            return False, "PRESENCE_UNKNOWN: autoridade runtime não confirmou o estado do jogador."
        if state == PresenceState.ONLINE:
            return True, "PLAYER_ONLINE: autoridade runtime confirmou uma sessão ativa."
        return False, None

    def _calculate_fingerprint(self, identity: MigrationIdentity, findings: list[MigrationDataFinding]) -> str:
        h = hashlib.sha256()
        h.update(identity.canonical_uuid.encode())
        if identity.legacy_uuid:
            h.update(identity.legacy_uuid.encode())
        for f in sorted(findings, key=lambda x: x.adapter):
            payload = f"{f.adapter}:{f.status}:{f.source_found}:{f.target_found}:{f.source_records}:{f.target_records}"
            h.update(payload.encode())
        return h.hexdigest()
