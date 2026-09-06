from __future__ import annotations

import json
import os
import shutil
import sqlite3
from enum import Enum
from pathlib import Path
from typing import Any

from .domain import MigrationIdentity, MigrationPlan, now_iso
from .inspector import GenericInspector
from .lock import PlayerMigrationLock, PlayerOnlineError, PlayerPresenceChecker
from .rollback import GranularRollbackManager, RollbackError
from .snapshot import GranularSnapshotManager, SnapshotManifest


class ProductionWriteDisabled(Exception):
    pass


class StalePlanError(Exception):
    pass


class ApplyExecutionError(Exception):
    pass


class VerificationError(Exception):
    pass


class FailureInjectionTriggered(Exception):
    pass


class MigrationState(str, Enum):
    REQUESTED = "REQUESTED"
    PLANNED = "PLANNED"
    LOCK_ACQUIRING = "LOCK_ACQUIRING"
    LOCKED = "LOCKED"
    REVALIDATING = "REVALIDATING"
    SNAPSHOTTING = "SNAPSHOTTING"
    APPLYING = "APPLYING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"
    CRITICAL_FAILURE = "CRITICAL_FAILURE"


# Hardcoded absolute path of the real production Minecraft server
PRODUCTION_SERVER_ROOT = Path("/opt/minecraft/crafty/servers/c253fa7e-2bd2-4545-a9ba-b1a8db892197").resolve()


class GenericMigrationRun:
    """Orchestrates transactional sandbox migration runs with atomic state machine and automatic rollback."""

    def __init__(
        self,
        server_root: Path | str,
        runs_dir: Path | str,
        locks_dir: Path | str,
        *,
        presence_checker: PlayerPresenceChecker | None = None,
        inject_failure_at: str | None = None,
    ) -> None:
        self.server_root = Path(server_root).resolve()
        self.runs_dir = Path(runs_dir).resolve()
        self.locks_dir = Path(locks_dir).resolve()
        if presence_checker is not None:
            self.presence_checker = presence_checker
        elif self._is_isolated_sandbox_root(self.server_root):
            self.presence_checker = PlayerPresenceChecker(self.server_root, online_override=set())
        else:
            self.presence_checker = PlayerPresenceChecker(self.server_root)
        self.inject_failure_at = inject_failure_at

        self.snapshot_mgr = GranularSnapshotManager(self.runs_dir)
        self.rollback_mgr = GranularRollbackManager(self.runs_dir)
        self.lock_mgr = PlayerMigrationLock(self.locks_dir)
        self.state = MigrationState.REQUESTED
        self.state_history: list[tuple[str, str]] = [(MigrationState.REQUESTED.value, now_iso())]

    @staticmethod
    def _is_isolated_sandbox_root(path: Path) -> bool:
        text = str(path.resolve())
        return text.startswith("/tmp/") or text.startswith("/var/tmp/") or text.startswith(str(Path("/opt/minecraft/migration-engine/sandbox").resolve()) + "/")

    def _transition(self, new_state: MigrationState, run_dir: Path | None = None) -> None:
        self.state = new_state
        ts = now_iso()
        self.state_history.append((new_state.value, ts))
        if run_dir:
            status_file = run_dir / "state.json"
            try:
                status_file.write_text(
                    json.dumps({"state": new_state.value, "history": self.state_history}, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass

    def execute(self, migration_id: str, plan: MigrationPlan, *, allow_write: bool = False) -> dict[str, Any]:
        # --- HARD GATE: PRODUCTION WRITE PROTECTION ---
        if self.server_root == PRODUCTION_SERVER_ROOT:
            raise ProductionWriteDisabled(
                "PRODUCTION_GATE: Mutação direta no servidor de produção está terminantemente bloqueada nesta fase. "
                "Execuções são permitidas exclusivamente em ambientes sandbox ou cópias isoladas."
            )

        if not allow_write:
            raise ApplyExecutionError("allow_write=True é obrigatório para execução em sandbox.")

        if plan.status != "READY":
            raise ApplyExecutionError(f"Plano não está em estado READY (status={plan.status}).")

        identity = plan.identity
        run_dir = self.runs_dir / migration_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self._transition(MigrationState.PLANNED, run_dir)

        manifest: SnapshotManifest | None = None
        locks_acquired = False

        try:
            # 1. Player presence check
            self.presence_checker.assert_player_offline(identity)

            # 2. Acquire locks
            self._transition(MigrationState.LOCK_ACQUIRING, run_dir)
            self.lock_mgr.acquire(migration_id, identity)
            locks_acquired = True
            self._transition(MigrationState.LOCKED, run_dir)

            if self.inject_failure_at == "after_lock":
                raise FailureInjectionTriggered("Falha injetada: after_lock")

            # 3. TOCTOU Re-Validation
            self._transition(MigrationState.REVALIDATING, run_dir)
            inspector = GenericInspector(self.server_root, presence_checker=self.presence_checker)
            re_inspection = inspector.inspect(identity)
            if re_inspection.inspection_fingerprint != plan.inspection_fingerprint:
                raise StalePlanError(
                    f"STALE_PLAN: Fingerprint do servidor divergiu do plano ({re_inspection.inspection_fingerprint[:12]} != {plan.inspection_fingerprint[:12]})."
                )

            # 4. Granular Snapshot
            self._transition(MigrationState.SNAPSHOTTING, run_dir)
            manifest = self.snapshot_mgr.create_snapshot(migration_id, plan, self.server_root)

            if self.inject_failure_at == "after_snapshot":
                raise FailureInjectionTriggered("Falha injetada: after_snapshot")

            # 5. Apply Mutations
            self._transition(MigrationState.APPLYING, run_dir)
            if manifest.status != "VALID":
                raise ApplyExecutionError(f"Snapshot não está VALID (status={manifest.status}).")
            self._apply_mutations(plan, manifest)

            if self.inject_failure_at == "before_verify":
                raise FailureInjectionTriggered("Falha injetada: before_verify")

            # 6. Verify Applied State
            self._transition(MigrationState.VERIFYING, run_dir)
            if self.inject_failure_at == "during_verify":
                raise VerificationError("Falha injetada: during_verify")
            self._verify_applied(plan, manifest)

            # 7. Succeeded
            self._transition(MigrationState.SUCCEEDED, run_dir)
            return {
                "status": MigrationState.SUCCEEDED.value,
                "migration_id": migration_id,
                "history": self.state_history,
            }

        except Exception as exec_err:
            # Automatic Rollback on failure
            self._transition(MigrationState.ROLLING_BACK, run_dir)
            rollback_success = False
            try:
                if self.inject_failure_at == "during_rollback":
                    raise RollbackError("Falha crítica injetada durante rollback")

                if manifest:
                    self.rollback_mgr.rollback(migration_id, self.server_root, manifest=manifest)
                    self._verify_rolled_back(plan, manifest)
                    rollback_success = True
                    self._transition(MigrationState.ROLLED_BACK, run_dir)
                else:
                    # No snapshot was taken yet; rolling back locks only
                    self._transition(MigrationState.ROLLED_BACK, run_dir)
                    rollback_success = True
            except Exception as rb_err:
                self._transition(MigrationState.CRITICAL_FAILURE, run_dir)
                raise RollbackError(
                    f"CRITICAL_FAILURE: Falha no rollback da migração {migration_id}: {rb_err}"
                ) from rb_err

            if isinstance(exec_err, (ProductionWriteDisabled, StalePlanError, PlayerOnlineError)):
                raise exec_err

            return {
                "status": MigrationState.ROLLED_BACK.value if rollback_success else MigrationState.FAILED.value,
                "migration_id": migration_id,
                "error": str(exec_err),
                "history": self.state_history,
            }

        finally:
            # Release physical locks on completion or clean rollback
            if locks_acquired and self.state != MigrationState.CRITICAL_FAILURE:
                self.lock_mgr.release(identity)

    def _apply_mutations(self, plan: MigrationPlan, manifest: SnapshotManifest) -> None:
        if manifest.status != "VALID":
            raise ApplyExecutionError(f"Snapshot não está VALID (status={manifest.status}).")
        first_file_done = False
        first_sql_done = False

        # Apply File Mutations
        for f_entry in manifest.file_entries:
            src_rel = f_entry.source_path_logical
            tgt_rel = f_entry.target_path_logical
            if not src_rel or not tgt_rel:
                continue

            src_full = self.server_root / src_rel
            tgt_full = self.server_root / tgt_rel
            if not src_full.is_file():
                continue

            tgt_full.parent.mkdir(parents=True, exist_ok=True)
            # Stage write via temp file + atomic replace
            tmp_tgt = tgt_full.with_suffix(tgt_full.suffix + ".tmp_migration")
            shutil.copy2(src_full, tmp_tgt)
            # Flush and sync
            with open(tmp_tgt, "rb") as f:
                os.fsync(f.fileno())

            os.replace(tmp_tgt, tgt_full)
            src_full.unlink()

            if not first_file_done:
                first_file_done = True
                if self.inject_failure_at == "after_first_file":
                    raise FailureInjectionTriggered("Falha injetada: after_first_file")

        # Apply SQLite Mutations
        for db_entry in manifest.database_entries:
            db_full = self.server_root / db_entry.db_rel
            if not db_full.is_file():
                continue

            with sqlite3.connect(str(db_full), timeout=10.0) as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for row_info in db_entry.rows:
                        pk_val = row_info["pk_value"]
                        uuid_col = row_info["uuid_column"]
                        planned_after = row_info["planned_after"]

                        if db_entry.adapter == "marriagemaster":
                            conn.execute(
                                f"UPDATE {db_entry.table} SET player1 = ?, player2 = ? WHERE {db_entry.pk_column} = ?",
                                (planned_after.get("player1"), planned_after.get("player2"), pk_val),
                            )
                        else:
                            tgt_val = planned_after.get(uuid_col)
                            conn.execute(
                                f"UPDATE {db_entry.table} SET {uuid_col} = ? WHERE {db_entry.pk_column} = ?",
                                (tgt_val, pk_val),
                            )

                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

            if not first_sql_done:
                first_sql_done = True
                if self.inject_failure_at == "after_first_sql":
                    raise FailureInjectionTriggered("Falha injetada: after_first_sql")

    def _verify_applied(self, plan: MigrationPlan, manifest: SnapshotManifest) -> None:
        """Verify that all target artifacts exist and source artifacts are gone."""
        for f in manifest.file_entries:
            if f.source_existed and f.source_path_logical:
                src_path = self.server_root / f.source_path_logical
                if src_path.is_file():
                    raise VerificationError(f"Arquivo de origem ainda existe: {f.source_path_logical}")

            if f.target_path_logical:
                tgt_path = self.server_root / f.target_path_logical
                if not tgt_path.is_file():
                    raise VerificationError(f"Arquivo de destino não foi criado: {f.target_path_logical}")
                if tgt_path.stat().st_size == 0 and f.source_size > 0:
                    raise VerificationError(f"Arquivo de destino está vazio: {f.target_path_logical}")

        for d in manifest.database_entries:
            db_path = self.server_root / d.db_rel
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                for row_info in d.rows:
                    pk_val = row_info["pk_value"]
                    uuid_col = row_info["uuid_column"]
                    planned_after = row_info["planned_after"]

                    if d.adapter == "marriagemaster":
                        cur = conn.execute(
                            f"SELECT player1, player2 FROM {d.table} WHERE {d.pk_column} = ?",
                            (pk_val,),
                        ).fetchone()
                        if not cur or (cur[0] != planned_after.get("player1") or cur[1] != planned_after.get("player2")):
                            raise VerificationError(f"Registro SQLite não confere em {d.table} (PK {pk_val})")
                    else:
                        search_pk = planned_after.get(d.pk_column) if d.pk_column == uuid_col else pk_val
                        cur = conn.execute(
                            f"SELECT {uuid_col} FROM {d.table} WHERE {d.pk_column} = ?",
                            (search_pk,),
                        ).fetchone()
                        if not cur or cur[0] != planned_after.get(uuid_col):
                            raise VerificationError(f"Registro SQLite não confere em {d.table} (PK {pk_val})")

    def _verify_rolled_back(self, plan: MigrationPlan, manifest: SnapshotManifest) -> None:
        """Verify that source artifacts are restored and target artifacts created are removed."""
        for f in manifest.file_entries:
            if f.source_existed and f.source_path_logical:
                src_path = self.server_root / f.source_path_logical
                if not src_path.is_file():
                    raise VerificationError(f"Rollback falhou: arquivo fonte não foi restaurado: {f.source_path_logical}")

            if not f.target_existed and f.target_path_logical:
                tgt_path = self.server_root / f.target_path_logical
                if tgt_path.is_file():
                    raise VerificationError(f"Rollback falhou: arquivo alvo criado ainda existe: {f.target_path_logical}")

        for d in manifest.database_entries:
            db_path = self.server_root / d.db_rel
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                for row_info in d.rows:
                    pk_val = row_info["pk_value"]
                    uuid_col = row_info["uuid_column"]
                    before = row_info["before"]

                    if d.adapter == "marriagemaster":
                        cur = conn.execute(
                            f"SELECT player1, player2 FROM {d.table} WHERE {d.pk_column} = ?",
                            (pk_val,),
                        ).fetchone()
                        if not cur or (cur[0] != before.get("player1") or cur[1] != before.get("player2")):
                            raise VerificationError(f"Rollback falhou: registro SQLite não retornou ao valor original em {d.table}")
                    else:
                        cur = conn.execute(
                            f"SELECT {uuid_col} FROM {d.table} WHERE {d.pk_column} = ?",
                            (pk_val,),
                        ).fetchone()
                        if not cur or cur[0] != before.get(uuid_col):
                            raise VerificationError(f"Rollback falhou: registro SQLite não retornou ao valor original em {d.table}")
