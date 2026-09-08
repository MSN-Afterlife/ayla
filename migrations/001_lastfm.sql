CREATE TABLE IF NOT EXISTS lastfm_accounts (
    discord_user_id INTEGER PRIMARY KEY,
    lastfm_username TEXT NOT NULL,
    lastfm_session_key TEXT NOT NULL,
    scrobble_enabled INTEGER NOT NULL DEFAULT 1,
    scrobble_mode TEXT NOT NULL DEFAULT 'requested',
    linked_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS lastfm_auth_states (
    state_hash TEXT PRIMARY KEY,
    discord_user_id INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER
);
