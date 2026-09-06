from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .snapshot import DatabaseSnapshotEntry, FileSnapshotEntry, SnapshotManifest


class RollbackError(Exception):
    pass


class RollbackConflictError(RollbackError):
    pass


class GranularRollbackManager:
    """Executes granular, per-player rollback strictly guided by a SnapshotManifest."""

    def __init__(self, runs_dir: Path | str) -> None:
        self.runs_dir = Path(runs_dir).resolve()

    @staticmethod
    def sha256_file(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def rollback(
        self,
        migration_id: str,
        server_root: Path | str,
        *,
        manifest: SnapshotManifest | None = None,
    ) -> dict[str, Any]:
        server_root = Path(server_root).resolve()
        run_dir = self.runs_dir / migration_id

        if manifest is None:
            manifest_file = run_dir / "manifest.json"
            if not manifest_file.is_file():
                raise RollbackError(f"Manifesto não encontrado para {migration_id}")
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            manifest = SnapshotManifest.from_dict(data)

        if manifest.status != "VALID":
            raise RollbackError(f"Snapshot não está VALID (status={manifest.status}).")

        files_reverted = 0
        targets_cleaned = 0
        db_rows_reverted = 0

        # 1. Rollback SQLite rows (inverse DML on registered PKs)
        for db_entry in manifest.database_entries:
            db_full = (server_root / db_entry.db_rel).resolve()
            if not db_full.is_file():
                continue

            rows_done = self._rollback_sqlite_entry(db_entry, db_full)
            db_rows_reverted += rows_done

        # 2. Rollback Files
        for f_entry in manifest.file_entries:
            src_rel = f_entry.source_path_logical
            tgt_rel = f_entry.target_path_logical

            src_full = (server_root / src_rel).resolve() if src_rel else None
            tgt_full = (server_root / tgt_rel).resolve() if tgt_rel else None

            # A. If target was created during migration and did not exist before: remove it
            if tgt_full and not f_entry.target_existed and tgt_full.is_file():
                tgt_full.unlink()
                targets_cleaned += 1

            # B. If target existed before: restore it from backup
            elif tgt_full and f_entry.target_existed and f_entry.backup_target_rel:
                backup_tgt = run_dir / f_entry.backup_target_rel
                if backup_tgt.is_file():
                    shutil.copy2(backup_tgt, tgt_full)
                    if f_entry.mode is not None:
                        os.chmod(tgt_full, f_entry.mode)

            # C. Restore source file if it existed before
            if src_full and f_entry.source_existed and f_entry.backup_source_rel:
                backup_src = run_dir / f_entry.backup_source_rel
                if backup_src.is_file():
                    src_full.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup_src, src_full)
                    if f_entry.mode is not None:
                        try:
                            os.chmod(src_full, f_entry.mode)
                        except OSError:
                            pass
                    files_reverted += 1

        return {
            "status": "ROLLED_BACK",
            "migration_id": migration_id,
            "files_reverted": files_reverted,
            "targets_cleaned": targets_cleaned,
            "db_rows_reverted": db_rows_reverted,
        }

    def _rollback_sqlite_entry(self, entry: DatabaseSnapshotEntry, db_path: Path) -> int:
        """Execute inverse DML with conflict detection (current != expected_after)."""
        reverted_count = 0
        with sqlite3.connect(str(db_path), timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            try:
                for row_info in entry.rows:
                    pk_val = row_info["pk_value"]
                    uuid_col = row_info["uuid_column"]
                    before_dict = row_info["before"]
                    planned_after = row_info["planned_after"]
                    pk_is_uuid = (entry.pk_column == uuid_col)

                    # If pk_is_uuid, row might currently have PK == planned_after[pk_column] (if migrated)
                    # or PK == pk_val (if not migrated or already rolled back)
                    cur_row = None
                    current_pk = pk_val
                    if pk_is_uuid:
                        target_pk = planned_after.get(entry.pk_column)
                        cur_row = conn.execute(
                            f"SELECT * FROM {entry.table} WHERE {entry.pk_column} = ?",
                            (target_pk,),
                        ).fetchone()
                        if cur_row is not None:
                            current_pk = target_pk

                    if cur_row is None:
                        cur_row = conn.execute(
                            f"SELECT * FROM {entry.table} WHERE {entry.pk_column} = ?",
                            (pk_val,),
                        ).fetchone()
                        current_pk = pk_val

                    if cur_row is None:
                        raise RollbackConflictError(
                            f"ROLLBACK_CONFLICT: Linha PK {pk_val} em {entry.table} não existe mais no banco."
                        )

                    cur_dict = dict(cur_row)

                    # IDEMPOTENCY CHECK: if already equal to before, no-op
                    if cur_dict == before_dict:
                        continue

                    # CONFLICT CHECK: current must match planned_after!
                    # If someone/plugin changed this row to something else (C != B), abort!
                    if entry.adapter == "marriagemaster":
                        if (
                            cur_dict.get("player1") != planned_after.get("player1")
                            or cur_dict.get("player2") != planned_after.get("player2")
                        ):
                            raise RollbackConflictError(
                                f"ROLLBACK_CONFLICT: Linha PK {pk_val} em {entry.table} divergente do esperado pós-migração."
                            )
                    else:
                        if cur_dict.get(uuid_col) != planned_after.get(uuid_col):
                            raise RollbackConflictError(
                                f"ROLLBACK_CONFLICT: Linha PK {pk_val} em {entry.table} possui valor atual '{cur_dict.get(uuid_col)}', divergente do esperado pós-migração '{planned_after.get(uuid_col)}'."
                            )

                    # Execute inverse mutation strictly on the current PK
                    orig_val = before_dict.get(uuid_col)
                    if entry.adapter == "marriagemaster":
                        conn.execute(
                            f"UPDATE {entry.table} SET player1 = ?, player2 = ? WHERE {entry.pk_column} = ?",
                            (before_dict.get("player1"), before_dict.get("player2"), current_pk),
                        )
                    else:
                        conn.execute(
                            f"UPDATE {entry.table} SET {uuid_col} = ? WHERE {entry.pk_column} = ?",
                            (orig_val, current_pk),
                        )
                    reverted_count += 1

                conn.commit()
            except Exception:
                conn.rollback()
                raise

        return reverted_count
