CREATE TABLE IF NOT EXISTS minecraft_migration_audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    discord_user_id TEXT,
    canonical_uuid TEXT,
    migration_id TEXT,
    presence TEXT,
    lock_state TEXT,
    result TEXT,
    error TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_migration_audit_events_discord 
    ON minecraft_migration_audit_events(discord_user_id);
CREATE INDEX IF NOT EXISTS idx_migration_audit_events_canonical 
    ON minecraft_migration_audit_events(canonical_uuid);
CREATE INDEX IF NOT EXISTS idx_migration_audit_events_migration 
    ON minecraft_migration_audit_events(migration_id);
