from __future__ import annotations

import secrets
import time
from typing import Any

from .domain import MigrationInspection, MigrationPlan, now_iso


class GenericPlanner:
    def __init__(self, ttl_seconds: int = 900) -> None:
        self.ttl_seconds = ttl_seconds

    def create_plan(self, inspection: MigrationInspection) -> MigrationPlan:
        identity = inspection.identity
        changes: list[dict[str, Any]] = []
        files_count = 0
        db_records_count = 0

        for f in inspection.findings:
            if not f.source_found:
                continue

            if f.adapter.startswith("vanilla_") or f.adapter in ("auraskills", "quests"):
                files_count += f.source_records
                changes.append({
                    "adapter": f.adapter,
                    "action": "MOVE_FILE",
                    "source": f.details.get("source_path") or f.details.get("file"),
                    "target": f.details.get("target_path") or f.details.get("target_file"),
                    "records": f.source_records,
                    "strategy": f.migration_strategy,
                })
            elif f.adapter == "multiverse_inventories":
                files_count += f.source_records
                changes.append({
                    "adapter": f.adapter,
                    "action": "RENAME_MVI_JSON",
                    "source_files": f.details.get("source_files", []),
                    "records": f.source_records,
                    "strategy": f.migration_strategy,
                })
            elif f.adapter in ("huskhomes", "elitemobs", "waypoints", "simplepets", "marriagemaster", "ultimateteams"):
                db_records_count += f.source_records
                changes.append({
                    "adapter": f.adapter,
                    "action": "UPDATE_SQL_ROWS",
                    "database": f.details.get("database"),
                    "records": f.source_records,
                    "strategy": f.migration_strategy,
                })
            elif f.adapter == "imageframe":
                files_count += 1 if f.details.get("player_file") else 0
                db_records_count += f.details.get("maps_creator_count", 0)
                changes.append({
                    "adapter": f.adapter,
                    "action": "UPDATE_IMAGEFRAME",
                    "player_file": f.details.get("player_file"),
                    "maps_count": f.details.get("maps_creator_count", 0),
                    "strategy": f.migration_strategy,
                })
            elif f.adapter == "luckperms":
                changes.append({
                    "adapter": f.adapter,
                    "action": "DEFERRED_CONSOLE_OR_OFFLINE",
                    "records": f.source_records,
                    "strategy": f.migration_strategy,
                    "note": "Requer comando Bukkit 'lp user clone' ou janela de manutenção offline.",
                })
            elif f.adapter == "simplelogin":
                changes.append({
                    "adapter": f.adapter,
                    "action": "PRESERVED_NO_OP",
                    "records": f.source_records,
                    "strategy": f.migration_strategy,
                    "note": "Credenciais originais preservadas intactas.",
                })

        # Determine overall status
        if inspection.blockers:
            status = "BLOCKED"
        elif not identity.legacy_uuid or not identity.canonical_uuid:
            status = "INCOMPLETE"
        else:
            status = "READY"

        now = time.time()
        nonce = secrets.token_urlsafe(16)
        plan_id = f"plan-{inspection.inspection_fingerprint[:8]}-{secrets.token_hex(4)}"

        summary = {
            "files_count": files_count,
            "db_records_count": db_records_count,
            "warnings_count": len(inspection.warnings),
            "blockers_count": len(inspection.blockers),
        }

        return MigrationPlan(
            plan_id=plan_id,
            created_at=now_iso(),
            identity=identity,
            inspection_fingerprint=inspection.inspection_fingerprint,
            status=status,
            planned_changes=changes,
            warnings=inspection.warnings,
            blockers=inspection.blockers,
            summary=summary,
            approval_nonce=nonce,
            nonce_expires_at=now + self.ttl_seconds,
        )
