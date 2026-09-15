"""Versioned, platform-independent identity for legacy economy snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping

CONTENT_HASH_ALGORITHM = "AYLA_LEGACY_CONTENT_HASH_V1"
SCHEMA_FINGERPRINT_ALGORITHM = "AYLA_LEGACY_SCHEMA_FINGERPRINT_V1"
CONTENT_FIELDS = ("user_id", "balance", "daily_streak", "last_daily_at", "updated_at")
TABLE_NAME = "economy_profiles"


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite float is not canonical")
        return {"__type__": "float", "value": format(value, ".17g")}
    return {"__type__": type(value).__name__, "value": str(value)}


def canonicalize_legacy_profiles(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return explicit-field records sorted numerically by user_id.

    Valid bridge records use JSON integers and NULL directly. Out-of-domain
    values are represented with an explicit type envelope so diagnostics are
    still deterministic without relying on repr().
    """
    result = []
    for record in records:
        result.append({field: _json_value(record[field]) for field in CONTENT_FIELDS})
    result.sort(key=lambda record: (0, record["user_id"]) if isinstance(record["user_id"], int) and not isinstance(record["user_id"], bool) else (1, json.dumps(record["user_id"], ensure_ascii=False, separators=(",", ":"))))
    return result


def canonical_legacy_content_payload(records: Iterable[Mapping[str, Any]]) -> bytes:
    payload = canonicalize_legacy_profiles(records)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def legacy_content_hash(records: Iterable[Mapping[str, Any]]) -> str:
    return hashlib.sha256(canonical_legacy_content_payload(records)).hexdigest()


def _normalize_declared_type(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().upper())


def _normalize_default(value: Any) -> Any:
    if value is None:
        return None
    return re.sub(r"\s+", " ", str(value).strip()).upper()


def canonicalize_legacy_schema(columns: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(columns, key=lambda column: int(column["cid"]))
    return {
        "table": TABLE_NAME,
        "columns": [
            {
                "name": str(column["name"]),
                "type": _normalize_declared_type(column.get("type")),
                "notnull": bool(column.get("notnull")),
                "default": _normalize_default(column.get("default")),
                "pk": int(column.get("pk", 0)),
            }
            for column in ordered
        ],
    }


def canonical_legacy_schema_payload(columns: Iterable[Mapping[str, Any]]) -> bytes:
    return json.dumps(canonicalize_legacy_schema(columns), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def legacy_schema_fingerprint(columns: Iterable[Mapping[str, Any]]) -> str:
    return hashlib.sha256(canonical_legacy_schema_payload(columns)).hexdigest()
