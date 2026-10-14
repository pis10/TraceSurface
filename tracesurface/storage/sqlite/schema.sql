
CREATE TABLE IF NOT EXISTS scans (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    target_url    TEXT NOT NULL,
    domain        TEXT NOT NULL,
    started_at    INTEGER NOT NULL,
    finished_at   INTEGER,
    wait_ms       INTEGER,
    js_count      INTEGER,
    ast_total     INTEGER,
    status        TEXT NOT NULL,
    route_count   INTEGER DEFAULT 0,
    visited_route_count INTEGER DEFAULT 0,
    productive_route_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_scans_domain ON scans(domain);
CREATE INDEX IF NOT EXISTS idx_scans_target_url ON scans(target_url);

CREATE TABLE IF NOT EXISTS api_sinks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id     INTEGER NOT NULL REFERENCES scans(id),
    method      TEXT,
    ast_path    TEXT,
    source_js   TEXT,
    line        INTEGER,
    col_start   INTEGER,
    pattern     TEXT,
    params_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_sinks_scan ON api_sinks(scan_id);

CREATE TABLE IF NOT EXISTS api_resolutions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    sink_id             INTEGER NOT NULL REFERENCES api_sinks(id),
    scan_id             INTEGER NOT NULL REFERENCES scans(id),
    full_url            TEXT NOT NULL,
    grade               TEXT NOT NULL,
    base_source         TEXT,
    binding_rule        TEXT,
    why_not_higher_tier TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_resolutions_scan ON api_resolutions(scan_id);
CREATE INDEX IF NOT EXISTS idx_api_resolutions_sink ON api_resolutions(sink_id);
CREATE INDEX IF NOT EXISTS idx_api_resolutions_grade ON api_resolutions(grade);

CREATE TABLE IF NOT EXISTS resolution_evidence (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    resolution_id INTEGER NOT NULL REFERENCES api_resolutions(id),
    evidence_kind TEXT NOT NULL,
    evidence_id   INTEGER NOT NULL,
    role          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resolution_evidence_res ON resolution_evidence(resolution_id);

CREATE TABLE IF NOT EXISTS cdp_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id       INTEGER NOT NULL REFERENCES scans(id),
    method        TEXT NOT NULL,
    request_url   TEXT NOT NULL,
    request_path  TEXT NOT NULL,
    query_string  TEXT,
    post_data     TEXT,
    content_type  TEXT,
    frames_json   TEXT NOT NULL,
    request_headers  TEXT,
    response_status  INTEGER,
    response_headers TEXT,
    response_body    TEXT,
    response_file    TEXT,
    response_size    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cdp_requests_scan ON cdp_requests(scan_id);
CREATE INDEX IF NOT EXISTS idx_cdp_requests_url ON cdp_requests(request_url);

CREATE TABLE IF NOT EXISTS verifications (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    resolution_id  INTEGER REFERENCES api_resolutions(id),
    cdp_request_id INTEGER REFERENCES cdp_requests(id),
    scan_id        INTEGER NOT NULL REFERENCES scans(id),
    domain         TEXT NOT NULL,
    variant        TEXT,
    sent_url       TEXT NOT NULL,
    sent_method    TEXT NOT NULL,
    sent_query     TEXT,
    sent_body      TEXT,
    sent_headers   TEXT,
    status         INTEGER,
    resp_headers   TEXT,
    resp_ct        TEXT,
    resp_len       INTEGER,
    resp_snippet   TEXT,
    resp_file      TEXT,
    time_ms        INTEGER,
    error          TEXT,
    created_at     INTEGER NOT NULL,
    grade          TEXT,
    base_source    TEXT,
    binding_rule   TEXT,
    why_not_higher_tier TEXT
);
CREATE INDEX IF NOT EXISTS idx_verifications_resolution ON verifications(resolution_id);
CREATE INDEX IF NOT EXISTS idx_verifications_cdp ON verifications(cdp_request_id);
CREATE INDEX IF NOT EXISTS idx_verifications_domain ON verifications(domain);
CREATE INDEX IF NOT EXISTS idx_verifications_status ON verifications(status);
CREATE INDEX IF NOT EXISTS idx_verifications_scan ON verifications(scan_id);
CREATE INDEX IF NOT EXISTS idx_verifications_grade ON verifications(grade);
CREATE INDEX IF NOT EXISTS idx_verifications_dedup ON verifications(sent_method, sent_url);

CREATE TABLE IF NOT EXISTS secrets (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id        INTEGER NOT NULL REFERENCES scans(id),
    rule_id        TEXT NOT NULL,
    rule_group     TEXT NOT NULL,
    sensitive      INTEGER NOT NULL DEFAULT 0,
    value          TEXT NOT NULL,
    source_js      TEXT NOT NULL,
    line           INTEGER NOT NULL,
    col_start      INTEGER NOT NULL,
    context_before TEXT,
    context_line   TEXT,
    context_after  TEXT,
    metadata_json  TEXT
);
CREATE INDEX IF NOT EXISTS idx_secrets_scan ON secrets(scan_id);
CREATE INDEX IF NOT EXISTS idx_secrets_group ON secrets(rule_group);
CREATE INDEX IF NOT EXISTS idx_secrets_rule ON secrets(rule_id);

CREATE TABLE IF NOT EXISTS ai_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    settings_json TEXT NOT NULL,
    prompt_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES ai_batches(id) ON DELETE CASCADE,
    scan_id INTEGER REFERENCES scans(id) ON DELETE SET NULL,
    target_url TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','done','failed','skipped')),
    message TEXT NOT NULL DEFAULT '',
    input_count INTEGER NOT NULL DEFAULT 0,
    truncated_count INTEGER NOT NULL DEFAULT 0,
    suspected_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    started_at INTEGER,
    finished_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ai_jobs_batch ON ai_jobs(batch_id);
CREATE INDEX IF NOT EXISTS idx_ai_jobs_scan ON ai_jobs(scan_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_active_scan ON ai_jobs(scan_id)
    WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS ai_results (
    job_id INTEGER NOT NULL REFERENCES ai_jobs(id) ON DELETE CASCADE,
    replay_id INTEGER NOT NULL REFERENCES verifications(id) ON DELETE CASCADE,
    suspected INTEGER NOT NULL CHECK(suspected IN (0,1)),
    reason TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    truncated INTEGER NOT NULL,
    PRIMARY KEY(job_id, replay_id)
);
CREATE INDEX IF NOT EXISTS idx_ai_results_suspected ON ai_results(suspected, job_id);
PRAGMA user_version = 2;
