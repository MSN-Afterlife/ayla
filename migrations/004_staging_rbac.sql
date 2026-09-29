CREATE TABLE IF NOT EXISTS staging_authorized_roles (
    guild_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    created_by INTEGER NOT NULL,
    PRIMARY KEY (guild_id, role_id)
);

CREATE TABLE IF NOT EXISTS staging_rbac_audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    operator_user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    role_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_staging_authorized_roles_guild
    ON staging_authorized_roles(guild_id);
CREATE INDEX IF NOT EXISTS idx_staging_rbac_audit_guild
    ON staging_rbac_audit_events(guild_id, timestamp);
