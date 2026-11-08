CREATE TABLE IF NOT EXISTS auth_events (
    id INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    username TEXT,
    country TEXT NOT NULL DEFAULT 'Unknown',
    status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
    reason TEXT,
    is_invalid_user INTEGER NOT NULL DEFAULT 0 CHECK (is_invalid_user IN (0, 1)),
    source_file TEXT NOT NULL,
    line_number INTEGER NOT NULL,
    CONSTRAINT auth_events_source_line_unique UNIQUE (source_file, line_number)
);

CREATE INDEX IF NOT EXISTS auth_events_status_country_idx
    ON auth_events (status, country);
CREATE INDEX IF NOT EXISTS auth_events_status_username_idx
    ON auth_events (status, username);
CREATE INDEX IF NOT EXISTS auth_events_invalid_user_idx
    ON auth_events (is_invalid_user)
    WHERE is_invalid_user = 1;
