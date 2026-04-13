-- Phase 4 additive migration: execution observability tables.
-- This migration is intentionally additive and does not alter existing tables.

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS eod_reports (
    report_date TEXT NOT NULL,
    profile TEXT NOT NULL,
    start_equity REAL NOT NULL,
    end_equity REAL NOT NULL,
    pnl REAL NOT NULL,
    return_pct REAL NOT NULL,
    max_drawdown_pct REAL NOT NULL,
    trade_count INTEGER NOT NULL,
    turnover_buy_notional REAL NOT NULL,
    turnover_sell_notional REAL NOT NULL,
    turnover_total_notional REAL NOT NULL,
    turnover_ratio REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    generated_at TEXT NOT NULL,
    PRIMARY KEY (report_date, profile)
);

CREATE TABLE IF NOT EXISTS turnover_monitor_daily (
    report_date TEXT NOT NULL,
    profile TEXT NOT NULL,
    turnover_buy_notional REAL NOT NULL,
    turnover_sell_notional REAL NOT NULL,
    turnover_total_notional REAL NOT NULL,
    turnover_ratio REAL NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY (report_date, profile)
);

CREATE INDEX IF NOT EXISTS idx_eod_reports_profile_date
    ON eod_reports(profile, report_date);

CREATE INDEX IF NOT EXISTS idx_turnover_monitor_profile_date
    ON turnover_monitor_daily(profile, report_date);

COMMIT;
