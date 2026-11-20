CREATE TABLE IF NOT EXISTS moderation_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    duration_seconds INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS moderation_guild_config (
    guild_id INTEGER PRIMARY KEY,
    log_channel_id INTEGER,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_moderation_cases_guild_user
    ON moderation_cases(guild_id, user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_moderation_cases_guild_action_active
    ON moderation_cases(guild_id, action, active);
