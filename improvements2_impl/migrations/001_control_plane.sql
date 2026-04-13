-- 001_control_plane.sql
-- Additive schema extension for improvements2 control plane.
-- This migration is intentionally non-destructive and keeps compatibility
-- with existing base tables (state_kv/events).

BEGIN;

CREATE TABLE IF NOT EXISTS state_kv (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL,
    sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_cycles (
    cycle_id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    cycle_type TEXT NOT NULL,
    profile TEXT,
    target_json TEXT NOT NULL DEFAULT '{}',
    effective_target_json TEXT NOT NULL DEFAULT '{}',
    decision_hash TEXT,
    severity TEXT NOT NULL DEFAULT 'ok',
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS locks (
    lock_id INTEGER PRIMARY KEY AUTOINCREMENT,
    lock_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    subject TEXT,
    state TEXT NOT NULL CHECK(state IN ('active', 'cleared', 'expired')),
    reason TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    start_ts TEXT NOT NULL,
    expiry_ts TEXT,
    cleared_ts TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_locks_lookup
    ON locks(lock_type, scope, subject, state);

CREATE UNIQUE INDEX IF NOT EXISTS uq_locks_active
    ON locks(lock_type, scope, IFNULL(subject, '__GLOBAL__'))
    WHERE state = 'active';

CREATE TABLE IF NOT EXISTS drift_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    cycle_id TEXT,
    symbol TEXT NOT NULL,
    expected_qty REAL NOT NULL,
    broker_qty REAL NOT NULL,
    qty_drift REAL NOT NULL,
    unexpected_open_orders INTEGER NOT NULL,
    severity TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0,
    detail_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_drift_cycle
    ON drift_snapshots(cycle_id, ts);

CREATE TABLE IF NOT EXISTS open_order_state (
    order_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    state TEXT NOT NULL,
    created_ts TEXT,
    updated_ts TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_open_order_symbol
    ON open_order_state(symbol, state);

CREATE TABLE IF NOT EXISTS risk_state (
    ts TEXT PRIMARY KEY,
    equity REAL NOT NULL,
    peak_equity REAL NOT NULL,
    drawdown_pct REAL NOT NULL,
    exposure_scalar REAL NOT NULL,
    dd_brake_state TEXT NOT NULL,
    recovery_phase TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS session_state (
    session_date TEXT PRIMARY KEY,
    session_pnl REAL NOT NULL DEFAULT 0.0,
    breaker_state TEXT NOT NULL DEFAULT 'none',
    reentry_blocked_json TEXT NOT NULL DEFAULT '[]',
    detail_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_reasons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    symbol TEXT,
    reason_code TEXT NOT NULL,
    priority_class TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    ts TEXT NOT NULL,
    FOREIGN KEY(cycle_id) REFERENCES decision_cycles(cycle_id)
);

CREATE INDEX IF NOT EXISTS idx_decision_reasons_cycle
    ON decision_reasons(cycle_id, ts);

CREATE TABLE IF NOT EXISTS shadow_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    variant_name TEXT NOT NULL,
    effective_target_json TEXT NOT NULL DEFAULT '{}',
    hypothetical_actions_json TEXT NOT NULL DEFAULT '[]',
    diff_json TEXT NOT NULL DEFAULT '{}',
    ts TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shadow_cycles_cycle
    ON shadow_cycles(cycle_id, ts);

COMMIT;

