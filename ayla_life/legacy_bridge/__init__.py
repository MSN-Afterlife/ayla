"""Read-only preparation tools for bridging legacy economy data to Ayla Life."""

from .genesis import (
    GenesisDryRunReport,
    GenesisDryRunner,
    GenesisManifest,
    GenesisPlan,
    LegacyGenesisExecutor,
    LegacyGenesisPlanner,
    WinkCurrencySpec,
)
from .canonical import (
    CONTENT_HASH_ALGORITHM,
    SCHEMA_FINGERPRINT_ALGORITHM,
    canonical_legacy_content_payload,
    canonicalize_legacy_profiles,
    canonicalize_legacy_schema,
    legacy_content_hash,
    legacy_schema_fingerprint,
)
from .models import (
    LegacyProfile,
    LegacySnapshot,
    LegacyValidationReport,
    ProfileIssue,
    ValidationSeverity,
)
from .reader import LegacyEconomyReader, LegacyReadError, LegacySchemaError

__all__ = [
    "GenesisDryRunReport", "GenesisDryRunner", "GenesisManifest", "GenesisPlan",
    "LegacyGenesisExecutor", "LegacyGenesisPlanner", "WinkCurrencySpec",
    "LegacyProfile", "LegacySnapshot", "LegacyValidationReport", "ProfileIssue",
    "ValidationSeverity", "LegacyEconomyReader", "LegacyReadError", "LegacySchemaError",
    "CONTENT_HASH_ALGORITHM", "SCHEMA_FINGERPRINT_ALGORITHM", "canonical_legacy_content_payload",
    "canonicalize_legacy_profiles", "canonicalize_legacy_schema", "legacy_content_hash",
    "legacy_schema_fingerprint",
]
