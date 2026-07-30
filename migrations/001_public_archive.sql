CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY,
    year INTEGER NOT NULL UNIQUE CHECK(year BETWEEN 2020 AND 2100),
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'final')),
    final_snapshot_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    run_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    snapshot_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('receiving', 'ready', 'failed', 'final')),
    payload_sha256 TEXT,
    competition_count INTEGER NOT NULL DEFAULT 0,
    application_count INTEGER NOT NULL DEFAULT 0,
    applicant_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(campaign_id, run_id, source_key)
);

CREATE TABLE IF NOT EXISTS campaign_stats (
    campaign_id INTEGER PRIMARY KEY REFERENCES campaigns(id) ON DELETE CASCADE,
    snapshot_id INTEGER REFERENCES snapshots(id),
    competition_count INTEGER NOT NULL DEFAULT 0,
    application_count INTEGER NOT NULL DEFAULT 0,
    applicant_count INTEGER NOT NULL DEFAULT 0,
    institution_count INTEGER NOT NULL DEFAULT 0,
    program_count INTEGER NOT NULL DEFAULT 0,
    institutions_json TEXT NOT NULL DEFAULT '[]',
    programs_json TEXT NOT NULL DEFAULT '[]',
    freshness_at TEXT,
    computed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS competition_snapshot_metrics (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    competition_id INTEGER NOT NULL REFERENCES competitions_core(id) ON DELETE CASCADE,
    seats INTEGER,
    row_count INTEGER NOT NULL DEFAULT 0,
    consent_count INTEGER NOT NULL DEFAULT 0,
    min_passing_ordinal INTEGER,
    PRIMARY KEY(snapshot_id, competition_id)
);

CREATE TABLE IF NOT EXISTS auth_users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'manager' CHECK(role IN ('admin', 'manager')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    csrf_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    user_agent_hash TEXT,
    ip_prefix_hash TEXT
);

CREATE TABLE IF NOT EXISTS admin_audit (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES auth_users(id),
    event_type TEXT NOT NULL,
    target_type TEXT,
    target_key TEXT,
    outcome TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_campaign_time
    ON snapshots(campaign_id, snapshot_at DESC);
CREATE INDEX IF NOT EXISTS idx_competition_snapshot_metrics_competition
    ON competition_snapshot_metrics(competition_id, snapshot_id);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_expiry
    ON auth_sessions(user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_admin_audit_created
    ON admin_audit(created_at DESC);
