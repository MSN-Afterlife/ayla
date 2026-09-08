CREATE TABLE IF NOT EXISTS minecraft_identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id TEXT NOT NULL UNIQUE,
    canonical_uuid TEXT NOT NULL UNIQUE,
    canonical_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    discord_name_original TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
);

CREATE TABLE IF NOT EXISTS minecraft_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id INTEGER NOT NULL REFERENCES minecraft_identities(id) ON DELETE CASCADE,
    platform TEXT NOT NULL CHECK (platform IN ('java', 'bedrock')),
    external_id TEXT NOT NULL,
    current_username TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (platform, external_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS minecraft_accounts_one_java_per_identity
    ON minecraft_accounts(identity_id) WHERE platform = 'java';

CREATE TABLE IF NOT EXISTS minecraft_link_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL CHECK (platform IN ('java', 'bedrock')),
    external_id TEXT NOT NULL,
    username TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    requested_ip TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS minecraft_link_codes_lookup
    ON minecraft_link_codes(platform, external_id, expires_at);

CREATE TABLE IF NOT EXISTS minecraft_audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    discord_user_id TEXT,
    platform TEXT,
    external_id TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL
);
