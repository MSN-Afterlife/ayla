from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .domain import MigrationIdentity, MigrationPlan, now_iso


@dataclass
class FileSnapshotEntry:
    adapter: str
    source_path_logical: str | None
    target_path_logical: str | None
    source_existed: bool
    target_existed: bool
    source_sha256_before: str | None = None
    target_sha256_before: str | None = None
    source_size: int = 0
    target_size: int = 0
    mode: int | None = None
    uid: int | None = None
    gid: int | None = None
    backup_source_rel: str | None = None
    backup_target_rel: str | None = None
    planned_action: str = "MOVE_FILE"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileSnapshotEntry:
        return cls(**data)


@dataclass
class DatabaseSnapshotEntry:
    adapter: str
    db_rel: str
    table: str
    pk_column: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    # Each row dict has: {"pk_value": Any, "before": dict, "planned_after": dict}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DatabaseSnapshotEntry:
        return cls(**data)


@dataclass
class SnapshotManifest:
    migration_id: str
    created_at: str
    identity: MigrationIdentity
    inspection_fingerprint: str
    status: str = "VALID"
    file_entries: list[FileSnapshotEntry] = field(default_factory=list)
    database_entries: list[DatabaseSnapshotEntry] = field(default_factory=list)
    validation_results: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "migration_id": self.migration_id,
            "created_at": self.created_at,
            "identity": self.identity.to_dict(),
            "inspection_fingerprint": self.inspection_fingerprint,
            "status": self.status,
            "file_entries": [f.to_dict() for f in self.file_entries],
            "database_entries": [d.to_dict() for d in self.database_entries],
            "validation_results": self.validation_results,
            "errors": self.errors,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SnapshotManifest:
        return cls(
            migration_id=data["migration_id"],
            created_at=data["created_at"],
            identity=MigrationIdentity.from_dict(data["identity"]),
            inspection_fingerprint=data.get("inspection_fingerprint", ""),
            status=data.get("status", "VALID"),
            file_entries=[FileSnapshotEntry.from_dict(f) for f in data.get("file_entries", [])],
            database_entries=[DatabaseSnapshotEntry.from_dict(d) for d in data.get("database_entries", [])],
            validation_results=data.get("validation_results", {}),
            errors=list(data.get("errors", [])),
            metadata=data.get("metadata", {}),
        )


class SnapshotError(Exception):
    pass


class GranularSnapshotManager:
    """Creates isolated, granular per-player snapshots before any migration mutation."""

    def __init__(self, runs_dir: Path | str) -> None:
        self.runs_dir = Path(runs_dir).resolve()
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def sha256_file(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def create_snapshot(
        self,
        migration_id: str,
        plan: MigrationPlan,
        server_root: Path | str,
    ) -> SnapshotManifest:
        server_root = Path(server_root).resolve()
        run_dir = self.runs_dir / migration_id
        snap_files_dir = run_dir / "snapshot" / "files"
        snap_db_dir = run_dir / "snapshot" / "database_rows"
        logs_dir = run_dir / "logs"

        snap_files_dir.mkdir(parents=True, exist_ok=True)
        snap_db_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)

        identity = plan.identity
        leg_u = identity.legacy_uuid
        can_u = identity.canonical_uuid
        if not leg_u or not can_u:
            raise SnapshotError("Identidade incompleta para snapshot.")

        file_entries: list[FileSnapshotEntry] = []
        db_entries: list[DatabaseSnapshotEntry] = []

        manifest_final = run_dir / "manifest.json"
        creating_manifest = SnapshotManifest(
            migration_id=migration_id,
            created_at=now_iso(),
            identity=identity,
            inspection_fingerprint=plan.inspection_fingerprint,
            status="CREATING",
            metadata={"plan_id": plan.plan_id},
        )
        self._atomic_write_manifest(manifest_final, creating_manifest)

        try:
            # 1. Capture Files
            for change in plan.planned_changes:
                adapter = change.get("adapter", "")
                action = change.get("action", "")

                # File migrations
                if action in ("MOVE_FILE", "UPDATE_IMAGEFRAME"):
                    src_rel = change.get("source") or (f"plugins/ImageFrame/players/{leg_u}.json" if adapter == "imageframe" else None)
                    tgt_rel = change.get("target") or (f"plugins/ImageFrame/players/{can_u}.json" if adapter == "imageframe" else None)

                    if src_rel:
                        src_full = (server_root / src_rel).resolve()
                        # Security check: must reside inside server_root
                        try:
                            src_full.relative_to(server_root)
                        except ValueError:
                            raise SnapshotError(f"Caminho fonte fora do server_root: {src_rel}")

                        src_existed = src_full.is_file()
                        src_hash = self.sha256_file(src_full) if src_existed else None
                        stat = src_full.stat() if src_existed else None

                        backup_src_rel = None
                        if src_existed:
                            backup_name = f"{adapter}_src_{src_full.name}"
                            dst = snap_files_dir / backup_name
                            shutil.copy2(src_full, dst)
                            backup_src_rel = str(dst.relative_to(run_dir))

                        tgt_full = (server_root / tgt_rel).resolve() if tgt_rel else None
                        if tgt_full:
                            try:
                                tgt_full.relative_to(server_root)
                            except ValueError:
                                raise SnapshotError(f"Caminho destino fora do server_root: {tgt_rel}")

                        tgt_existed = bool(tgt_full and tgt_full.is_file())
                        tgt_hash = self.sha256_file(tgt_full) if tgt_existed and tgt_full else None

                        backup_tgt_rel = None
                        if tgt_existed and tgt_full:
                            backup_tgt_name = f"{adapter}_tgt_{tgt_full.name}"
                            dst = snap_files_dir / backup_tgt_name
                            shutil.copy2(tgt_full, dst)
                            backup_tgt_rel = str(dst.relative_to(run_dir))

                        file_entries.append(
                            FileSnapshotEntry(
                                adapter=adapter,
                                source_path_logical=src_rel,
                                target_path_logical=tgt_rel,
                                source_existed=src_existed,
                                target_existed=tgt_existed,
                                source_sha256_before=src_hash,
                                target_sha256_before=tgt_hash,
                                source_size=stat.st_size if stat else 0,
                                target_size=tgt_full.stat().st_size if tgt_existed and tgt_full else 0,
                                mode=stat.st_mode if stat else None,
                                uid=stat.st_uid if stat else None,
                                gid=stat.st_gid if stat else None,
                                backup_source_rel=backup_src_rel,
                                backup_target_rel=backup_tgt_rel,
                                planned_action=action,
                            )
                        )

                elif action == "RENAME_MVI_JSON":
                    source_files = change.get("source_files", [])
                    for src_rel in source_files:
                        src_full = (server_root / src_rel).resolve()
                        try:
                            src_full.relative_to(server_root)
                        except ValueError:
                            raise SnapshotError(f"Caminho Multiverse fora do server_root: {src_rel}")

                        if not src_full.is_file():
                            continue

                        stat = src_full.stat()
                        src_hash = self.sha256_file(src_full)
                        if leg_u and can_u and leg_u in src_rel:
                            tgt_rel = src_rel.replace(leg_u, can_u)
                        elif identity.legacy_name and identity.canonical_name and identity.legacy_name in src_rel:
                            tgt_rel = src_rel.replace(identity.legacy_name, identity.canonical_name)
                        else:
                            tgt_rel = src_rel

                        tgt_full = server_root / tgt_rel
                        tgt_existed = tgt_full.is_file()
                        tgt_hash = self.sha256_file(tgt_full) if tgt_existed else None

                        safe_src_name = src_rel.replace("/", "_").replace("\\", "_")
                        dst = snap_files_dir / f"mvi_{safe_src_name}"
                        shutil.copy2(src_full, dst)

                        file_entries.append(
                            FileSnapshotEntry(
                                adapter="multiverse_inventories",
                                source_path_logical=src_rel,
                                target_path_logical=tgt_rel,
                                source_existed=True,
                                target_existed=tgt_existed,
                                source_sha256_before=src_hash,
                                target_sha256_before=tgt_hash,
                                source_size=stat.st_size,
                                target_size=tgt_full.stat().st_size if tgt_existed else 0,
                                mode=stat.st_mode,
                                uid=stat.st_uid,
                                gid=stat.st_gid,
                                backup_source_rel=str(dst.relative_to(run_dir)),
                                backup_target_rel=None,
                                planned_action=action,
                            )
                        )

                # Database row snapshots
                elif action == "UPDATE_SQL_ROWS":
                    db_rel = change.get("database")
                    if not db_rel:
                        continue
                    db_full = (server_root / db_rel).resolve()
                    if not db_full.is_file():
                        continue

                    entries = self._snapshot_sqlite_rows(adapter, db_rel, db_full, leg_u, can_u)
                    for entry in entries:
                        if entry.rows:
                            db_entries.append(entry)
                            dump_file = snap_db_dir / f"{adapter}_{entry.table}.json"
                            dump_file.write_text(json.dumps(entry.to_dict(), indent=2), encoding="utf-8")
        except Exception as err:
            invalid_manifest = SnapshotManifest(
                migration_id=migration_id,
                created_at=creating_manifest.created_at,
                identity=identity,
                inspection_fingerprint=plan.inspection_fingerprint,
                status="INVALID",
                file_entries=file_entries,
                database_entries=db_entries,
                validation_results={"valid": False},
                errors=[str(err)],
                metadata={"plan_id": plan.plan_id},
            )
            self._atomic_write_manifest(manifest_final, invalid_manifest)
            raise

        manifest = SnapshotManifest(
            migration_id=migration_id,
            created_at=now_iso(),
            identity=identity,
            inspection_fingerprint=plan.inspection_fingerprint,
            status="VALID",
            file_entries=file_entries,
            database_entries=db_entries,
            validation_results=self._validate_entries(run_dir, file_entries, db_entries),
            metadata={"plan_id": plan.plan_id},
        )

        self._atomic_write_manifest(manifest_final, manifest)

        return manifest

    def _atomic_write_manifest(self, manifest_final: Path, manifest: SnapshotManifest) -> None:
        manifest_tmp = manifest_final.with_suffix(".json.tmp")
        manifest_tmp.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
        os.replace(manifest_tmp, manifest_final)

    def _validate_entries(
        self,
        run_dir: Path,
        file_entries: list[FileSnapshotEntry],
        db_entries: list[DatabaseSnapshotEntry],
    ) -> dict[str, Any]:
        checked_files = 0
        checked_db_rows = 0
        for entry in file_entries:
            if entry.source_existed:
                if not entry.backup_source_rel:
                    raise SnapshotError(f"Snapshot sem backup fonte: {entry.source_path_logical}")
                backup = run_dir / entry.backup_source_rel
                if not backup.is_file():
                    raise SnapshotError(f"Backup fonte ausente: {entry.source_path_logical}")
                if entry.source_sha256_before and self.sha256_file(backup) != entry.source_sha256_before:
                    raise SnapshotError(f"Hash de backup fonte diverge: {entry.source_path_logical}")
                if backup.stat().st_size != entry.source_size:
                    raise SnapshotError(f"Tamanho de backup fonte diverge: {entry.source_path_logical}")
                checked_files += 1
            if entry.target_existed:
                if not entry.backup_target_rel:
                    raise SnapshotError(f"Snapshot sem backup alvo: {entry.target_path_logical}")
                backup = run_dir / entry.backup_target_rel
                if not backup.is_file():
                    raise SnapshotError(f"Backup alvo ausente: {entry.target_path_logical}")
                if entry.target_sha256_before and self.sha256_file(backup) != entry.target_sha256_before:
                    raise SnapshotError(f"Hash de backup alvo diverge: {entry.target_path_logical}")
                checked_files += 1
        for entry in db_entries:
            for row in entry.rows:
                if "before" not in row or "planned_after" not in row or "pk_value" not in row:
                    raise SnapshotError(f"Linha SQLite incompleta em {entry.table}")
                checked_db_rows += 1
        return {"valid": True, "files_checked": checked_files, "db_rows_checked": checked_db_rows}

    def _snapshot_sqlite_rows(
        self,
        adapter: str,
        db_rel: str,
        db_path: Path,
        legacy_uuid: str,
        canonical_uuid: str,
    ) -> list[DatabaseSnapshotEntry]:
        """Extract exact affected rows for a given player before mutation."""
        table_configs = {
            "huskhomes": [
                ("huskhomes_users", "uuid", "uuid"),
                ("huskhomes_homes", "uuid", "owner_uuid"),
                ("huskhomes_saved_positions", "id", "player_uuid"),
            ],
            "elitemobs": [
                ("PlayerData", "PlayerUUID", "PlayerUUID"),
                ("player_data", "player_uuid", "player_uuid"),
            ],
            "waypoints": [
                ("waypoints", "id", "owner"),
                ("folders", "id", "owner"),
                ("waypoints", "id", "player_uuid"),
            ],
            "ultimateteams": [
                ("ultimateteams_users", "uuid", "uuid"),
                ("team_members", "id", "player_uuid"),
            ],
            "simplepets": [
                ("simplepets_players", "uuid", "uuid"),
                ("pet_data", "id", "owner_uuid"),
            ],
            "marriagemaster": [
                ("marry_players", "player_id", "uuid"),
                ("marry_partners", "marry_id", "player1"),
                ("marriages", "id", "player1"),
            ],
        }

        cfgs = table_configs.get(adapter, [])
        if not cfgs:
            return []

        entries: list[DatabaseSnapshotEntry] = []

        try:
            uri = f"file:{db_path}?mode=ro"
            with sqlite3.connect(uri, uri=True) as conn:
                conn.row_factory = sqlite3.Row
                tables_present = {
                    r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }

                for table, pk_col, uuid_col in cfgs:
                    if table not in tables_present:
                        continue

                    col_names = {c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                    if uuid_col not in col_names or pk_col not in col_names:
                        continue

                    rows_captured = []
                    if adapter == "marriagemaster" and "player2" in col_names:
                        cursor = conn.execute(
                            f"SELECT * FROM {table} WHERE player1 = ? OR player2 = ?",
                            (legacy_uuid, legacy_uuid),
                        )
                    else:
                        cursor = conn.execute(f"SELECT * FROM {table} WHERE {uuid_col} = ?", (legacy_uuid,))

                    for r in cursor.fetchall():
                        row_dict = dict(r)
                        pk_val = row_dict.get(pk_col)

                        after_dict = dict(row_dict)
                        if adapter == "marriagemaster":
                            if after_dict.get("player1") == legacy_uuid:
                                after_dict["player1"] = canonical_uuid
                            if after_dict.get("player2") == legacy_uuid:
                                after_dict["player2"] = canonical_uuid
                        else:
                            after_dict[uuid_col] = canonical_uuid

                        rows_captured.append({
                            "pk_value": pk_val,
                            "uuid_column": uuid_col,
                            "before": row_dict,
                            "planned_after": after_dict,
                        })

                    if rows_captured:
                        entries.append(
                            DatabaseSnapshotEntry(
                                adapter=adapter,
                                db_rel=db_rel,
                                table=table,
                                pk_column=pk_col,
                                rows=rows_captured,
                            )
                        )
        except Exception as e:
            raise SnapshotError(f"Erro ao capturar snapshot SQLite para {adapter}: {e}") from e

        return entries
