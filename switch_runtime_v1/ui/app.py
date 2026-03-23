#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import base64
import io
import difflib
import zipfile
import smtplib
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

NY_TZ = "America/New_York"
APP_TITLE = "Automated Trading Dashboard"
APP_SUBTITLE = "Operator console for switch-runtime strategy execution, risk controls, and broker-facing decision flow"


@dataclass(frozen=True)
class AuthConfig:
    username: str
    password_hash: str
    allow_plain_env_password: bool
    allow_registration: bool
    users_db_path: str
    oauth_enabled: bool
    oauth_provider: str
    oauth_client_id: str
    oauth_client_secret: str
    oauth_auth_url: str
    oauth_token_url: str
    oauth_userinfo_url: str
    oauth_scopes: str
    oauth_redirect_uri: str
    oauth_issuer: str
    oauth_allowed_domain: str
    admin_users: tuple[str, ...]
    session_timeout_min: int
    totp_enabled: bool
    totp_secret: str


ROLE_VIEWER = "viewer"
ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"
VALID_ROLES = (ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN)
TIME_WINDOW_OPTIONS = ("1D", "3D", "1W", "2W", "1M", "3M", "All")
TIME_WINDOW_DELTAS: dict[str, pd.Timedelta] = {
    "1D": pd.Timedelta(days=1),
    "3D": pd.Timedelta(days=3),
    "1W": pd.Timedelta(weeks=1),
    "2W": pd.Timedelta(weeks=2),
    "1M": pd.Timedelta(days=30),
    "3M": pd.Timedelta(days=90),
}
STREAM_TRANSPORT_OPTIONS = ("Polling", "SSE (experimental)")
USER_UI_PREF_KEYS = (
    "ui_watchlist_pins",
    "ui_watchlist_pinned_only",
    "ui_time_window",
    "ui_sync_charts",
    "ui_symbol_view_mode",
    "ui_symbol_chart_type",
    "ui_auto_refresh_enabled",
    "ui_auto_refresh_interval_sec",
    "ui_strict_user_db",
    "ui_global_filters_enabled",
    "ui_global_time_window",
    "ui_global_filter_query",
    "ui_global_symbols",
    "ui_global_event_types",
    "ui_global_variants",
    "ui_global_sides",
    "ui_global_order_types",
    "ui_saved_workspace",
    "ui_live_stream_enabled",
    "ui_live_stream_transport",
    "ui_live_stream_interval_sec",
    "ui_live_stream_sse_url",
    "ui_mobile_compact_mode",
    "ui_alert_rules",
)


def _set_auth_notice(kind: str, message: str) -> None:
    st.session_state["auth_notice_kind"] = str(kind or "").strip().lower()
    st.session_state["auth_notice_text"] = str(message or "").strip()


def _session_cookie_name() -> str:
    name = str(os.getenv("SWITCH_UI_SESSION_COOKIE_NAME", "switch_ui_session") or "").strip()
    return name or "switch_ui_session"


def _session_cookie_secure() -> bool:
    return str(os.getenv("SWITCH_UI_SESSION_COOKIE_SECURE", "0")).strip() == "1"


def _session_ttl_days() -> int:
    raw = str(os.getenv("SWITCH_UI_PERSIST_DAYS", "7")).strip()
    try:
        value = int(raw)
    except Exception:
        value = 7
    return min(30, max(1, value))


def _hash_session_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _cookie_escape_js(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


def _queue_set_session_cookie(token: str, max_age_sec: int) -> None:
    st.session_state["auth_cookie_set_token"] = str(token or "")
    st.session_state["auth_cookie_set_max_age"] = int(max(0, max_age_sec))
    st.session_state["auth_cookie_clear"] = False


def _queue_clear_session_cookie() -> None:
    st.session_state["auth_cookie_clear"] = True
    st.session_state["auth_cookie_set_token"] = ""
    st.session_state["auth_cookie_set_max_age"] = 0


def _flush_cookie_ops() -> None:
    cookie_name = _cookie_escape_js(_session_cookie_name())
    secure_attr = "; secure" if _session_cookie_secure() else ""
    token = str(st.session_state.get("auth_cookie_set_token", "") or "").strip()
    if token:
        max_age = int(st.session_state.get("auth_cookie_set_max_age", 0) or 0)
        token_js = _cookie_escape_js(token)
        components.html(
            f"""
            <script>
              (function() {{
                var v = encodeURIComponent('{token_js}');
                document.cookie = '{cookie_name}=' + v + '; path=/; max-age={max_age}; samesite=Lax{secure_attr}';
              }})();
            </script>
            """,
            height=0,
            width=0,
        )
        st.session_state["auth_cookie_set_token"] = ""
        st.session_state["auth_cookie_set_max_age"] = 0
    if bool(st.session_state.get("auth_cookie_clear", False)):
        components.html(
            f"""
            <script>
              (function() {{
                document.cookie = '{cookie_name}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT; max-age=0; samesite=Lax{secure_attr}';
              }})();
            </script>
            """,
            height=0,
            width=0,
        )
        st.session_state["auth_cookie_clear"] = False


def _clear_auth_notice() -> None:
    st.session_state["auth_notice_kind"] = ""
    st.session_state["auth_notice_text"] = ""


def _render_auth_notice() -> None:
    kind = str(st.session_state.get("auth_notice_kind", "") or "").strip().lower()
    text = str(st.session_state.get("auth_notice_text", "") or "").strip()
    if not kind or not text:
        return
    if kind == "success":
        st.success(text)
    elif kind == "warning":
        st.warning(text)
    elif kind == "info":
        st.info(text)
    else:
        st.error(text)


def _missing_oauth_fields(cfg: AuthConfig) -> list[str]:
    missing: list[str] = []
    required = [
        ("SWITCH_UI_OAUTH_CLIENT_ID", cfg.oauth_client_id),
        ("SWITCH_UI_OAUTH_AUTH_URL", cfg.oauth_auth_url),
        ("SWITCH_UI_OAUTH_TOKEN_URL", cfg.oauth_token_url),
        ("SWITCH_UI_OAUTH_USERINFO_URL", cfg.oauth_userinfo_url),
        ("SWITCH_UI_OAUTH_REDIRECT_URI", cfg.oauth_redirect_uri),
    ]
    for env_name, value in required:
        if not str(value or "").strip():
            missing.append(env_name)
    return missing


def _normalize_role(role: str) -> str:
    candidate = str(role or "").strip().lower()
    if candidate in VALID_ROLES:
        return candidate
    return ROLE_OPERATOR


def _role_rank(role: str) -> int:
    role_n = _normalize_role(role)
    if role_n == ROLE_ADMIN:
        return 3
    if role_n == ROLE_OPERATOR:
        return 2
    return 1


def _role_at_least(role: str, required: str) -> bool:
    return _role_rank(role) >= _role_rank(required)


def _verify_totp(code: str, base32_secret: str, period_seconds: int = 30, digits: int = 6, drift_steps: int = 1) -> bool:
    token = str(code or "").strip()
    if not token.isdigit() or len(token) != digits:
        return False
    secret_raw = str(base32_secret or "").strip().replace(" ", "").upper()
    if not secret_raw:
        return False
    try:
        key = base64.b32decode(secret_raw, casefold=True)
    except Exception:
        return False
    now_step = int(datetime.now(tz=timezone.utc).timestamp()) // period_seconds
    for offset in range(-drift_steps, drift_steps + 1):
        counter = now_step + offset
        msg = int(counter).to_bytes(8, byteorder="big", signed=False)
        digest = hmac.new(key, msg, hashlib.sha1).digest()
        off = digest[-1] & 0x0F
        dbc = int.from_bytes(digest[off : off + 4], "big") & 0x7FFFFFFF
        otp = str(dbc % (10**digits)).zfill(digits)
        if hmac.compare_digest(otp, token):
            return True
    return False


def _prefs_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "switch_runtime_v1" / "ui" / "preferences.json"


def _load_prefs() -> dict[str, Any]:
    path = _prefs_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_prefs(payload: dict[str, Any]) -> tuple[bool, str]:
    path = _prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return True, "Preferences saved."
    except Exception as exc:
        return False, f"Failed to save preferences: {exc}"


def _ui_audit_log_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "switch_runtime_v1" / "ui" / "ui_command_audit.jsonl"


def _append_ui_audit(action: str, meta: dict[str, Any] | None = None) -> None:
    payload = {
        "ts_ny": pd.Timestamp.now(tz=NY_TZ).isoformat(),
        "user": str(st.session_state.get("auth_user", "") or ""),
        "role": str(st.session_state.get("auth_role_cached", "") or ""),
        "action": str(action or "").strip(),
        "meta": (meta or {}),
    }
    try:
        path = _ui_audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except Exception:
        # Audit logging should never break UI flow.
        pass


def _load_ui_audit(limit: int = 2000) -> pd.DataFrame:
    path = _ui_audit_log_path()
    if not path.exists():
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
        if not rows:
            return pd.DataFrame()
        out = pd.DataFrame(rows).tail(int(limit)).copy()
        out["ts_ny"] = pd.to_datetime(out.get("ts_ny"), errors="coerce")
        out = out.sort_values("ts_ny", ascending=False).reset_index(drop=True)
        return out
    except Exception:
        return pd.DataFrame()


def _user_slug(username: str) -> str:
    raw = str(username or "").strip().lower()
    if not raw:
        return "anonymous"
    out = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "_" for ch in raw)
    out = out.strip("._-")
    return out or "anonymous"


def _user_runtime_db_path(username: str) -> Path:
    root = Path(__file__).resolve().parents[2]
    slug = _user_slug(username)
    return root / "switch_runtime_v1" / "runtime_data" / "users" / slug / "switch_runtime_v1_runtime.db"


def _ensure_runtime_db_min_schema(db_path: str) -> tuple[bool, str]:
    target = Path(str(db_path or "").strip())
    if not str(target):
        return False, "DB path is empty."
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(target))
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS state_kv (
                    key TEXT PRIMARY KEY,
                    value_json TEXT,
                    updated_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT,
                    event_type TEXT,
                    payload_json TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type)")
            conn.commit()
        finally:
            conn.close()
        return True, "Runtime DB schema ready."
    except Exception as exc:
        return False, f"Runtime DB schema bootstrap failed: {exc}"


def _db_owner_from_path(db_path: str) -> str:
    p = Path(str(db_path or "").strip())
    parts = list(p.parts)
    try:
        idx = parts.index("users")
        if idx + 1 < len(parts):
            return str(parts[idx + 1]).strip()
    except ValueError:
        pass
    return ""


def _runtime_command_lines(
    *,
    mode: str,
    state_db: str,
    execute_orders: bool,
    strategy_profile: str = "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m",
) -> list[str]:
    lines = [
        "/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \\",
        "  /home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py \\",
        "  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \\",
        "  --env-override \\",
        f"  --mode {str(mode).strip()} \\",
        f"  --strategy-profile {strategy_profile} \\",
        "  --data-feed sip \\",
        "  --eval-time 15:55 \\",
        "  --profit-lock-order-type market_order \\",
        "  --rebalance-order-type market \\",
        f"  --state-db {state_db}" + (" \\" if execute_orders else ""),
    ]
    if execute_orders:
        lines.append("  --execute-orders")
    return lines


def _account_profiles_from_env() -> list[dict[str, Any]]:
    raw = str(os.getenv("SWITCH_UI_ACCOUNTS_JSON", "") or "").strip()
    if not raw:
        return []
    try:
        obj = json.loads(raw)
    except Exception:
        return []
    if not isinstance(obj, list):
        return []
    out: list[dict[str, Any]] = []
    for row in obj:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "mode": str(row.get("mode", "paper") or "paper").strip().lower(),
                "data_feed": str(row.get("data_feed", "sip") or "sip").strip().lower(),
                "strategy_profile": str(row.get("strategy_profile", "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m") or "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m").strip(),
                "state_db": str(row.get("state_db", "") or "").strip(),
            }
        )
    return out


def _sanitize_user_ui_prefs(ui: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    raw_pins = ui.get("ui_watchlist_pins", [])
    if isinstance(raw_pins, list):
        pins = [str(s).strip().upper() for s in raw_pins if str(s).strip()]
        out["ui_watchlist_pins"] = sorted(set(pins))
    out["ui_watchlist_pinned_only"] = bool(ui.get("ui_watchlist_pinned_only", False))
    tw = str(ui.get("ui_time_window", "1D") or "1D")
    out["ui_time_window"] = tw if tw in TIME_WINDOW_OPTIONS else "1D"
    out["ui_sync_charts"] = bool(ui.get("ui_sync_charts", True))
    view = str(ui.get("ui_symbol_view_mode", "Intraday Activity") or "Intraday Activity")
    out["ui_symbol_view_mode"] = view if view in {"Intraday Activity", "Lifecycle", "Event Mix"} else "Intraday Activity"
    chart_type = str(ui.get("ui_symbol_chart_type", "Line") or "Line")
    out["ui_symbol_chart_type"] = chart_type if chart_type in {"Line", "Bar", "Candles"} else "Line"
    out["ui_auto_refresh_enabled"] = bool(ui.get("ui_auto_refresh_enabled", False))
    interval = int(ui.get("ui_auto_refresh_interval_sec", 30) or 30)
    out["ui_auto_refresh_interval_sec"] = interval if interval in {5, 15, 30, 60, 120} else 30
    out["ui_strict_user_db"] = bool(ui.get("ui_strict_user_db", True))
    out["ui_global_filters_enabled"] = bool(ui.get("ui_global_filters_enabled", False))
    gtw = str(ui.get("ui_global_time_window", "1M") or "1M")
    out["ui_global_time_window"] = gtw if gtw in TIME_WINDOW_OPTIONS else "1M"
    out["ui_global_filter_query"] = str(ui.get("ui_global_filter_query", "") or "")
    for key in ["ui_global_symbols", "ui_global_event_types", "ui_global_variants", "ui_global_sides", "ui_global_order_types"]:
        raw = ui.get(key, [])
        out[key] = list(raw) if isinstance(raw, list) else []
    out["ui_saved_workspace"] = str(ui.get("ui_saved_workspace", "Tradeboard") or "Tradeboard")
    out["ui_live_stream_enabled"] = bool(ui.get("ui_live_stream_enabled", False))
    tr = str(ui.get("ui_live_stream_transport", "Polling") or "Polling")
    out["ui_live_stream_transport"] = tr if tr in STREAM_TRANSPORT_OPTIONS else "Polling"
    live_interval = int(pd.to_numeric(ui.get("ui_live_stream_interval_sec", 5), errors="coerce") or 5)
    out["ui_live_stream_interval_sec"] = max(1, min(60, live_interval))
    out["ui_live_stream_sse_url"] = str(ui.get("ui_live_stream_sse_url", "") or "")
    out["ui_mobile_compact_mode"] = bool(ui.get("ui_mobile_compact_mode", False))
    raw_rules = ui.get("ui_alert_rules", [])
    out["ui_alert_rules"] = raw_rules if isinstance(raw_rules, list) else []
    return out


def _load_user_ui_prefs_once(current_user: str, user_prefs: dict[str, Any]) -> None:
    user = str(current_user or "").strip()
    if not user:
        return
    loaded_for = str(st.session_state.get("ui_prefs_loaded_for_user", "") or "").strip()
    if loaded_for == user:
        return
    ui = user_prefs.get("ui_state", {}) if isinstance(user_prefs, dict) else {}
    if isinstance(ui, dict) and ui:
        clean = _sanitize_user_ui_prefs(ui)
        for k, v in clean.items():
            st.session_state[k] = v
    st.session_state["ui_prefs_loaded_for_user"] = user


def _password_strength(password: str) -> tuple[int, str, list[str]]:
    pwd = str(password or "")
    checks: list[tuple[bool, str]] = [
        (len(pwd) >= 8, "At least 8 characters"),
        (any(ch.islower() for ch in pwd), "Contains lowercase"),
        (any(ch.isupper() for ch in pwd), "Contains uppercase"),
        (any(ch.isdigit() for ch in pwd), "Contains number"),
        (any(not ch.isalnum() for ch in pwd), "Contains special character"),
    ]
    score = sum(1 for ok, _ in checks if ok)
    if score <= 1:
        label = "Very Weak"
    elif score == 2:
        label = "Weak"
    elif score == 3:
        label = "Moderate"
    elif score == 4:
        label = "Strong"
    else:
        label = "Very Strong"
    details = [f"{'OK' if ok else 'NO'} - {text}" for ok, text in checks]
    return score, label, details


def _inject_theme_css() -> None:
    st.markdown(
        """
        <style>
          @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

          :root {
            --bg-0: #0b0d10;
            --bg-1: #111418;
            --bg-2: #171b20;
            --card: rgba(18, 22, 27, 0.9);
            --card-strong: rgba(14, 18, 22, 0.96);
            --text: #f4f7fa;
            --muted: #9aa3ad;
            --accent: #00c805;
            --accent-2: #3cd95b;
            --accent-3: #99ec5b;
            --ok: #00c805;
            --warn: #f3b13c;
            --err: #ff5d7a;
            --border: rgba(138, 149, 161, 0.24);
            --shadow: 0 20px 42px rgba(0, 0, 0, 0.44);
            --shadow-soft: 0 10px 24px rgba(0, 0, 0, 0.34);
          }

          .stApp {
            background:
              radial-gradient(840px 420px at 100% -10%, rgba(0, 200, 5, 0.16), transparent 63%),
              radial-gradient(760px 390px at 0% 0%, rgba(88, 101, 115, 0.17), transparent 62%),
              linear-gradient(180deg, var(--bg-0) 0%, var(--bg-1) 45%, var(--bg-2) 100%);
            color: var(--text);
            font-family: "Manrope", "Segoe UI", sans-serif;
          }
          [data-testid="stAppViewContainer"] > .main {
            padding-top: 0.2rem;
          }
          [data-testid="stAppViewContainer"] .main .block-container {
            padding: 35px !important;
          }
          .stMainBlockContainer.block-container {
            padding: 35px !important;
          }
          #MainMenu, header, footer {
            display: none !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
          }

          [data-testid="stSidebar"] {
            background: linear-gradient(180deg, rgba(12, 15, 19, 0.98), rgba(16, 20, 25, 0.98));
            border-right: 1px solid rgba(138, 149, 161, 0.26);
          }
          [data-testid="stSidebar"] * {
            color: #e2e8f0 !important;
          }
          [data-testid="stSidebar"] .stButton button {
            background: linear-gradient(90deg, rgba(0,200,5,0.95), rgba(82,219,102,0.9));
            color: #031204 !important;
            border: 1px solid rgba(159, 179, 213, 0.35);
            border-radius: 12px;
            font-weight: 700;
          }
          [data-testid="stSidebar"] .stTextInput input,
          [data-testid="stSidebar"] .stSelectbox div[data-baseweb="select"] > div {
            background: rgba(20, 25, 30, 0.9) !important;
            border: 1px solid rgba(138, 149, 161, 0.34) !important;
            border-radius: 10px !important;
          }

          h1, h2, h3, h4 {
            letter-spacing: -0.02em;
          }
          [data-testid="stCaptionContainer"] p, .stCaption {
            color: #c5cfdb !important;
            line-height: 1.35;
            font-weight: 500;
          }
          .stTextInput input {
            min-height: 2.7rem;
            font-size: 1rem !important;
            border-radius: 12px !important;
          }
          .stButton button {
            min-height: 2.7rem;
            border-radius: 12px;
            font-weight: 700;
          }
          .login-wrap .auth-panel [data-testid="stCaptionContainer"] {
            margin-top: 0.18rem;
            margin-bottom: 0.14rem;
          }
          .login-wrap .auth-panel [data-testid="stAlert"] {
            margin-top: 0.22rem;
            margin-bottom: 0.38rem;
          }
          .login-wrap .auth-panel [data-testid="stRadio"] {
            margin-top: 0.25rem;
            margin-bottom: 0.28rem;
          }
          .login-wrap .auth-panel [data-testid="stTextInput"] {
            margin-top: 0.12rem;
            margin-bottom: 0.1rem;
          }
          .login-wrap .auth-panel [data-testid="stButton"] {
            margin-top: 0.38rem;
          }
          .login-wrap .auth-panel .stProgress {
            margin-top: 0.25rem;
            margin-bottom: 0.15rem;
          }
          .login-wrap .auth-panel .auth-divider {
            margin: 0.58rem 0 0.5rem 0;
          }
          .admin-compact [data-testid="stMetric"] {
            margin-bottom: 0.1rem;
          }
          .admin-compact [data-testid="stSelectbox"],
          .admin-compact [data-testid="stTextInput"] {
            margin-bottom: 0.14rem;
          }
          .admin-compact .stCodeBlock {
            margin-top: 0.2rem;
          }
          [data-testid="stMetricValue"] {
            font-size: 1.35rem !important;
          }

          .top-banner {
            background:
              radial-gradient(420px 190px at 95% 0%, rgba(255,255,255,0.08), transparent 70%),
              linear-gradient(112deg, rgba(27, 33, 40, 0.9) 0%, rgba(18, 23, 28, 0.96) 45%, rgba(14, 18, 22, 0.98) 100%);
            color: white;
            border-radius: 20px;
            padding: 1.2rem 1.35rem;
            box-shadow: var(--shadow);
            border: 1px solid rgba(138, 149, 161, 0.24);
            margin-bottom: 1.05rem;
            backdrop-filter: blur(8px);
          }
          .hero-grid {
            display: grid;
            grid-template-columns: 1.3fr 1fr;
            gap: 0.95rem;
            align-items: center;
          }
          .hero-left h2 {
            margin: 0.15rem 0 0.1rem 0;
            font-size: 1.75rem;
            letter-spacing: -0.03em;
          }
          .hero-right {
            background: rgba(5, 10, 20, 0.38);
            border: 1px solid rgba(159, 179, 213, 0.26);
            border-radius: 14px;
            padding: 0.6rem 0.75rem;
          }
          .hero-right-title {
            font-size: 0.74rem;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            opacity: 0.9;
            margin-bottom: 0.45rem;
          }
          .hero-right-row {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.35rem 0.55rem;
          }
          .hero-right-k {
            color: #bbd1f7;
            font-size: 0.74rem;
          }
          .hero-right-v {
            color: #ffffff;
            font-size: 0.86rem;
            font-weight: 700;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }

          .hero-meta {
            margin-top: 0.65rem;
            display: flex;
            gap: 0.5rem;
            flex-wrap: wrap;
          }

          .meta-chip {
            display: inline-block;
            border-radius: 999px;
            padding: 0.18rem 0.62rem;
            font-size: 0.78rem;
            font-weight: 600;
            border: 1px solid rgba(159, 179, 213, 0.35);
            background: rgba(12, 19, 33, 0.45);
          }
          .mode-chip {
            background: linear-gradient(90deg, rgba(0,200,5,0.22), rgba(82,219,102,0.18));
            border-color: rgba(120, 198, 130, 0.44);
          }

          .kpi-card {
            background: var(--card);
            border: 1px solid rgba(107, 134, 180, 0.3);
            box-shadow: var(--shadow-soft);
            border-radius: 16px;
            padding: 0.78rem 0.95rem;
            backdrop-filter: blur(6px);
          }

          .kpi-label {
            color: var(--muted);
            font-size: 0.78rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.15rem;
          }

          .kpi-value {
            color: var(--text);
            font-size: 1.34rem;
            font-weight: 700;
            line-height: 1.2;
          }

          .kpi-sub {
            color: var(--muted);
            font-size: 0.77rem;
            margin-top: 0.16rem;
          }
          .event-tape {
            margin-top: 0.72rem;
            border: 1px solid rgba(107, 134, 180, 0.3);
            background: rgba(6, 11, 21, 0.62);
            border-radius: 12px;
            padding: 0.42rem 0.58rem;
            white-space: nowrap;
            overflow: hidden;
            font-family: "IBM Plex Mono", monospace;
            font-size: 0.76rem;
            color: #d5e3fc;
          }
          .event-tape b {
            color: #7fd6ff;
          }
          .status-bar {
            background: rgba(7, 12, 23, 0.72);
            border: 1px solid rgba(107, 134, 180, 0.28);
            border-radius: 14px;
            box-shadow: var(--shadow-soft);
            padding: 0.5rem 0.65rem;
            display: flex;
            gap: 0.45rem;
            flex-wrap: wrap;
            margin-bottom: 0.9rem;
          }
          .status-badge {
            border-radius: 999px;
            padding: 0.17rem 0.58rem;
            font-size: 0.76rem;
            font-family: "IBM Plex Mono", monospace;
            border: 1px solid rgba(144, 171, 214, 0.36);
            background: rgba(12, 19, 33, 0.6);
            color: #dbe9ff;
          }
          .status-good {
            border-color: rgba(34, 197, 139, 0.5);
            color: #98f5d0;
          }
          .status-warn {
            border-color: rgba(245, 158, 11, 0.6);
            color: #ffd185;
          }
          .status-bad {
            border-color: rgba(239, 68, 68, 0.62);
            color: #ffabab;
          }
          .alert-card {
            border-radius: 12px;
            border: 1px solid rgba(107, 134, 180, 0.28);
            background: rgba(7, 12, 23, 0.66);
            padding: 0.55rem 0.7rem;
            margin-bottom: 0.42rem;
          }
          .alert-title {
            font-size: 0.82rem;
            font-weight: 700;
            margin-bottom: 0.14rem;
            color: #f0f6ff;
          }
          .alert-text {
            font-size: 0.8rem;
            color: #b9cce8;
          }
          .terminal-shell {
            background: rgba(16, 20, 24, 0.86);
            border: 1px solid rgba(122, 132, 142, 0.12);
            border-radius: 14px;
            box-shadow: 0 10px 18px rgba(0, 0, 0, 0.22);
            padding: 0.65rem 0.75rem 0.4rem 0.75rem;
            margin-bottom: 0.75rem;
          }
          .terminal-shell-hero {
            background: rgba(14, 18, 22, 0.95);
            border: 1px solid rgba(122, 132, 142, 0.1);
            border-radius: 16px;
            box-shadow: 0 14px 24px rgba(0, 0, 0, 0.25);
            padding: 0.8rem 0.9rem 0.6rem 0.9rem;
            margin-bottom: 0.75rem;
          }
          .watch-item {
            border: 1px solid rgba(122, 132, 142, 0.14);
            background: rgba(14, 18, 22, 0.9);
            border-radius: 10px;
            padding: 0.34rem 0.48rem;
            margin-bottom: 0.34rem;
          }
          .watch-item-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-family: "IBM Plex Mono", monospace;
            font-size: 0.77rem;
            color: #e6f0ff;
            font-weight: 600;
          }
          .watch-item-sub {
            margin-top: 0.14rem;
            color: #9fb3d5;
            font-size: 0.72rem;
            font-family: "IBM Plex Mono", monospace;
          }
          .terminal-title {
            font-size: 0.83rem;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: #9fb3d5;
            margin-bottom: 0.38rem;
            font-weight: 700;
          }
          .terminal-stat {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.28rem 0.5rem;
            margin-bottom: 0.1rem;
          }
          .terminal-stat-k {
            color: #9fb3d5;
            font-size: 0.75rem;
          }
          .terminal-stat-v {
            color: #e8f1ff;
            font-size: 0.79rem;
            text-align: right;
            font-family: "IBM Plex Mono", monospace;
          }
          .rh-hero-symbol {
            font-size: 1.5rem;
            font-weight: 800;
            letter-spacing: -0.02em;
            color: #f6fbff;
          }
          .rh-hero-sub {
            color: #a1acb8;
            font-size: 0.84rem;
            margin-top: 0.05rem;
            margin-bottom: 0.45rem;
          }
          .rh-stats-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 0.45rem;
            margin-bottom: 0.5rem;
          }
          .rh-stat {
            border: 1px solid rgba(122, 132, 142, 0.16);
            border-radius: 12px;
            background: rgba(14, 18, 22, 0.9);
            padding: 0.48rem 0.56rem;
          }
          .rh-stat-k {
            color: #8f9aa7;
            font-size: 0.72rem;
            margin-bottom: 0.1rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
          }
          .rh-stat-v {
            color: #f3f8ff;
            font-size: 1.04rem;
            font-weight: 700;
            font-family: "IBM Plex Mono", monospace;
          }
          .rh-action-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.38rem;
          }
          .rh-action-chip {
            border: 1px solid rgba(0, 200, 5, 0.28);
            background: rgba(0, 200, 5, 0.08);
            color: #9ff3a2;
            border-radius: 999px;
            padding: 0.16rem 0.55rem;
            font-size: 0.74rem;
            font-weight: 700;
            letter-spacing: 0.02em;
          }

          .auth-image-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.9rem;
            margin-bottom: 0.9rem;
          }
          .auth-image-tile {
            min-height: 260px;
            border-radius: 16px;
            border: 1px solid rgba(107, 134, 180, 0.35);
            position: relative;
            overflow: hidden;
            box-shadow: var(--shadow-soft);
            background-size: cover;
            background-position: center;
          }
          .auth-image-tile::before {
            content: "";
            position: absolute;
            inset: 0;
            background: linear-gradient(180deg, rgba(6,10,19,0.16), rgba(6,10,19,0.62) 80%, rgba(6,10,19,0.78));
          }
          .auth-image-tile span {
            position: absolute;
            left: 0.72rem;
            bottom: 0.58rem;
            font-size: 0.8rem;
            color: #e5efff;
            letter-spacing: 0.03em;
            font-weight: 600;
            z-index: 2;
          }
          .auth-image-left {
            background-image:
              radial-gradient(120% 90% at 100% 0%, rgba(0, 200, 5, 0.22), transparent 52%),
              linear-gradient(120deg, rgba(7, 12, 20, 0.62), rgba(7, 12, 20, 0.18)),
              url('https://images.pexels.com/photos/7567434/pexels-photo-7567434.jpeg?auto=compress&cs=tinysrgb&w=2200');
            background-position: center 42%;
            background-repeat: no-repeat;
            background-size: 108%;
          }
          .auth-image-right {
            background-image:
              linear-gradient(180deg, rgba(7, 13, 24, 0.08), rgba(7, 13, 24, 0.08)),
              url('https://images.pexels.com/photos/534216/pexels-photo-534216.jpeg?auto=compress&cs=tinysrgb&w=1800');
            background-position: center 45%;
            background-repeat: no-repeat;
          }
          .login-wrap {
            max-width: 1080px;
            margin: 0 auto 0 auto;
            position: relative;
            z-index: 1;
          }
          .desk-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 0.85rem;
          }
          .stat-chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin: 0.2rem 0 0.45rem 0;
          }
          .stat-chip {
            border: 1px solid rgba(107, 134, 180, 0.35);
            background: rgba(10, 16, 29, 0.72);
            color: #d7e5fb;
            border-radius: 999px;
            padding: 0.14rem 0.55rem;
            font-size: 0.77rem;
            font-family: "IBM Plex Mono", monospace;
          }

          .muted {
            color: var(--muted);
          }

          .ok-pill, .warn-pill, .err-pill {
            display: inline-block;
            border-radius: 999px;
            padding: 0.15rem 0.6rem;
            font-size: 0.8rem;
            font-weight: 600;
          }

          .ok-pill { background: rgba(6, 118, 71, 0.13); color: var(--ok); }
          .warn-pill { background: rgba(181, 71, 8, 0.12); color: var(--warn); }
          .err-pill { background: rgba(180, 35, 24, 0.12); color: var(--err); }

          div[data-testid="stMetric"] {
            background: var(--card);
            border: 1px solid rgba(107, 134, 180, 0.28);
            border-radius: 16px;
            padding: 0.45rem 0.65rem;
            box-shadow: var(--shadow-soft);
          }
          div[data-testid="stMetric"] label,
          div[data-testid="stMetric"] div {
            color: var(--text) !important;
          }
          div[data-testid="stMetricLabel"] {
            font-weight: 600;
            letter-spacing: 0.01em;
          }

          .stTabs [data-baseweb="tab-list"] {
            gap: 0.3rem;
            background: rgba(7, 12, 23, 0.8);
            border-radius: 11px;
            padding: 0.18rem;
            border: 1px solid rgba(107, 134, 180, 0.24);
          }
          .stTabs [data-baseweb="tab"] {
            border-radius: 9px;
            font-weight: 600;
            font-size: 0.82rem;
            padding: 0.28rem 0.66rem;
            color: var(--muted);
          }
          .stTabs [aria-selected="true"] {
            background: linear-gradient(90deg, rgba(0,200,5,0.22), rgba(82,219,102,0.15));
            border: 1px solid rgba(0,200,5,0.4);
            color: #eaf1ff;
          }
          .desk-block {
            background: rgba(16, 20, 24, 0.72);
            border: 1px solid rgba(122, 132, 142, 0.16);
            border-radius: 14px;
            padding: 0.7rem 0.8rem 0.5rem 0.8rem;
            box-shadow: var(--shadow-soft);
            margin-bottom: 0.8rem;
          }

          .stDataFrame, .stTable {
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid rgba(122, 132, 142, 0.14);
            box-shadow: 0 8px 16px rgba(0, 0, 0, 0.2);
            background: rgba(14, 18, 22, 0.85);
          }
          div[data-testid="stDataFrame"] [role="grid"] {
            font-family: "IBM Plex Mono", monospace;
            font-size: 0.79rem;
          }

          .section-caption {
            color: var(--muted);
            font-size: 0.88rem;
            margin-top: -0.25rem;
            margin-bottom: 0.5rem;
          }

          div[data-testid="stTextInput"] input,
          div[data-testid="stNumberInput"] input,
          div[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
          div[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
            background: rgba(18, 23, 28, 0.95) !important;
            color: #dbe8ff !important;
            border: 1px solid rgba(122, 132, 142, 0.3) !important;
            border-radius: 12px !important;
          }

          .stButton button {
            background: linear-gradient(90deg, rgba(0,200,5,0.95), rgba(82,219,102,0.95));
            color: #021204 !important;
            border: 1px solid rgba(159, 179, 213, 0.44);
            font-weight: 700;
            letter-spacing: 0.01em;
            border-radius: 12px;
          }

          code, .stCodeBlock pre, .stCode {
            font-family: "IBM Plex Mono", monospace !important;
          }
          @media (max-width: 1100px) {
            .hero-grid {
              grid-template-columns: 1fr;
            }
            .auth-image-row {
              grid-template-columns: 1fr;
            }
            .auth-image-tile {
              min-height: 190px;
            }
            .stTabs [data-baseweb="tab-list"] {
              overflow-x: auto;
              white-space: nowrap;
            }
            .status-bar {
              flex-wrap: wrap;
            }
            .rh-stats-grid {
              grid-template-columns: 1fr 1fr;
            }
          }
          @media (max-width: 760px) {
            .stMainBlockContainer, .block-container {
              padding-left: 0.6rem !important;
              padding-right: 0.6rem !important;
            }
            .desk-block {
              padding: 0.55rem 0.55rem 0.4rem 0.55rem;
              border-radius: 12px;
            }
            .rh-hero-symbol {
              font-size: 1.2rem !important;
            }
            .rh-stats-grid {
              grid-template-columns: 1fr;
            }
            .stButton button {
              min-height: 2.5rem;
            }
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _inject_compact_mode_css(enabled: bool) -> None:
    if not enabled:
        return
    st.markdown(
        """
        <style>
          .stMainBlockContainer.block-container,
          [data-testid="stAppViewContainer"] .main .block-container {
            padding-top: 0.55rem !important;
            padding-bottom: 0.7rem !important;
          }
          .desk-block {
            padding: 0.48rem 0.56rem 0.4rem 0.56rem !important;
          }
          .section-caption {
            font-size: 0.81rem !important;
            margin-bottom: 0.35rem !important;
          }
          div[data-testid="stMetric"] {
            padding: 0.35rem 0.5rem !important;
          }
          .stButton button {
            min-height: 2.35rem !important;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _build_auth_config() -> AuthConfig:
    username = os.getenv("SWITCH_UI_USERNAME", "admin").strip() or "admin"
    password_hash = os.getenv("SWITCH_UI_PASSWORD_HASH", "").strip()
    allow_plain = os.getenv("SWITCH_UI_ALLOW_PLAIN_PASSWORD", "0").strip() == "1"
    root = Path(__file__).resolve().parents[2]
    users_db = os.getenv("SWITCH_UI_USERS_DB", str(root / "switch_runtime_v1" / "ui" / "users.db")).strip()
    oauth_enabled = os.getenv("SWITCH_UI_OAUTH_ENABLED", "0").strip() == "1"
    admin_raw = os.getenv("SWITCH_UI_ADMIN_USERS", username).strip()
    admins = tuple(sorted({u.strip().lower() for u in admin_raw.split(",") if u.strip()}))
    timeout_raw = os.getenv("SWITCH_UI_SESSION_TIMEOUT_MIN", "45").strip()
    try:
        session_timeout_min = max(5, int(timeout_raw))
    except Exception:
        session_timeout_min = 45
    return AuthConfig(
        username=username,
        password_hash=password_hash,
        allow_plain_env_password=allow_plain,
        allow_registration=os.getenv("SWITCH_UI_ALLOW_REGISTRATION", "1").strip() == "1",
        users_db_path=users_db,
        oauth_enabled=oauth_enabled,
        oauth_provider=os.getenv("SWITCH_UI_OAUTH_PROVIDER", "oauth").strip() or "oauth",
        oauth_client_id=os.getenv("SWITCH_UI_OAUTH_CLIENT_ID", "").strip(),
        oauth_client_secret=os.getenv("SWITCH_UI_OAUTH_CLIENT_SECRET", "").strip(),
        oauth_auth_url=os.getenv("SWITCH_UI_OAUTH_AUTH_URL", "").strip(),
        oauth_token_url=os.getenv("SWITCH_UI_OAUTH_TOKEN_URL", "").strip(),
        oauth_userinfo_url=os.getenv("SWITCH_UI_OAUTH_USERINFO_URL", "").strip(),
        oauth_scopes=os.getenv("SWITCH_UI_OAUTH_SCOPES", "openid profile email").strip() or "openid profile email",
        oauth_redirect_uri=os.getenv("SWITCH_UI_OAUTH_REDIRECT_URI", "").strip(),
        oauth_issuer=os.getenv("SWITCH_UI_OAUTH_ISSUER", "").strip(),
        oauth_allowed_domain=os.getenv("SWITCH_UI_OAUTH_ALLOWED_DOMAIN", "").strip().lower(),
        admin_users=admins,
        session_timeout_min=session_timeout_min,
        totp_enabled=os.getenv("SWITCH_UI_TOTP_ENABLED", "0").strip() == "1",
        totp_secret=os.getenv("SWITCH_UI_TOTP_SECRET", "").strip(),
    )


def _pbkdf2_hex(password: str, salt: str, iterations: int) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return digest.hex()


def _verify_password_hash(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    # Format: pbkdf2_sha256$<iterations>$<salt>$<hex_digest>
    try:
        algo, iters, salt, digest = password_hash.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        candidate = _pbkdf2_hex(password, salt, int(iters))
        return hmac.compare_digest(candidate, digest)
    except Exception:
        return False


def _make_password_hash(password: str, iterations: int = 260000) -> str:
    salt = secrets.token_hex(16)
    digest = _pbkdf2_hex(password, salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def _verify_env_password(password: str, cfg: AuthConfig) -> bool:
    if cfg.password_hash and _verify_password_hash(password, cfg.password_hash):
        return True

    # Optional plain fallback for local-only quick starts.
    if cfg.allow_plain_env_password:
        plain = os.getenv("SWITCH_UI_PASSWORD", "")
        if plain:
            return hmac.compare_digest(password, plain)

    # Explicitly opt-in dev-only fallback. Never enabled by default.
    dev_fallback_enabled = os.getenv("SWITCH_UI_DEV_INSECURE_FALLBACK", "0").strip() == "1"
    if dev_fallback_enabled:
        dev_value = os.getenv("SWITCH_UI_DEV_INSECURE_FALLBACK_PASSWORD", "change-me")
        return hmac.compare_digest(password, dev_value)

    return False


def _is_admin_username(cfg: AuthConfig, username: str) -> bool:
    return str(username or "").strip().lower() in set(cfg.admin_users)


def _ensure_users_column(conn: sqlite3.Connection, column_name: str, ddl_fragment: str) -> None:
    columns = {str(r[1]) for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE users ADD COLUMN {ddl_fragment}")


def _init_users_db(cfg: AuthConfig) -> None:
    db_path = Path(cfg.users_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                oauth_provider TEXT,
                oauth_sub TEXT,
                oauth_email TEXT,
                display_name TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                is_admin INTEGER NOT NULL DEFAULT 0,
                role TEXT NOT NULL DEFAULT 'operator',
                last_login_at TEXT
            )
            """
        )
        # Forward-compatible migration for existing DBs.
        _ensure_users_column(conn, "is_active", "is_active INTEGER NOT NULL DEFAULT 1")
        _ensure_users_column(conn, "is_admin", "is_admin INTEGER NOT NULL DEFAULT 0")
        _ensure_users_column(conn, "role", "role TEXT NOT NULL DEFAULT 'operator'")
        _ensure_users_column(conn, "last_login_at", "last_login_at TEXT")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_oauth_provider_sub
            ON users(oauth_provider, oauth_sub)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ui_sessions (
                token_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                auth_method TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ui_sessions_username ON ui_sessions(username)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ui_sessions_expires_at ON ui_sessions(expires_at)"
        )
        # Keep admin flags in sync with configured admin usernames.
        if cfg.admin_users:
            admin_set = {u.strip().lower() for u in cfg.admin_users if u.strip()}
            rows = conn.execute("SELECT username FROM users").fetchall()
            for (u,) in rows:
                uname = str(u or "").strip()
                if not uname:
                    continue
                should_admin = 1 if uname.lower() in admin_set else 0
                role = ROLE_ADMIN if should_admin == 1 else ROLE_OPERATOR
                conn.execute(
                    "UPDATE users SET is_admin = ?, role = COALESCE(role, ?), is_active = COALESCE(is_active, 1) WHERE username = ?",
                    (should_admin, role, uname),
                )
        # Ensure role field remains normalized across old records.
        rows2 = conn.execute("SELECT username, role, is_admin FROM users").fetchall()
        for uname, role, is_admin in rows2:
            name = str(uname or "").strip()
            if not name:
                continue
            role_n = _normalize_role(str(role or ""))
            if int(is_admin or 0) == 1:
                role_n = ROLE_ADMIN
            conn.execute("UPDATE users SET role = ? WHERE username = ?", (role_n, name))
        conn.commit()
    finally:
        conn.close()


def _create_persistent_session(cfg: AuthConfig, username: str, auth_method: str) -> str:
    _init_users_db(cfg)
    token = secrets.token_urlsafe(48)
    token_hash = _hash_session_token(token)
    now = datetime.now(tz=timezone.utc)
    expires = now + timedelta(days=_session_ttl_days())
    conn = sqlite3.connect(str(Path(cfg.users_db_path)))
    try:
        conn.execute("DELETE FROM ui_sessions WHERE expires_at < ?", (now.isoformat(),))
        conn.execute(
            """
            INSERT OR REPLACE INTO ui_sessions(
                token_hash, username, auth_method, created_at, last_seen_at, expires_at, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (
                token_hash,
                str(username or "").strip(),
                str(auth_method or "").strip() or "local",
                now.isoformat(),
                now.isoformat(),
                expires.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def _disable_persistent_session(cfg: AuthConfig, token: str) -> None:
    t = str(token or "").strip()
    if not t:
        return
    _init_users_db(cfg)
    token_hash = _hash_session_token(t)
    conn = sqlite3.connect(str(Path(cfg.users_db_path)))
    try:
        conn.execute("UPDATE ui_sessions SET is_active = 0 WHERE token_hash = ?", (token_hash,))
        conn.commit()
    finally:
        conn.close()


def _restore_auth_from_cookie(cfg: AuthConfig) -> bool:
    if bool(st.session_state.get("auth_ok", False)):
        return False
    try:
        cookies = dict(getattr(st.context, "cookies", {}) or {})
    except Exception:
        cookies = {}
    token = str(cookies.get(_session_cookie_name(), "") or "").strip()
    if not token:
        return False

    def _invalid() -> bool:
        _queue_clear_session_cookie()
        return False

    _init_users_db(cfg)
    now = datetime.now(tz=timezone.utc)
    token_hash = _hash_session_token(token)
    conn = sqlite3.connect(str(Path(cfg.users_db_path)))
    try:
        conn.execute("DELETE FROM ui_sessions WHERE expires_at < ?", (now.isoformat(),))
        row = conn.execute(
            """
            SELECT username, auth_method, expires_at, is_active
            FROM ui_sessions
            WHERE token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if not row:
            return _invalid()
        username = str(row[0] or "").strip()
        auth_method = str(row[1] or "local").strip() or "local"
        expires_at = str(row[2] or "").strip()
        is_active = int(row[3] or 0)
        if is_active != 1:
            return _invalid()
        if not username:
            return _invalid()
        if expires_at and pd.to_datetime(expires_at, errors="coerce", utc=True) < pd.Timestamp(now):
            conn.execute("UPDATE ui_sessions SET is_active = 0 WHERE token_hash = ?", (token_hash,))
            conn.commit()
            return _invalid()
        user_row = conn.execute(
            "SELECT is_active FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if not user_row or int(user_row[0] or 0) != 1:
            return _invalid()
        conn.execute(
            "UPDATE ui_sessions SET last_seen_at = ? WHERE token_hash = ?",
            (now.isoformat(), token_hash),
        )
        conn.execute(
            "UPDATE users SET last_login_at = ? WHERE username = ?",
            (now.isoformat(), username),
        )
        conn.commit()
    finally:
        conn.close()
    st.session_state.auth_ok = True
    st.session_state.auth_user = username
    st.session_state.auth_method = auth_method
    st.session_state.auth_session_token = token
    _queue_set_session_cookie(token, max_age_sec=_session_ttl_days() * 24 * 3600)
    return True


def _validate_username(username: str) -> tuple[bool, str]:
    user = str(username or "").strip()
    if len(user) < 3 or len(user) > 48:
        return False, "Username length must be between 3 and 48 characters."
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if any(ch not in allowed for ch in user):
        return False, "Username may only contain letters, numbers, dot, underscore, and hyphen."
    return True, ""


def _register_local_user(cfg: AuthConfig, username: str, password: str) -> tuple[bool, str]:
    ok, reason = _validate_username(username)
    if not ok:
        return False, reason
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    _init_users_db(cfg)
    db_path = Path(cfg.users_db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        created_at = datetime.now(tz=timezone.utc).isoformat()
        pwd_hash = _make_password_hash(password)
        is_admin = 1 if _is_admin_username(cfg, username) else 0
        role = ROLE_ADMIN if is_admin == 1 else ROLE_OPERATOR
        conn.execute(
            """
            INSERT INTO users(username, password_hash, created_at, is_active, is_admin, role)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (username.strip(), pwd_hash, created_at, is_admin, role),
        )
        conn.commit()
        return True, "Registration successful. Please sign in."
    except sqlite3.IntegrityError:
        return False, "Username already exists."
    finally:
        conn.close()


def _verify_local_user(cfg: AuthConfig, username: str, password: str) -> tuple[bool, str]:
    user = str(username or "").strip()
    if not user or not password:
        return False, "Invalid username or password."
    _init_users_db(cfg)
    conn = sqlite3.connect(str(Path(cfg.users_db_path)))
    try:
        row = conn.execute(
            "SELECT password_hash, is_active FROM users WHERE username = ?",
            (user,),
        ).fetchone()
        if not row:
            return False, "Invalid username or password."
        stored_hash = str(row[0] or "")
        is_active = int(row[1]) if row[1] is not None else 1
        if is_active != 1:
            return False, "Account is disabled. Contact an administrator."
        if not stored_hash or (not _verify_password_hash(password, stored_hash)):
            return False, "Invalid username or password."
        conn.execute(
            "UPDATE users SET last_login_at = ? WHERE username = ?",
            (datetime.now(tz=timezone.utc).isoformat(), user),
        )
        conn.commit()
        return True, ""
    finally:
        conn.close()


def _oauth_ready(cfg: AuthConfig) -> bool:
    if not cfg.oauth_enabled:
        return False
    needed = [
        cfg.oauth_client_id,
        cfg.oauth_auth_url,
        cfg.oauth_token_url,
        cfg.oauth_userinfo_url,
        cfg.oauth_redirect_uri,
    ]
    return all(bool(x) for x in needed)


def _pkce_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def _decode_jwt_payload_unverified(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        pad = "=" * ((4 - (len(payload) % 4)) % 4)
        decoded = base64.urlsafe_b64decode((payload + pad).encode("utf-8"))
        parsed = json.loads(decoded.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _oauth_auth_url(cfg: AuthConfig, state: str, nonce: str, code_challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": cfg.oauth_client_id,
        "redirect_uri": cfg.oauth_redirect_uri,
        "scope": cfg.oauth_scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{cfg.oauth_auth_url}?{urllib.parse.urlencode(params)}"


def _http_post_form_json(url: str, payload: dict[str, str]) -> dict[str, Any]:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


def _http_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


def _oauth_exchange_code(cfg: AuthConfig, code: str, code_verifier: str) -> dict[str, Any]:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg.oauth_redirect_uri,
        "client_id": cfg.oauth_client_id,
        "code_verifier": code_verifier,
    }
    if cfg.oauth_client_secret:
        payload["client_secret"] = cfg.oauth_client_secret
    return _http_post_form_json(cfg.oauth_token_url, payload)


def _oauth_fetch_userinfo(cfg: AuthConfig, access_token: str) -> dict[str, Any]:
    return _http_get_json(
        cfg.oauth_userinfo_url,
        headers={"Authorization": f"Bearer {access_token}"},
    )


def _oauth_user_login(cfg: AuthConfig, provider_sub: str, email: str, display_name: str) -> str:
    _init_users_db(cfg)
    db_path = Path(cfg.users_db_path)
    username_base = (email or display_name or f"{cfg.oauth_provider}_{provider_sub}").strip()
    username = username_base.replace(" ", "_")
    if "@" in username:
        username = username.split("@", 1)[0]
    ok, _ = _validate_username(username)
    if not ok:
        username = f"{cfg.oauth_provider}_{provider_sub[:12]}"
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT username, is_active FROM users WHERE oauth_provider = ? AND oauth_sub = ?",
            (cfg.oauth_provider, provider_sub),
        ).fetchone()
        if row:
            existing_username = str(row[0])
            is_active = int(row[1]) if row[1] is not None else 1
            if is_active != 1:
                raise RuntimeError("OAuth account is disabled. Contact an administrator.")
            conn.execute(
                """
                UPDATE users
                SET oauth_email = ?, display_name = ?, role = COALESCE(role, ?), last_login_at = ?
                WHERE username = ?
                """,
                (
                    email or None,
                    display_name or None,
                    ROLE_ADMIN if _is_admin_username(cfg, existing_username) else ROLE_OPERATOR,
                    datetime.now(tz=timezone.utc).isoformat(),
                    existing_username,
                ),
            )
            conn.commit()
            return existing_username

        final_username = username
        suffix = 1
        while conn.execute("SELECT 1 FROM users WHERE username = ?", (final_username,)).fetchone():
            suffix += 1
            final_username = f"{username}_{suffix}"
        created_at = datetime.now(tz=timezone.utc).isoformat()
        is_admin = 1 if _is_admin_username(cfg, final_username) else 0
        role = ROLE_ADMIN if is_admin == 1 else ROLE_OPERATOR
        conn.execute(
            """
            INSERT INTO users(
                username, password_hash, created_at, oauth_provider, oauth_sub, oauth_email, display_name, is_active, is_admin, role, last_login_at
            )
            VALUES (?, '', ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                final_username,
                created_at,
                cfg.oauth_provider,
                provider_sub,
                email or None,
                display_name or None,
                is_admin,
                role,
                created_at,
            ),
        )
        conn.commit()
        return final_username
    finally:
        conn.close()


def _is_admin_user(cfg: AuthConfig, username: str) -> bool:
    user = str(username or "").strip()
    if not user:
        return False
    if _is_admin_username(cfg, user):
        return True
    _init_users_db(cfg)
    conn = sqlite3.connect(str(Path(cfg.users_db_path)))
    try:
        row = conn.execute("SELECT is_admin, role FROM users WHERE username = ?", (user,)).fetchone()
    finally:
        conn.close()
    if not row:
        return False
    return int(row[0] or 0) == 1 or _normalize_role(str(row[1] or "")) == ROLE_ADMIN


def _get_user_role(cfg: AuthConfig, username: str) -> str:
    user = str(username or "").strip()
    if not user:
        return ROLE_VIEWER
    if _is_admin_username(cfg, user):
        return ROLE_ADMIN
    _init_users_db(cfg)
    conn = sqlite3.connect(str(Path(cfg.users_db_path)))
    try:
        row = conn.execute("SELECT role, is_admin FROM users WHERE username = ?", (user,)).fetchone()
    finally:
        conn.close()
    if not row:
        return ROLE_VIEWER
    if int(row[1] or 0) == 1:
        return ROLE_ADMIN
    return _normalize_role(str(row[0] or ""))


def _set_user_role(cfg: AuthConfig, target_user: str, role: str, actor: str) -> tuple[bool, str]:
    target = str(target_user or "").strip()
    actor_user = str(actor or "").strip()
    role_n = _normalize_role(role)
    if not target:
        return False, "No target user selected."
    if target == cfg.username and role_n != ROLE_ADMIN:
        return False, "Primary env admin must remain role=admin."
    if target == actor_user and role_n == ROLE_VIEWER:
        return False, "Cannot downgrade currently signed-in account to viewer."
    _init_users_db(cfg)
    conn = sqlite3.connect(str(Path(cfg.users_db_path)))
    try:
        row = conn.execute("SELECT username, is_active, is_admin FROM users WHERE username = ?", (target,)).fetchone()
        if not row:
            return False, "Target user does not exist."
        if role_n != ROLE_ADMIN and int(row[2] or 0) == 1:
            active_admins = int(conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1").fetchone()[0])
            if active_admins <= 1:
                return False, "Cannot remove admin role from the last active admin."
        is_admin = 1 if role_n == ROLE_ADMIN else 0
        conn.execute("UPDATE users SET role = ?, is_admin = ? WHERE username = ?", (role_n, is_admin, target))
        conn.commit()
        return True, f"User `{target}` role updated to `{role_n}`."
    finally:
        conn.close()


def _list_users(cfg: AuthConfig) -> pd.DataFrame:
    _init_users_db(cfg)
    conn = sqlite3.connect(str(Path(cfg.users_db_path)))
    try:
        df = pd.read_sql_query(
            """
            SELECT
                username,
                is_active,
                is_admin,
                role,
                oauth_provider,
                oauth_email,
                display_name,
                created_at,
                last_login_at
            FROM users
            ORDER BY username
            """,
            conn,
        )
    finally:
        conn.close()
    if df.empty:
        return df
    df["is_active"] = df["is_active"].map(lambda x: "yes" if int(x or 0) == 1 else "no")
    df["is_admin"] = df["is_admin"].map(lambda x: "yes" if int(x or 0) == 1 else "no")
    df["role"] = df["role"].map(lambda x: _normalize_role(str(x or "")))
    return df


def _set_user_active(cfg: AuthConfig, target_user: str, active: bool, actor: str) -> tuple[bool, str]:
    target = str(target_user or "").strip()
    actor_user = str(actor or "").strip()
    if not target:
        return False, "No target user selected."
    if target == actor_user:
        return False, "Cannot change active state of current signed-in account."
    _init_users_db(cfg)
    conn = sqlite3.connect(str(Path(cfg.users_db_path)))
    try:
        row = conn.execute("SELECT username, is_admin, is_active FROM users WHERE username = ?", (target,)).fetchone()
        if not row:
            return False, "Target user does not exist."
        is_admin = int(row[1]) if row[1] is not None else 0
        is_active = int(row[2]) if row[2] is not None else 1
        if (not active) and is_admin == 1 and is_active == 1:
            active_admins = int(conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1").fetchone()[0])
            if active_admins <= 1:
                return False, "Cannot disable the last active admin account."
        conn.execute("UPDATE users SET is_active = ? WHERE username = ?", (1 if active else 0, target))
        conn.commit()
        return True, f"User `{target}` {'enabled' if active else 'disabled'}."
    finally:
        conn.close()


def _delete_user(cfg: AuthConfig, target_user: str, actor: str) -> tuple[bool, str]:
    target = str(target_user or "").strip()
    actor_user = str(actor or "").strip()
    if not target:
        return False, "No target user selected."
    if target == actor_user:
        return False, "Cannot delete current signed-in account."
    if target == cfg.username:
        return False, "Cannot delete primary env admin account."
    _init_users_db(cfg)
    conn = sqlite3.connect(str(Path(cfg.users_db_path)))
    try:
        row = conn.execute("SELECT username, is_admin, is_active FROM users WHERE username = ?", (target,)).fetchone()
        if not row:
            return False, "Target user does not exist."
        is_admin = int(row[1]) if row[1] is not None else 0
        is_active = int(row[2]) if row[2] is not None else 1
        if is_admin == 1 and is_active == 1:
            active_admins = int(conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1").fetchone()[0])
            if active_admins <= 1:
                return False, "Cannot delete the last active admin account."
        conn.execute("DELETE FROM users WHERE username = ?", (target,))
        conn.commit()
        return True, f"User `{target}` deleted."
    finally:
        conn.close()


def _render_admin_sidebar(cfg: AuthConfig, current_user: str) -> None:
    if not _is_admin_user(cfg, current_user):
        return
    st.markdown("---")
    st.markdown("### Admin")
    st.markdown("<div class='admin-compact'>", unsafe_allow_html=True)
    users_df = _list_users(cfg)
    st.caption(f"User records: `{len(users_df)}`")
    if users_df.empty:
        st.info("No users yet.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    active_count = int((users_df["is_active"] == "yes").sum())
    admin_count = int((users_df["is_admin"] == "yes").sum())
    metric_cols = st.columns(2)
    with metric_cols[0]:
        st.metric("Active Users", active_count)
    with metric_cols[1]:
        st.metric("Admins", admin_count)
    with st.expander("User Directory", expanded=False):
        st.dataframe(users_df, use_container_width=True, hide_index=True, height=180)
    targets = users_df["username"].astype(str).tolist()
    target = st.selectbox("Manage User", options=targets, index=0, key="admin_target_user")
    selected_row = users_df[users_df["username"] == target]
    selected_role = ROLE_OPERATOR
    if not selected_row.empty:
        row = selected_row.iloc[0]
        selected_role = _normalize_role(str(row.get("role") or "operator"))
        st.caption(
            f"Target status: active=`{row['is_active']}` role=`{selected_role}` admin=`{row['is_admin']}` oauth=`{str(row.get('oauth_provider') or '-')}`"
        )
    action = st.selectbox(
        "Action",
        options=["enable", "disable", "delete", "set_role"],
        index=0,
        key="admin_action",
    )
    role_choice = st.selectbox("Role", options=list(VALID_ROLES), index=VALID_ROLES.index(selected_role), key="admin_role_choice")
    confirm_phrase = f"CONFIRM {action.upper()} {target}"
    st.caption(f"Type `{confirm_phrase}` and click Apply to execute admin action.")
    confirm_text = st.text_input("Confirmation", value="", key="admin_confirm_text", placeholder=confirm_phrase)
    confirm_ok = str(confirm_text).strip() == confirm_phrase
    if st.button("Apply", use_container_width=True, key="admin_apply_action", disabled=not confirm_ok):
        if not confirm_ok:
            st.error("Action blocked: confirmation text mismatch.")
        else:
            if action == "enable":
                ok, msg = _set_user_active(cfg, target_user=target, active=True, actor=current_user)
            elif action == "set_role":
                ok, msg = _set_user_role(cfg, target_user=target, role=role_choice, actor=current_user)
            elif action in {"disable", "delete"}:
                staged = {
                    "action": action,
                    "target": target,
                    "actor": current_user,
                    "created_at": pd.Timestamp.now(tz=NY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z"),
                }
                st.session_state["admin_pending_action"] = staged
                ok, msg = True, f"Staged `{action}` for `{target}`. Execute staged action below to finalize."
            else:
                ok, msg = False, "Unsupported action."
            (st.success if ok else st.error)(msg)
            ts_ny = pd.Timestamp.now(tz=NY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
            audit_line = f"[{ts_ny}] {current_user} -> {action} `{target}`: {msg}"
            st.session_state["admin_last_action"] = audit_line
            if hasattr(st, "toast"):
                st.toast(audit_line, icon=("✅" if ok else "⚠️"))
            st.rerun()
    pending = st.session_state.get("admin_pending_action", {})
    if isinstance(pending, dict) and pending.get("action") in {"disable", "delete"}:
        st.caption(
            f"Pending admin action: `{pending.get('action')}` on `{pending.get('target')}` staged at `{pending.get('created_at')}`."
        )
        if bool(st.session_state.get("admin_pending_final_ack_reset", False)):
            st.session_state["admin_pending_final_ack"] = False
            st.session_state["admin_pending_final_ack_reset"] = False
        exec_col1, exec_col2 = st.columns(2)
        with exec_col1:
            final_ack = st.checkbox("Final confirm staged action", key="admin_pending_final_ack")
            if st.button("Execute Staged Action", use_container_width=True, key="admin_execute_staged"):
                if not final_ack:
                    st.error("Final confirmation is required before staged execution.")
                else:
                    action_p = str(pending.get("action"))
                    target_p = str(pending.get("target"))
                    if action_p == "disable":
                        ok, msg = _set_user_active(cfg, target_user=target_p, active=False, actor=current_user)
                    else:
                        ok, msg = _delete_user(cfg, target_user=target_p, actor=current_user)
                    (st.success if ok else st.error)(msg)
                    ts_ny = pd.Timestamp.now(tz=NY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
                    st.session_state["admin_last_action"] = f"[{ts_ny}] {current_user} -> execute `{action_p}` `{target_p}`: {msg}"
                    st.session_state["admin_pending_action"] = {}
                    st.session_state["admin_pending_final_ack_reset"] = True
                    st.rerun()
        with exec_col2:
            if st.button("Cancel Staged Action", use_container_width=True, key="admin_cancel_staged"):
                st.session_state["admin_pending_action"] = {}
                st.session_state["admin_pending_final_ack_reset"] = True
                st.info("Staged action canceled.")
                st.rerun()
    st.caption("Safety: self-action blocked; primary env admin deletion blocked; last active admin disable/delete blocked.")
    last_admin_action = str(st.session_state.get("admin_last_action", "") or "").strip()
    if last_admin_action:
        st.caption("Last admin action")
        st.code(last_admin_action, language="text")
    st.markdown("</div>", unsafe_allow_html=True)


def _init_session_state() -> None:
    st.session_state.setdefault("auth_ok", False)
    st.session_state.setdefault("auth_user", "")
    st.session_state.setdefault("auth_method", "")
    st.session_state.setdefault("auth_view", "Sign In")
    st.session_state.setdefault("oauth_state", "")
    st.session_state.setdefault("oauth_nonce", "")
    st.session_state.setdefault("oauth_code_verifier", "")
    st.session_state.setdefault("auth_notice_kind", "")
    st.session_state.setdefault("auth_notice_text", "")
    st.session_state.setdefault("admin_last_action", "")
    st.session_state.setdefault("auth_view_requested", "")
    st.session_state.setdefault("admin_pending_final_ack_reset", False)
    st.session_state.setdefault("auth_session_token", "")
    st.session_state.setdefault("auth_cookie_set_token", "")
    st.session_state.setdefault("auth_cookie_set_max_age", 0)
    st.session_state.setdefault("auth_cookie_clear", False)
    st.session_state.setdefault("session_last_activity_ts", 0.0)
    st.session_state.setdefault("notice_actions", {})
    st.session_state.setdefault("admin_pending_action", {})
    st.session_state.setdefault("ui_auto_refresh_enabled", False)
    st.session_state.setdefault("ui_auto_refresh_interval_sec", 30)
    st.session_state.setdefault("ui_live_stream_enabled", False)
    st.session_state.setdefault("ui_live_stream_transport", "Polling")
    st.session_state.setdefault("ui_live_stream_interval_sec", 5)
    st.session_state.setdefault("ui_live_stream_sse_url", "")
    st.session_state.setdefault("ui_mobile_compact_mode", False)
    st.session_state.setdefault("ui_alert_rules", [])
    st.session_state.setdefault("ui_strict_user_db", True)
    st.session_state.setdefault("ui_watchlist_pins", [])
    st.session_state.setdefault("ui_watchlist_pinned_only", False)
    st.session_state.setdefault("ui_time_window", "1D")
    st.session_state.setdefault("ui_sync_charts", True)
    st.session_state.setdefault("ui_symbol_view_mode", "Intraday Activity")
    st.session_state.setdefault("ui_symbol_chart_type", "Line")
    st.session_state.setdefault("ui_command_palette_action", "Refresh Data Now")
    st.session_state.setdefault("ui_command_palette_search", "")
    st.session_state.setdefault("ui_palette_status", "")
    st.session_state.setdefault("ui_pending_auto_refresh_enabled", None)
    st.session_state.setdefault("ui_pending_symbol_view_mode", "")
    st.session_state.setdefault("ui_pending_symbol_chart_type", "")
    st.session_state.setdefault("ui_pending_selected_symbol", "")
    st.session_state.setdefault("ui_pending_time_window", "")
    st.session_state.setdefault("ui_layout_pending_load", {})
    st.session_state.setdefault("ui_pending_sidebar_custom_db", "")
    st.session_state.setdefault("ui_pending_sidebar_event_limit", None)
    st.session_state.setdefault("sidebar_custom_db", "")
    st.session_state.setdefault("sidebar_event_limit", 3000)
    st.session_state.setdefault("ui_prefs_loaded_for_user", "")
    st.session_state.setdefault("sidebar_loaded_for_user", "")
    st.session_state.setdefault("ui_global_filters_enabled", False)
    st.session_state.setdefault("ui_global_time_window", "1M")
    st.session_state.setdefault("ui_global_filter_query", "")
    st.session_state.setdefault("ui_global_symbols", [])
    st.session_state.setdefault("ui_global_event_types", [])
    st.session_state.setdefault("ui_global_variants", [])
    st.session_state.setdefault("ui_global_sides", [])
    st.session_state.setdefault("ui_global_order_types", [])
    st.session_state.setdefault("ui_saved_workspace", "Tradeboard")
    st.session_state.setdefault("ui_custom_alert_stale_enabled", False)
    st.session_state.setdefault("ui_custom_alert_stale_min", 10)
    st.session_state.setdefault("ui_custom_alert_min_cycles_enabled", False)
    st.session_state.setdefault("ui_custom_alert_min_cycles_24h", 1)
    st.session_state.setdefault("ui_custom_alert_error_enabled", False)
    st.session_state.setdefault("ui_custom_alert_error_max_24h", 0)
    st.session_state.setdefault("audit_replay_playing", False)
    st.session_state.setdefault("audit_replay_speed_sec", 2)


def _apply_global_pending_ui_state() -> None:
    pending_layout = st.session_state.get("ui_layout_pending_load", {})
    if isinstance(pending_layout, dict) and pending_layout:
        for k, v in pending_layout.items():
            st.session_state[k] = v
        st.session_state["ui_layout_pending_load"] = {}

    pending_auto = st.session_state.get("ui_pending_auto_refresh_enabled", None)
    if pending_auto is not None:
        st.session_state["ui_auto_refresh_enabled"] = bool(pending_auto)
        st.session_state["ui_pending_auto_refresh_enabled"] = None

    pending_db = str(st.session_state.get("ui_pending_sidebar_custom_db", "") or "").strip()
    if pending_db:
        st.session_state["sidebar_custom_db"] = pending_db
        st.session_state["ui_pending_sidebar_custom_db"] = ""
    pending_limit = st.session_state.get("ui_pending_sidebar_event_limit", None)
    if pending_limit is not None:
        try:
            st.session_state["sidebar_event_limit"] = int(pending_limit)
        except Exception:
            pass
        st.session_state["ui_pending_sidebar_event_limit"] = None


def _apply_terminal_pending_ui_state(symbols: list[str]) -> None:
    pending_view = str(st.session_state.get("ui_pending_symbol_view_mode", "") or "").strip()
    if pending_view:
        st.session_state["ui_symbol_view_mode"] = pending_view
        st.session_state["ui_pending_symbol_view_mode"] = ""

    pending_chart = str(st.session_state.get("ui_pending_symbol_chart_type", "") or "").strip()
    if pending_chart:
        st.session_state["ui_symbol_chart_type"] = pending_chart
        st.session_state["ui_pending_symbol_chart_type"] = ""

    pending_symbol = str(st.session_state.get("ui_pending_selected_symbol", "") or "").strip().upper()
    if pending_symbol:
        if pending_symbol in symbols:
            st.session_state["ui_selected_symbol"] = pending_symbol
        st.session_state["ui_pending_selected_symbol"] = ""

    pending_window = str(st.session_state.get("ui_pending_time_window", "") or "").strip().upper()
    if pending_window:
        if pending_window in TIME_WINDOW_OPTIONS:
            st.session_state["ui_time_window"] = pending_window
        st.session_state["ui_pending_time_window"] = ""


def _clear_auth_session(cfg: AuthConfig | None = None) -> None:
    token = str(st.session_state.get("auth_session_token", "") or "").strip()
    if cfg is not None and token:
        _disable_persistent_session(cfg, token)
    _queue_clear_session_cookie()
    st.session_state.auth_ok = False
    st.session_state.auth_user = ""
    st.session_state.auth_method = ""
    st.session_state.auth_session_token = ""
    st.session_state.oauth_state = ""
    st.session_state.oauth_nonce = ""
    st.session_state.oauth_code_verifier = ""
    st.session_state.ui_prefs_loaded_for_user = ""
    st.session_state.sidebar_loaded_for_user = ""


def _touch_session_activity() -> None:
    st.session_state.session_last_activity_ts = float(datetime.now(tz=timezone.utc).timestamp())


def _enforce_session_timeout(cfg: AuthConfig) -> bool:
    if not bool(st.session_state.get("auth_ok", False)):
        return False
    now_ts = float(datetime.now(tz=timezone.utc).timestamp())
    last_ts = float(st.session_state.get("session_last_activity_ts", 0.0) or 0.0)
    if last_ts <= 0:
        st.session_state.session_last_activity_ts = now_ts
        return False
    idle_min = (now_ts - last_ts) / 60.0
    if idle_min > float(cfg.session_timeout_min):
        _clear_auth_session(cfg)
        _set_auth_notice("warning", f"Session timed out after {cfg.session_timeout_min} minutes of inactivity. Please sign in again.")
        return True
    return False


def _role_guard(current_role: str, min_role: str, message: str = "Insufficient permissions for this section.") -> bool:
    if _role_at_least(current_role, min_role):
        return True
    st.warning(message)
    return False


def _auto_refresh_pulse(enabled: bool, interval_seconds: int) -> None:
    if not enabled:
        return
    sec = int(interval_seconds)
    if sec <= 0:
        return
    components.html(
        f"""
        <script>
          setTimeout(function() {{
            window.parent.location.reload();
          }}, {sec * 1000});
        </script>
        """,
        height=0,
        width=0,
    )


def _live_stream_pulse(enabled: bool, transport: str, interval_seconds: int, sse_url: str) -> None:
    if not enabled:
        return
    sec = int(interval_seconds)
    if sec <= 0:
        return
    transport_n = str(transport or "Polling").strip()
    if transport_n.startswith("SSE"):
        endpoint = str(sse_url or "").strip()
        if endpoint:
            endpoint_js = endpoint.replace("\\", "\\\\").replace("'", "\\'")
            components.html(
                f"""
                <script>
                  (function() {{
                    var fired = false;
                    function reloadOnce() {{
                      if (fired) return;
                      fired = true;
                      window.parent.location.reload();
                    }}
                    try {{
                      var es = new EventSource('{endpoint_js}');
                      es.onmessage = function() {{ reloadOnce(); }};
                      es.onerror = function() {{
                        setTimeout(reloadOnce, {sec * 1000});
                        try {{ es.close(); }} catch (e) {{}}
                      }};
                      setTimeout(function() {{
                        try {{ es.close(); }} catch (e) {{}}
                        reloadOnce();
                      }}, {sec * 1000});
                    }} catch (e) {{
                      setTimeout(reloadOnce, {sec * 1000});
                    }}
                  }})();
                </script>
                """,
                height=0,
                width=0,
            )
            return
    # Polling fallback
    _auto_refresh_pulse(enabled=True, interval_seconds=sec)


def _process_oauth_callback(cfg: AuthConfig) -> None:
    if not _oauth_ready(cfg):
        return
    qp = st.query_params

    def _qp_value(name: str) -> str:
        value = qp.get(name, "")
        if isinstance(value, list):
            return str(value[0] if value else "").strip()
        return str(value or "").strip()

    code = _qp_value("code")
    state = _qp_value("state")
    err = _qp_value("error")
    if not (code or err):
        return

    # Clear query params after processing to prevent replay on reruns.
    def _clear_params() -> None:
        try:
            st.query_params.clear()
        except Exception:
            pass

    def _clear_oauth_secrets() -> None:
        st.session_state.oauth_state = ""
        st.session_state.oauth_nonce = ""
        st.session_state.oauth_code_verifier = ""

    if err:
        _clear_params()
        _clear_oauth_secrets()
        _set_auth_notice("error", f"OAuth sign-in failed: {err}")
        return

    expected_state = str(st.session_state.get("oauth_state", "") or "")
    expected_nonce = str(st.session_state.get("oauth_nonce", "") or "")
    expected_code_verifier = str(st.session_state.get("oauth_code_verifier", "") or "")
    if (not expected_state) or (state != expected_state):
        _clear_params()
        _clear_oauth_secrets()
        _set_auth_notice("error", "OAuth state validation failed. Please retry sign-in.")
        return
    if not expected_code_verifier:
        _clear_params()
        _clear_oauth_secrets()
        _set_auth_notice("error", "OAuth PKCE verifier missing. Please retry sign-in.")
        return

    try:
        token = _oauth_exchange_code(cfg, code, expected_code_verifier)
        access_token = str(token.get("access_token", "") or "")
        if not access_token:
            raise RuntimeError("OAuth token response missing access_token.")
        id_token = str(token.get("id_token", "") or "")
        if id_token:
            claims = _decode_jwt_payload_unverified(id_token)
            token_nonce = str(claims.get("nonce", "") or "")
            if expected_nonce and token_nonce and token_nonce != expected_nonce:
                raise RuntimeError("OAuth nonce validation failed.")
            token_aud = claims.get("aud")
            aud_ok = False
            if isinstance(token_aud, str):
                aud_ok = token_aud == cfg.oauth_client_id
            elif isinstance(token_aud, list):
                aud_ok = cfg.oauth_client_id in [str(x) for x in token_aud]
            else:
                aud_ok = True
            if (not aud_ok) and cfg.oauth_client_id:
                raise RuntimeError("OAuth id_token audience mismatch.")
            token_iss = str(claims.get("iss", "") or "")
            if cfg.oauth_issuer and token_iss:
                if token_iss.rstrip("/") != cfg.oauth_issuer.rstrip("/"):
                    raise RuntimeError("OAuth id_token issuer mismatch.")
            token_exp = claims.get("exp")
            if token_exp is not None:
                try:
                    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
                    if int(token_exp) < (now_ts - 60):
                        raise RuntimeError("OAuth id_token is expired.")
                except ValueError:
                    raise RuntimeError("OAuth id_token exp claim is invalid.")
        userinfo = _oauth_fetch_userinfo(cfg, access_token)
        email = str(
            userinfo.get("email")
            or userinfo.get("preferred_username")
            or userinfo.get("upn")
            or ""
        ).strip()
        display_name = str(
            userinfo.get("name")
            or userinfo.get("given_name")
            or userinfo.get("nickname")
            or email
            or "oauth_user"
        ).strip()
        provider_sub = str(
            userinfo.get("sub")
            or userinfo.get("id")
            or userinfo.get("user_id")
            or email
        ).strip()
        if not provider_sub:
            raise RuntimeError("OAuth userinfo response missing unique user id.")

        if cfg.oauth_allowed_domain and email:
            if "@" not in email or email.lower().split("@", 1)[1] != cfg.oauth_allowed_domain:
                raise RuntimeError(f"OAuth email domain not allowed: {cfg.oauth_allowed_domain}")

        username = _oauth_user_login(cfg, provider_sub=provider_sub, email=email, display_name=display_name)
        st.session_state.auth_ok = True
        st.session_state.auth_user = username
        st.session_state.auth_method = f"oauth:{cfg.oauth_provider}"
        token = _create_persistent_session(cfg, username=username, auth_method=st.session_state.auth_method)
        st.session_state.auth_session_token = token
        _queue_set_session_cookie(token, max_age_sec=_session_ttl_days() * 24 * 3600)
        _set_auth_notice("success", f"Signed in with {cfg.oauth_provider.title()} as `{username}`.")
        _clear_oauth_secrets()
        _clear_params()
        st.rerun()
    except Exception as exc:
        _clear_params()
        _clear_oauth_secrets()
        _set_auth_notice("error", f"OAuth sign-in error: {exc}")


def _render_login(cfg: AuthConfig) -> None:
    _init_users_db(cfg)
    _process_oauth_callback(cfg)
    st.markdown("<div class='login-wrap'>", unsafe_allow_html=True)
    st.markdown(
        """
        <div class='auth-image-row'>
          <div class='auth-image-tile auth-image-left'><span>Realtime Market Intelligence</span></div>
          <div class='auth-image-tile auth-image-right'><span>Execution Risk Watch</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    left, right = st.columns([1.1, 1.0])

    with left:
        st.markdown("##### Secure Operator Access")
        st.markdown(f"## {APP_TITLE}")
        st.markdown(
            "Centralized interface for paper/live execution of "
            "`aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m` with switch-variant overlays. "
            "Use this desk to confirm state, observe decisions, and validate order outcomes in one place."
        )
        st.markdown("- Confirm runtime freshness before market decisions are trusted.")
        st.markdown("- Track variant transitions (`baseline`, `inverse_ma20`, `inverse_ma60`) and reasons.")
        st.markdown("- Inspect event-derived position drift versus current target allocations.")
        metric_cols = st.columns(3)
        with metric_cols[0]:
            st.caption("Primary Profile")
            st.code("aggr_adapt_t10_tr2", language="text")
        with metric_cols[1]:
            st.caption("Decision Time")
            st.code("15:55 NY", language="text")
        with metric_cols[2]:
            st.caption("Data Feed")
            st.code("SIP", language="text")
        st.caption(
            "Operational note: this UI is read/operate only. Strategy logic lives in runtime tools and the switch profile."
        )

    with right:
        with st.container(border=True):
            st.markdown("<div class='auth-panel'>", unsafe_allow_html=True)
            st.markdown("#### Access")
            st.caption("Use approved credentials or your configured OAuth provider.")
            _render_auth_notice()
            views = ["Sign In"]
            if cfg.allow_registration:
                views.append("Register")
            requested_view = str(st.session_state.get("auth_view_requested", "") or "").strip()
            current_auth_view = str(st.session_state.get("auth_view", "Sign In") or "Sign In")
            if requested_view in views:
                # Safe here: this runs before the auth_view widget is created.
                st.session_state["auth_view"] = requested_view
                current_auth_view = requested_view
                st.session_state.auth_view_requested = ""
            if current_auth_view not in views:
                current_auth_view = views[0]
            selected_view = st.radio(
                "Auth Action",
                options=views,
                index=views.index(current_auth_view),
                horizontal=True,
                key="auth_view",
                label_visibility="collapsed",
            )

            if selected_view == "Sign In":
                st.caption("Sign in with your local operator account.")
                with st.form("login_form", clear_on_submit=False, border=False):
                    username = st.text_input("Username", placeholder="Enter username", key="login_user_input")
                    password = st.text_input("Password", type="password", placeholder="Enter password", key="login_pass_input")
                    totp_code = ""
                    if cfg.totp_enabled:
                        totp_code = st.text_input("Authenticator Code", placeholder="6-digit code", key="login_totp_input")
                    submit = st.form_submit_button("Sign In", use_container_width=True)
                if submit:
                    user = username.strip()
                    if not user or not password:
                        _set_auth_notice("error", "Username and password are required.")
                        st.rerun()
                    if cfg.totp_enabled and (not _verify_totp(totp_code, cfg.totp_secret)):
                        _set_auth_notice("error", "Invalid authenticator code.")
                        st.rerun()
                    local_ok, local_msg = _verify_local_user(cfg, user, password)
                    if local_ok:
                        st.session_state.auth_ok = True
                        st.session_state.auth_user = user
                        st.session_state.auth_method = "local"
                        token = _create_persistent_session(cfg, username=user, auth_method="local")
                        st.session_state.auth_session_token = token
                        _queue_set_session_cookie(token, max_age_sec=_session_ttl_days() * 24 * 3600)
                        _set_auth_notice("success", f"Welcome back, `{user}`.")
                        st.rerun()
                    elif user == cfg.username and _verify_env_password(password, cfg):
                        st.session_state.auth_ok = True
                        st.session_state.auth_user = user
                        st.session_state.auth_method = "env"
                        token = _create_persistent_session(cfg, username=user, auth_method="env")
                        st.session_state.auth_session_token = token
                        _queue_set_session_cookie(token, max_age_sec=_session_ttl_days() * 24 * 3600)
                        _set_auth_notice("success", f"Signed in as primary operator `{user}`.")
                        st.rerun()
                    else:
                        _set_auth_notice("error", local_msg or "Invalid username or password")
                        st.rerun()
            else:
                st.caption("Create a local account. Admin can later enable/disable access.")
                with st.form("register_form", clear_on_submit=False, border=False):
                    reg_user = st.text_input("New Username", placeholder="Choose username", key="register_user_input")
                    reg_pass = st.text_input("New Password", type="password", placeholder="At least 8 characters", key="register_pass_input")
                    reg_pass2 = st.text_input(
                        "Confirm Password",
                        type="password",
                        placeholder="Re-enter password",
                        key="register_pass2_input",
                    )
                    reg_submit = st.form_submit_button("Create Account", use_container_width=True)
                if reg_user:
                    ok_user, reason = _validate_username(reg_user)
                    if ok_user:
                        st.caption("Username format looks valid.")
                    else:
                        st.caption(f"Username rule: {reason}")
                if reg_pass:
                    score, label, details = _password_strength(reg_pass)
                    st.progress(score / 5.0, text=f"Password strength: {label} ({score}/5)")
                    st.caption(" | ".join(details))
                if reg_pass and reg_pass2 and reg_pass != reg_pass2:
                    st.caption("Password confirmation does not match.")
                if reg_submit:
                    if reg_pass != reg_pass2:
                        _set_auth_notice("error", "Password confirmation does not match.")
                        st.rerun()
                    else:
                        ok, msg = _register_local_user(cfg, reg_user, reg_pass)
                        if ok:
                            _set_auth_notice("success", msg)
                            st.session_state.auth_view_requested = "Sign In"
                            st.rerun()
                        else:
                            _set_auth_notice("error", msg)
                            st.rerun()

            if _oauth_ready(cfg):
                st.markdown("<div class='auth-divider'></div>", unsafe_allow_html=True)
                st.caption(f"OAuth provider: `{cfg.oauth_provider}`")
                if cfg.oauth_allowed_domain:
                    st.caption(f"Allowed email domain: `{cfg.oauth_allowed_domain}`")
                if (not st.session_state.get("oauth_state")) or (not st.session_state.get("oauth_nonce")) or (not st.session_state.get("oauth_code_verifier")):
                    st.session_state.oauth_state = secrets.token_urlsafe(24)
                    st.session_state.oauth_nonce = secrets.token_urlsafe(24)
                    st.session_state.oauth_code_verifier = secrets.token_urlsafe(64)
                oauth_url = _oauth_auth_url(
                    cfg,
                    st.session_state.oauth_state,
                    st.session_state.oauth_nonce,
                    _pkce_code_challenge(str(st.session_state.oauth_code_verifier)),
                )
                st.link_button(
                    f"Continue with {cfg.oauth_provider.title()}",
                    oauth_url,
                    use_container_width=True,
                )
            elif cfg.oauth_enabled:
                missing = _missing_oauth_fields(cfg)
                st.warning("OAuth is enabled but configuration is incomplete.")
                if missing:
                    st.code("\n".join(missing), language="text")
            st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


@st.cache_data(ttl=20, show_spinner=False)
def _load_runtime_db(db_path: str, event_limit: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    path = Path(db_path)
    if not path.exists():
        return pd.DataFrame(), pd.DataFrame()

    conn = sqlite3.connect(str(path))
    try:
        try:
            state_df = pd.read_sql_query(
                "SELECT key, value_json, updated_at FROM state_kv ORDER BY key",
                conn,
            )
        except Exception:
            state_df = pd.DataFrame()
        try:
            events_df = pd.read_sql_query(
                "SELECT id, ts, event_type, payload_json FROM events ORDER BY id DESC LIMIT ?",
                conn,
                params=(int(event_limit),),
            )
        except Exception:
            events_df = pd.DataFrame()
    finally:
        conn.close()

    if not state_df.empty:
        state_df["value"] = state_df["value_json"].map(_safe_json_load)
        state_df["updated_at"] = pd.to_datetime(state_df["updated_at"], errors="coerce", utc=True)
        state_df["updated_at_ny"] = state_df["updated_at"].dt.tz_convert(NY_TZ)

    if not events_df.empty:
        events_df["payload"] = events_df["payload_json"].map(_safe_json_load)
        events_df["ts"] = pd.to_datetime(events_df["ts"], errors="coerce", utc=True)
        events_df["ts_ny"] = events_df["ts"].dt.tz_convert(NY_TZ)
        events_df = _expand_payload_columns(events_df)

    return state_df, events_df


@st.cache_data(ttl=15, show_spinner=False)
def _load_broker_runtime_snapshot(mode: str, data_feed: str) -> dict[str, Any]:
    try:
        from soxl_growth.config import AlpacaConfig
        from soxl_growth.execution.broker import AlpacaBroker
    except Exception as exc:
        return {"status": "unavailable", "detail": f"broker modules unavailable: {exc}", "account": {}, "positions": [], "open_orders": []}

    try:
        paper = str(mode or "paper").strip().lower() != "live"
        cfg = AlpacaConfig.from_env(paper=paper, data_feed=str(data_feed or "sip"))
        broker = AlpacaBroker(cfg.api_key, cfg.api_secret, paper=cfg.paper)
        account = broker.get_account()
        positions = broker.list_positions()
        open_orders = broker.list_open_orders()
        return {
            "status": "ok",
            "detail": "ok",
            "paper": bool(cfg.paper),
            "feed": str(cfg.data_feed),
            "account": account,
            "positions": positions,
            "open_orders": open_orders,
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc), "account": {}, "positions": [], "open_orders": []}


def _safe_json_load(value: Any) -> Any:
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return value


def _expand_payload_columns(df: pd.DataFrame) -> pd.DataFrame:
    fields = [
        "symbol",
        "side",
        "qty",
        "price",
        "fill_price",
        "avg_fill_price",
        "limit_price",
        "stop_price",
        "trigger_price",
        "profile",
        "variant",
        "variant_reason",
        "order_type",
        "threshold_pct",
        "day",
        "intent_count",
        "orders_submitted",
        "take_profit_price",
        "stop_loss_price",
        "intraday_slot",
        "broker_order_id",
        "client_order_id",
        "order_status",
        "submitted_at",
        "filled_at",
        "canceled_at",
    ]

    for col in fields:
        df[col] = df["payload"].map(lambda p: p.get(col) if isinstance(p, dict) else None)

    df["payload_text"] = df["payload"].map(lambda p: json.dumps(p, sort_keys=True) if isinstance(p, dict) else str(p))
    return df


def _discover_db_candidates(current_user: str = "") -> list[str]:
    root = Path(__file__).resolve().parents[2]
    candidates = [
        root / "switch_runtime_v1_runtime.db",
        root / "switch_runtime_v1" / "switch_runtime_v1_runtime.db",
        Path.cwd() / "switch_runtime_v1_runtime.db",
    ]
    if str(current_user or "").strip():
        candidates.insert(0, _user_runtime_db_path(current_user))

    discovered = set()
    for c in candidates:
        if c.exists():
            discovered.add(str(c.resolve()))

    # Include any db files under switch_runtime_v1 for convenience.
    for p in (root / "switch_runtime_v1").glob("*.db"):
        discovered.add(str(p.resolve()))
    # Include user-scoped runtime dbs.
    user_root = root / "switch_runtime_v1" / "runtime_data" / "users"
    if user_root.exists():
        for p in user_root.glob("*/*.db"):
            discovered.add(str(p.resolve()))

    return sorted(discovered)


def _render_banner(db_path: str, state_df: pd.DataFrame, events_df: pd.DataFrame) -> None:
    latest_ts = "-"
    freshness = "No events"
    freshness_class = "warn-pill"
    if not events_df.empty and events_df["ts_ny"].notna().any():
        latest = events_df["ts_ny"].max()
        latest_ts = str(latest)
        age_min = int(max(0.0, (pd.Timestamp.now(tz=NY_TZ) - latest).total_seconds() // 60))
        if age_min <= 10:
            freshness = f"Fresh ({age_min}m)"
            freshness_class = "ok-pill"
        elif age_min <= 60:
            freshness = f"Delayed ({age_min}m)"
            freshness_class = "warn-pill"
        else:
            freshness = f"Stale ({age_min}m)"
            freshness_class = "err-pill"

    current_variant = _state_value(state_df, "switch_last_variant", "-")
    last_day = _state_value(state_df, "switch_executed_day", "-")
    latest_profile = "-"
    latest_order_type = "-"
    if not events_df.empty:
        p = events_df["profile"].dropna()
        o = events_df["order_type"].dropna()
        if not p.empty:
            latest_profile = str(p.iloc[0])
        if not o.empty:
            latest_order_type = str(o.iloc[0])
    tape = _recent_event_tape(events_df, limit=10)

    st.markdown(
        (
            "<div class='top-banner'>"
            "<div class='hero-grid'>"
            "<div class='hero-left'>"
            f"<div style='font-size:0.84rem;opacity:0.88;font-weight:600'>Execution Desk | SIP Feed Ops</div>"
            f"<h2>{APP_TITLE}</h2>"
            f"<div style='opacity:0.92'>{APP_SUBTITLE}</div>"
            "<div class='hero-meta'>"
            f"<span class='meta-chip mode-chip'>Mode: <b>Switch Runtime V1</b></span>"
            f"<span class='meta-chip'>Variant: <b>{current_variant}</b></span>"
            f"<span class='meta-chip'>Last Cycle Day: <b>{last_day}</b></span>"
            f"<span class='meta-chip'>Latest Event: <b>{latest_ts}</b></span>"
            f"<span class='{freshness_class}'>{freshness}</span>"
            "</div>"
            f"<div class='event-tape'><b>Event Tape</b>  {tape}</div>"
            "</div>"
            "<div class='hero-right'>"
            "<div class='hero-right-title'>Session Context</div>"
            "<div class='hero-right-row'>"
            f"<div><div class='hero-right-k'>Active Profile</div><div class='hero-right-v'>{latest_profile}</div></div>"
            f"<div><div class='hero-right-k'>Latest Order Type</div><div class='hero-right-v'>{latest_order_type}</div></div>"
            f"<div><div class='hero-right-k'>Runtime DB</div><div class='hero-right-v'>{Path(db_path).name}</div></div>"
            f"<div><div class='hero-right-k'>Feed Status</div><div class='hero-right-v'>{freshness}</div></div>"
            "</div>"
            "</div>"
            "</div>"
            f"<div style='margin-top:0.55rem;font-size:0.79rem;opacity:0.95'>DB: <b>{db_path}</b></div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_operator_brief(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    latest_cycle = _latest_cycle_payload(events_df) or {}
    variant = _state_value(state_df, "switch_last_variant", "-")
    reason = latest_cycle.get("variant_reason", "-")
    threshold = latest_cycle.get("threshold_pct")
    intents = latest_cycle.get("intent_count")
    orders = latest_cycle.get("orders_submitted")
    day = latest_cycle.get("day", _state_value(state_df, "switch_executed_day", "-"))

    target_alloc = _target_allocations_table(state_df)
    primary_symbol = "-"
    primary_weight = "-"
    if not target_alloc.empty:
        row = target_alloc.iloc[0]
        primary_symbol = str(row.get("symbol", "-"))
        wt = row.get("target_weight_pct")
        if pd.notna(wt):
            primary_weight = f"{float(wt):.2f}%"

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("##### Session Objective")
        st.write(f"- Trading day: `{day}`")
        st.write(f"- Primary target: `{primary_symbol}` at `{primary_weight}`")
        st.write("- Goal: keep execution state aligned with selected switch variant.")
    with c2:
        st.markdown("##### Active Decision Model")
        st.write(f"- Active variant: `{variant}`")
        st.write(f"- Variant reason: `{reason}`")
        if threshold is None:
            st.write("- Profit-lock threshold: `-`")
        else:
            st.write(f"- Profit-lock threshold: `{float(threshold):.2f}%`")
    with c3:
        st.markdown("##### Last Cycle Outcome")
        st.write(f"- Intent count: `{intents if intents is not None else '-'}`")
        st.write(f"- Orders submitted: `{orders if orders is not None else '-'}`")
        st.write("- Next action: verify fills/cancels in Execution Journal.")


def _render_kpis(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    total_events = int(len(events_df))
    latest_cycle = _latest_cycle_payload(events_df)
    current_variant = _state_value(state_df, "switch_last_variant", "-")
    last_day = _state_value(state_df, "switch_executed_day", "-")

    events_24h = 0
    if not events_df.empty and events_df["ts_ny"].notna().any():
        cutoff = pd.Timestamp.now(tz=NY_TZ) - pd.Timedelta(hours=24)
        events_24h = int((events_df["ts_ny"] >= cutoff).sum())

    orders_submitted = int(latest_cycle.get("orders_submitted", 0)) if latest_cycle else 0
    threshold_pct = latest_cycle.get("threshold_pct", None) if latest_cycle else None

    kpi_data = [
        ("Events Loaded", f"{total_events:,}", "Database rows in memory"),
        ("Events (24h)", f"{events_24h:,}", "Recent runtime activity"),
        ("Current Variant", str(current_variant), "Active switch state"),
        ("Last Cycle Day", str(last_day), "Cycle completion marker"),
        ("Orders (Last Cycle)", f"{orders_submitted}", "Submitted intents"),
        ("Threshold %", "-" if threshold_pct is None else f"{float(threshold_pct):.2f}", "Adaptive profit-lock"),
    ]
    cols = st.columns(6)
    for col, (label, value, sub) in zip(cols, kpi_data, strict=False):
        col.markdown(
            (
                "<div class='kpi-card'>"
                f"<div class='kpi-label'>{label}</div>"
                f"<div class='kpi-value'>{value}</div>"
                f"<div class='kpi-sub'>{sub}</div>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )


def _state_value(state_df: pd.DataFrame, key: str, default: Any) -> Any:
    if state_df.empty:
        return default
    subset = state_df[state_df["key"] == key]
    if subset.empty:
        return default
    value = subset.iloc[0]["value"]
    return value if value is not None else default


def _latest_cycle_payload(events_df: pd.DataFrame) -> dict[str, Any] | None:
    if events_df.empty:
        return None
    rows = events_df[events_df["event_type"] == "switch_cycle_complete"]
    if rows.empty:
        return None
    payload = rows.iloc[0]["payload"]
    return payload if isinstance(payload, dict) else None


def _recent_event_tape(events_df: pd.DataFrame, limit: int = 10) -> str:
    if events_df.empty:
        return "No recent events."
    view = events_df.dropna(subset=["ts_ny"]).head(limit).copy()
    if view.empty:
        return "No recent events."

    parts: list[str] = []
    for _, row in view.iterrows():
        ts = row["ts_ny"]
        hhmm = ts.strftime("%H:%M") if isinstance(ts, pd.Timestamp) else "--:--"
        event_type = str(row.get("event_type", "-"))
        symbol = row.get("symbol")
        marker = f"{event_type}:{symbol}" if isinstance(symbol, str) and symbol else event_type
        parts.append(f"[{hhmm}] {marker}")
    return "  |  ".join(parts)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except Exception:
        return None


def _equity_curve_frame(events_df: pd.DataFrame, state_df: pd.DataFrame) -> pd.DataFrame:
    if events_df.empty:
        return pd.DataFrame()
    cycles = events_df[events_df["event_type"] == "switch_cycle_complete"].copy()
    if cycles.empty:
        return pd.DataFrame()
    cycles = cycles.dropna(subset=["ts_ny"]).sort_values("ts_ny")
    if cycles.empty:
        return pd.DataFrame()

    simulated_mode = isinstance(_state_value(state_df, "switch_demo_equity", {}), dict)
    points: list[dict[str, Any]] = []
    for _, row in cycles.iterrows():
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        equity: float | None = None
        for k in ("equity", "end_equity", "ending_equity", "account_equity", "final_equity", "window_end_equity"):
            equity = _safe_float(payload.get(k))
            if equity is not None:
                break
        if equity is None and simulated_mode:
            regime = payload.get("regime_metrics")
            if isinstance(regime, dict):
                equity = _safe_float(regime.get("close"))
        if equity is None:
            continue
        points.append(
            {
                "ts_ny": row.get("ts_ny"),
                "day": payload.get("day"),
                "equity": float(equity),
                "variant": payload.get("variant"),
            }
        )

    curve = pd.DataFrame(points)
    if curve.empty:
        return curve
    curve = curve.sort_values("ts_ny").reset_index(drop=True)
    curve["running_peak"] = curve["equity"].cummax()
    curve["drawdown_pct"] = ((curve["equity"] / curve["running_peak"]) - 1.0) * 100.0
    curve["pnl"] = curve["equity"].diff().fillna(0.0)
    curve["ret_pct"] = curve["equity"].pct_change().fillna(0.0) * 100.0
    return curve


def _pnl_snapshot(curve: pd.DataFrame, state_df: pd.DataFrame) -> dict[str, Any]:
    if curve.empty:
        demo = _state_value(state_df, "switch_demo_equity", {})
        if isinstance(demo, dict) and demo:
            start_eq = _safe_float(demo.get("start_equity"))
            end_eq = _safe_float(demo.get("end_equity"))
            pnl = _safe_float(demo.get("pnl"))
            ret = _safe_float(demo.get("return_pct"))
            dd = _safe_float(demo.get("drawdown_pct"))
            return {
                "start_equity": start_eq,
                "end_equity": end_eq,
                "pnl": pnl,
                "return_pct": ret,
                "max_drawdown_pct": dd,
                "last_day_pnl": None,
            }
        return {}

    start_eq = float(curve.iloc[0]["equity"])
    end_eq = float(curve.iloc[-1]["equity"])
    pnl = end_eq - start_eq
    ret = ((end_eq / start_eq) - 1.0) * 100.0 if start_eq > 0 else None
    max_dd = float(curve["drawdown_pct"].min()) if not curve.empty else None
    last_day_pnl = float(curve.iloc[-1]["pnl"]) if len(curve) >= 1 else None
    return {
        "start_equity": start_eq,
        "end_equity": end_eq,
        "pnl": pnl,
        "return_pct": ret,
        "max_drawdown_pct": max_dd,
        "last_day_pnl": last_day_pnl,
    }


def _render_persistent_pnl_tiles(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    curve = _equity_curve_frame(events_df, state_df)
    snap = _pnl_snapshot(curve, state_df)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Runtime Equity", "-" if snap.get("end_equity") is None else f"${float(snap['end_equity']):,.2f}")
    m2.metric("Total PnL", "-" if snap.get("pnl") is None else f"${float(snap['pnl']):,.2f}")
    m3.metric("Return %", "-" if snap.get("return_pct") is None else f"{float(snap['return_pct']):.2f}%")
    m4.metric("Max DD %", "-" if snap.get("max_drawdown_pct") is None else f"{float(snap['max_drawdown_pct']):.2f}%")


def _equity_curve_chart(curve: pd.DataFrame) -> alt.Chart | None:
    if curve.empty:
        return None
    frame = curve.copy()
    return (
        alt.Chart(frame)
        .mark_line(point=True, strokeWidth=2.6, color="#00c805")
        .encode(
            x=alt.X("ts_ny:T", title="Time (NY)"),
            y=alt.Y("equity:Q", title="Equity"),
            tooltip=[
                "ts_ny:T",
                alt.Tooltip("equity:Q", format=",.2f"),
                alt.Tooltip("pnl:Q", format=",.2f"),
                alt.Tooltip("ret_pct:Q", format=".2f"),
                "variant:N",
            ],
        )
        .properties(height=300)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_view(strokeOpacity=0)
    )


def _drawdown_curve_chart(curve: pd.DataFrame) -> alt.Chart | None:
    if curve.empty or "drawdown_pct" not in curve.columns:
        return None
    frame = curve.copy()
    return (
        alt.Chart(frame)
        .mark_area(color="#ff5d7a", opacity=0.35, line={"color": "#ff5d7a", "strokeWidth": 2})
        .encode(
            x=alt.X("ts_ny:T", title="Time (NY)"),
            y=alt.Y("drawdown_pct:Q", title="Drawdown %"),
            tooltip=["ts_ny:T", alt.Tooltip("drawdown_pct:Q", format=".2f"), "variant:N"],
        )
        .properties(height=220)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_view(strokeOpacity=0)
    )


def _rolling_sharpe_chart(curve: pd.DataFrame, window: int = 20) -> alt.Chart | None:
    if curve.empty or "ret_pct" not in curve.columns or len(curve) < max(5, window):
        return None
    frame = curve.copy()
    returns = pd.to_numeric(frame["ret_pct"], errors="coerce") / 100.0
    mean = returns.rolling(window=window, min_periods=max(5, window // 2)).mean()
    std = returns.rolling(window=window, min_periods=max(5, window // 2)).std()
    sharpe = (mean / std.replace(0.0, pd.NA)) * (252**0.5)
    frame["rolling_sharpe"] = pd.to_numeric(sharpe, errors="coerce")
    frame = frame.dropna(subset=["rolling_sharpe"])
    if frame.empty:
        return None
    return (
        alt.Chart(frame)
        .mark_line(color="#66d7ff", strokeWidth=2.2, point=True)
        .encode(
            x=alt.X("ts_ny:T", title="Time (NY)"),
            y=alt.Y("rolling_sharpe:Q", title=f"Rolling Sharpe ({window})"),
            tooltip=["ts_ny:T", alt.Tooltip("rolling_sharpe:Q", format=".2f"), "variant:N"],
        )
        .properties(height=220)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_view(strokeOpacity=0)
    )


def _variant_exposure_heatmap(curve: pd.DataFrame) -> alt.Chart | None:
    if curve.empty or "variant" not in curve.columns:
        return None
    frame = curve.copy()
    frame = frame.dropna(subset=["ts_ny"])
    if frame.empty:
        return None
    frame["week"] = frame["ts_ny"].dt.strftime("%Y-%W")
    frame["variant"] = frame["variant"].fillna("unknown").astype(str)
    grouped = frame.groupby(["week", "variant"], as_index=False).size().rename(columns={"size": "samples"})
    if grouped.empty:
        return None
    return (
        alt.Chart(grouped)
        .mark_rect()
        .encode(
            x=alt.X("week:N", title="Week"),
            y=alt.Y("variant:N", title="Variant"),
            color=alt.Color("samples:Q", title="Samples", scale=alt.Scale(scheme="greens")),
            tooltip=["week:N", "variant:N", "samples:Q"],
        )
        .properties(height=220)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff")
        .configure_view(strokeOpacity=0)
    )


def _events_by_hour_chart(events_df: pd.DataFrame) -> alt.Chart | None:
    if events_df.empty or events_df["ts_ny"].isna().all():
        return None

    frame = events_df[["ts_ny", "event_type"]].dropna().copy()
    frame["hour"] = frame["ts_ny"].dt.floor("h")
    grouped = frame.groupby(["hour", "event_type"]).size().reset_index(name="count")

    chart = (
        alt.Chart(grouped)
        .mark_area(opacity=0.38, line=True)
        .encode(
            x=alt.X("hour:T", title="Time (NY)"),
            y=alt.Y("count:Q", title="Event Count"),
            color=alt.Color(
                "event_type:N",
                title="Event Type",
                scale=alt.Scale(
                    range=["#00c805", "#52db66", "#9bec74", "#aab4be", "#f3b13c", "#ff5d7a", "#66d7ff"]
                ),
            ),
            tooltip=["hour:T", "event_type:N", "count:Q"],
        )
        .properties(height=320)
        .configure_axis(
            labelColor="#afc3e6",
            titleColor="#d8e6ff",
            gridColor="rgba(159,179,213,0.2)",
        )
        .configure_legend(
            labelColor="#d8e6ff",
            titleColor="#d8e6ff",
        )
        .configure_view(
            strokeOpacity=0,
        )
    )
    return chart


def _event_type_distribution(events_df: pd.DataFrame) -> alt.Chart | None:
    if events_df.empty:
        return None
    grouped = events_df.groupby("event_type").size().reset_index(name="count").sort_values("count", ascending=False)

    chart = (
        alt.Chart(grouped)
        .mark_bar(opacity=0.92, cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
        .encode(
            x=alt.X("count:Q", title="Count"),
            y=alt.Y("event_type:N", sort="-x", title="Event Type"),
            color=alt.Color(
                "event_type:N",
                legend=None,
                scale=alt.Scale(
                    range=["#00c805", "#52db66", "#9bec74", "#aab4be", "#f3b13c", "#ff5d7a", "#66d7ff"]
                ),
            ),
            tooltip=["event_type:N", "count:Q"],
        )
        .properties(height=320)
        .configure_axis(
            labelColor="#afc3e6",
            titleColor="#d8e6ff",
            gridColor="rgba(159,179,213,0.2)",
        )
        .configure_legend(
            labelColor="#d8e6ff",
            titleColor="#d8e6ff",
        )
        .configure_view(
            strokeOpacity=0,
        )
    )
    return chart


def _extract_cycle_events(events_df: pd.DataFrame) -> pd.DataFrame:
    if events_df.empty:
        return pd.DataFrame()
    cycles = events_df[events_df["event_type"] == "switch_cycle_complete"].copy()
    if cycles.empty:
        return pd.DataFrame()
    cycles = cycles.dropna(subset=["ts_ny"]).sort_values("ts_ny").copy()
    cycles["day"] = cycles["payload"].map(lambda p: p.get("day") if isinstance(p, dict) else None)
    cycles["profile"] = cycles["payload"].map(lambda p: p.get("profile") if isinstance(p, dict) else None)
    cycles["variant"] = cycles["payload"].map(lambda p: p.get("variant") if isinstance(p, dict) else None)
    cycles["variant_reason"] = cycles["payload"].map(lambda p: p.get("variant_reason") if isinstance(p, dict) else None)
    cycles["threshold_pct"] = pd.to_numeric(
        cycles["payload"].map(lambda p: p.get("threshold_pct") if isinstance(p, dict) else None),
        errors="coerce",
    )
    cycles["orders_submitted"] = pd.to_numeric(
        cycles["payload"].map(lambda p: p.get("orders_submitted") if isinstance(p, dict) else None),
        errors="coerce",
    )
    cycles["intent_count"] = pd.to_numeric(
        cycles["payload"].map(lambda p: p.get("intent_count") if isinstance(p, dict) else None),
        errors="coerce",
    )
    cycles["rv20_ann"] = pd.to_numeric(
        cycles["payload"].map(
            lambda p: p.get("regime_metrics", {}).get("rv20_ann")
            if isinstance(p, dict) and isinstance(p.get("regime_metrics"), dict)
            else None
        ),
        errors="coerce",
    )
    cycles["dd20_pct"] = pd.to_numeric(
        cycles["payload"].map(
            lambda p: p.get("regime_metrics", {}).get("dd20_pct")
            if isinstance(p, dict) and isinstance(p.get("regime_metrics"), dict)
            else None
        ),
        errors="coerce",
    )
    return cycles


def _health_snapshot(events_df: pd.DataFrame, state_df: pd.DataFrame) -> dict[str, Any]:
    now = pd.Timestamp.now(tz=NY_TZ)
    out: dict[str, Any] = {
        "event_count": int(len(events_df)),
        "state_key_count": int(len(state_df)),
        "freshness_min": None,
        "events_5m": 0,
        "events_1h": 0,
        "cycles_24h": 0,
        "order_events_24h": 0,
        "error_events_24h": 0,
        "median_cycle_gap_min": None,
        "max_cycle_gap_min": None,
    }
    if events_df.empty or events_df["ts_ny"].isna().all():
        return out

    ts = events_df["ts_ny"].dropna()
    freshest = ts.max()
    out["freshness_min"] = float(max(0.0, (now - freshest).total_seconds() / 60.0))
    cutoff_5m = now - pd.Timedelta(minutes=5)
    cutoff_1h = now - pd.Timedelta(hours=1)
    cutoff_24h = now - pd.Timedelta(hours=24)
    out["events_5m"] = int((ts >= cutoff_5m).sum())
    out["events_1h"] = int((ts >= cutoff_1h).sum())

    recent_24h = events_df[events_df["ts_ny"] >= cutoff_24h]
    out["cycles_24h"] = int((recent_24h["event_type"] == "switch_cycle_complete").sum())
    out["order_events_24h"] = int(
        recent_24h["event_type"].isin(["switch_rebalance_order", "switch_profit_lock_close", "switch_profit_lock_intraday_close"]).sum()
    )
    out["error_events_24h"] = int(
        recent_24h["event_type"].str.contains("error|reject|failed|fail", case=False, na=False).sum()
    )

    cycles = _extract_cycle_events(events_df)
    if len(cycles) >= 2:
        gaps = cycles["ts_ny"].diff().dropna().dt.total_seconds() / 60.0
        if not gaps.empty:
            out["median_cycle_gap_min"] = float(gaps.median())
            out["max_cycle_gap_min"] = float(gaps.max())
    return out


@st.cache_data(ttl=90, show_spinner=False)
def _broker_reachability_snapshot(base_url: str, api_key: str, api_secret: str) -> dict[str, Any]:
    out = {"status": "unknown", "detail": "not checked", "http_status": None}
    if not base_url or not api_key or not api_secret:
        out["status"] = "not_configured"
        out["detail"] = "Missing Alpaca base URL or API credentials."
        return out
    url = base_url.rstrip("/") + "/v2/clock"
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("APCA-API-KEY-ID", api_key)
        req.add_header("APCA-API-SECRET-KEY", api_secret)
        with urllib.request.urlopen(req, timeout=4) as resp:
            status = int(getattr(resp, "status", 200))
            out["http_status"] = status
            if 200 <= status < 300:
                out["status"] = "ok"
                out["detail"] = "Broker API reachable."
            else:
                out["status"] = "degraded"
                out["detail"] = f"Broker API returned HTTP {status}."
    except Exception as exc:
        out["status"] = "fail"
        out["detail"] = f"Broker API check failed: {exc}"
    return out


def _runtime_health_flags(cfg: AuthConfig, db_path: str, events_df: pd.DataFrame, state_df: pd.DataFrame) -> dict[str, Any]:
    hs = _health_snapshot(events_df, state_df)
    db = Path(db_path)
    db_exists = db.exists()
    db_mtime_min = None
    if db_exists:
        mtime = pd.Timestamp(db.stat().st_mtime, unit="s", tz="UTC").tz_convert(NY_TZ)
        db_mtime_min = float(max(0.0, (pd.Timestamp.now(tz=NY_TZ) - mtime).total_seconds() / 60.0))

    api_key = os.getenv("ALPACA_API_KEY", "").strip()
    api_secret = os.getenv("ALPACA_API_SECRET", "").strip() or os.getenv("ALPACA_SECRET_KEY", "").strip()
    base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").strip()
    broker = _broker_reachability_snapshot(base_url, api_key, api_secret)
    return {
        "hs": hs,
        "db_exists": db_exists,
        "db_mtime_min": db_mtime_min,
        "broker": broker,
        "session_timeout_min": cfg.session_timeout_min,
    }


def _render_runtime_health_banner(cfg: AuthConfig, db_path: str, events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    flags = _runtime_health_flags(cfg, db_path=db_path, events_df=events_df, state_df=state_df)
    hs = flags["hs"]
    fresh = hs.get("freshness_min")
    db_ok = bool(flags["db_exists"])
    broker = flags["broker"]
    heartbeat_ok = int(hs.get("events_5m", 0)) > 0 or int(hs.get("cycles_24h", 0)) > 0
    fresh_ok = (fresh is not None) and (float(fresh) <= 15.0)

    summary = (
        f"DB={'OK' if db_ok else 'MISSING'} | "
        f"Freshness={'-' if fresh is None else f'{float(fresh):.1f}m'} | "
        f"Heartbeat={'OK' if heartbeat_ok else 'LOW'} | "
        f"Broker={str(broker.get('status')).upper()} | "
        f"SessionTimeout={int(flags['session_timeout_min'])}m"
    )
    if (not db_ok) or (not fresh_ok):
        st.error(f"Runtime Health: {summary}")
    elif str(broker.get("status")) in {"fail", "degraded"}:
        st.warning(f"Runtime Health: {summary} | {broker.get('detail')}")
    else:
        st.success(f"Runtime Health: {summary}")

def _slo_checks_table(events_df: pd.DataFrame, state_df: pd.DataFrame) -> pd.DataFrame:
    hs = _health_snapshot(events_df, state_df)
    fresh = hs.get("freshness_min")
    checks: list[dict[str, Any]] = [
        {
            "check": "Event freshness <= 10m",
            "value": "-" if fresh is None else f"{fresh:.1f}m",
            "target": "<= 10m",
            "status": "PASS" if fresh is not None and fresh <= 10 else "FAIL",
        },
        {
            "check": "Cycles in last 24h",
            "value": f"{int(hs['cycles_24h'])}",
            "target": ">= 1",
            "status": "PASS" if int(hs["cycles_24h"]) >= 1 else "WARN",
        },
        {
            "check": "Events in last 1h",
            "value": f"{int(hs['events_1h'])}",
            "target": ">= 1",
            "status": "PASS" if int(hs["events_1h"]) >= 1 else "WARN",
        },
        {
            "check": "Error-like events in 24h",
            "value": f"{int(hs['error_events_24h'])}",
            "target": "0",
            "status": "PASS" if int(hs["error_events_24h"]) == 0 else "WARN",
        },
        {
            "check": "State key availability",
            "value": f"{int(hs['state_key_count'])}",
            "target": ">= 4",
            "status": "PASS" if int(hs["state_key_count"]) >= 4 else "WARN",
        },
    ]
    return pd.DataFrame(checks)


def _generate_operator_alerts(events_df: pd.DataFrame, state_df: pd.DataFrame) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    hs = _health_snapshot(events_df, state_df)
    fresh = hs.get("freshness_min")
    if hs["event_count"] == 0:
        alerts.append(
            {
                "severity": "high",
                "title": "No runtime events found",
                "detail": "Dashboard has no events yet. Start runtime loop or verify DB path.",
            }
        )
        return alerts
    if fresh is not None and fresh > 60:
        alerts.append(
            {
                "severity": "high",
                "title": "Event stream is stale",
                "detail": f"Last event age is {fresh:.1f} minutes. Verify runtime process and connectivity.",
            }
        )
    elif fresh is not None and fresh > 10:
        alerts.append(
            {
                "severity": "medium",
                "title": "Event stream delay detected",
                "detail": f"Last event age is {fresh:.1f} minutes. Monitoring recommended.",
            }
        )

    if int(hs["cycles_24h"]) == 0:
        alerts.append(
            {
                "severity": "medium",
                "title": "No completed cycles in 24h",
                "detail": "Strategy cycle event `switch_cycle_complete` has not arrived in the last day.",
            }
        )

    if int(hs["error_events_24h"]) > 0:
        alerts.append(
            {
                "severity": "medium",
                "title": "Error-like events detected",
                "detail": f"Found {int(hs['error_events_24h'])} event(s) matching reject/error/fail in the last 24h.",
            }
        )
    return alerts


def _risk_guardrail_table(events_df: pd.DataFrame, state_df: pd.DataFrame) -> pd.DataFrame:
    hs = _health_snapshot(events_df, state_df)
    alloc = _target_allocations_table(state_df)
    max_alloc = float(alloc["target_weight_pct"].max()) if (not alloc.empty) else None

    cycle = _latest_cycle_payload(events_df) or {}
    dd20 = None
    regime = cycle.get("regime_metrics")
    if isinstance(regime, dict):
        dd20 = _safe_float(regime.get("dd20_pct"))

    positions = _estimated_positions_table(events_df, state_df)
    concentration = None
    if not positions.empty:
        abs_net = positions["net_qty_est"].astype(float).abs()
        total = float(abs_net.sum())
        if total > 0:
            concentration = float((abs_net.max() / total) * 100.0)

    rows: list[dict[str, Any]] = []

    def _row(name: str, current: Any, limit: str, status: str, action: str) -> None:
        rows.append(
            {
                "Guardrail": name,
                "Current": current,
                "Limit": limit,
                "Status": status,
                "Action": action,
            }
        )

    fresh = hs.get("freshness_min")
    if fresh is None:
        _row("Event Freshness", "-", "<= 10m", "WARN", "Check runtime process and data feed.")
    elif float(fresh) <= 10:
        _row("Event Freshness", f"{float(fresh):.1f}m", "<= 10m", "PASS", "None")
    elif float(fresh) <= 60:
        _row("Event Freshness", f"{float(fresh):.1f}m", "<= 10m", "WARN", "Investigate delayed cycle updates.")
    else:
        _row("Event Freshness", f"{float(fresh):.1f}m", "<= 10m", "BREACH", "Restart runtime or inspect broker/feed connectivity.")

    if max_alloc is None:
        _row("Max Target Allocation", "-", "<= 60%", "WARN", "No target allocation present in state.")
    elif max_alloc <= 60:
        _row("Max Target Allocation", f"{max_alloc:.2f}%", "<= 60%", "PASS", "None")
    elif max_alloc <= 80:
        _row("Max Target Allocation", f"{max_alloc:.2f}%", "<= 60%", "WARN", "Review concentration before open.")
    else:
        _row("Max Target Allocation", f"{max_alloc:.2f}%", "<= 60%", "BREACH", "Reduce single-symbol concentration.")

    if dd20 is None:
        _row("Regime Drawdown (dd20)", "-", "<= 20%", "WARN", "No dd20 metric in latest cycle payload.")
    elif dd20 <= 20:
        _row("Regime Drawdown (dd20)", f"{dd20:.2f}%", "<= 20%", "PASS", "None")
    elif dd20 <= 30:
        _row("Regime Drawdown (dd20)", f"{dd20:.2f}%", "<= 20%", "WARN", "Consider defensive variant / reduced risk.")
    else:
        _row("Regime Drawdown (dd20)", f"{dd20:.2f}%", "<= 20%", "BREACH", "Pause new risk until drawdown stabilizes.")

    err24 = int(hs.get("error_events_24h", 0))
    if err24 == 0:
        _row("Error-like Events (24h)", "0", "= 0", "PASS", "None")
    elif err24 <= 3:
        _row("Error-like Events (24h)", str(err24), "= 0", "WARN", "Inspect rejects/failures before next cycle.")
    else:
        _row("Error-like Events (24h)", str(err24), "= 0", "BREACH", "Stop execution and investigate.")

    if concentration is None:
        _row("Event-Derived Net Qty Concentration", "-", "<= 80%", "WARN", "No event-derived positions available.")
    elif concentration <= 80:
        _row("Event-Derived Net Qty Concentration", f"{concentration:.2f}%", "<= 80%", "PASS", "None")
    elif concentration <= 92:
        _row("Event-Derived Net Qty Concentration", f"{concentration:.2f}%", "<= 80%", "WARN", "Monitor concentration drift.")
    else:
        _row("Event-Derived Net Qty Concentration", f"{concentration:.2f}%", "<= 80%", "BREACH", "Reduce concentrated exposure.")

    return pd.DataFrame(rows)


def _notification_center_table(events_df: pd.DataFrame, state_df: pd.DataFrame) -> pd.DataFrame:
    notices: list[dict[str, Any]] = []
    now = pd.Timestamp.now(tz=NY_TZ)
    recent_cutoff = now - pd.Timedelta(hours=24)

    def _push(ts: Any, severity: str, title: str, detail: str, source: str) -> None:
        notices.append(
            {
                "ts": ts,
                "severity": severity,
                "title": title,
                "detail": detail,
                "source": source,
            }
        )

    # Health-derived alerts
    for a in _generate_operator_alerts(events_df, state_df):
        sev = str(a.get("severity", "medium")).lower()
        mapped = "high" if sev == "high" else ("medium" if sev == "medium" else "info")
        _push(now, mapped, str(a.get("title", "Alert")), str(a.get("detail", "")), "health")

    if not events_df.empty:
        # Variant changes
        vc = events_df[events_df["event_type"] == "switch_variant_changed"].copy().head(20)
        for _, row in vc.iterrows():
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            _push(
                row.get("ts_ny"),
                "info",
                f"Variant changed: {payload.get('from', '?')} -> {payload.get('to', '?')}",
                f"Reason: {payload.get('reason', '-')}",
                "switch_variant_changed",
            )

        # Profit-lock events
        pl = events_df[events_df["event_type"].isin(["switch_profit_lock_close", "switch_profit_lock_intraday_close"])].copy().head(30)
        for _, row in pl.iterrows():
            sym = row.get("symbol") or "-"
            side = row.get("side") or "-"
            qty = row.get("qty")
            detail = f"{sym} {side} qty={qty}"
            _push(row.get("ts_ny"), "medium", "Profit-lock execution", detail, str(row.get("event_type")))

        # Reject/failure scans in last 24h
        err = events_df[
            (events_df["ts_ny"] >= recent_cutoff)
            & events_df["event_type"].str.contains("error|reject|fail", case=False, na=False)
        ].copy()
        for _, row in err.head(20).iterrows():
            _push(
                row.get("ts_ny"),
                "high",
                f"Error event: {row.get('event_type')}",
                str(row.get("payload_text", ""))[:240],
                "runtime",
            )

    out = pd.DataFrame(notices)
    if out.empty:
        return out
    out["ts"] = pd.to_datetime(out["ts"], errors="coerce")
    out = out.sort_values("ts", ascending=False).reset_index(drop=True)
    return out


def _notification_severity_chart(notices: pd.DataFrame) -> alt.Chart | None:
    if notices.empty:
        return None
    frame = notices.copy()
    frame = frame.dropna(subset=["ts"])
    if frame.empty:
        return None
    grouped = frame.groupby(["severity"], as_index=False).size().rename(columns={"size": "count"})
    return (
        alt.Chart(grouped)
        .mark_bar(opacity=0.9, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("severity:N", sort=["high", "medium", "info"], title="Severity"),
            y=alt.Y("count:Q", title="Count"),
            color=alt.Color(
                "severity:N",
                scale=alt.Scale(domain=["high", "medium", "info"], range=["#ff5d7a", "#f3b13c", "#52db66"]),
                legend=None,
            ),
            tooltip=["severity:N", "count:Q"],
        )
        .properties(height=220)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_view(strokeOpacity=0)
    )


def _custom_alert_notices(events_df: pd.DataFrame, state_df: pd.DataFrame) -> pd.DataFrame:
    notices: list[dict[str, Any]] = []
    hs = _health_snapshot(events_df, state_df)
    now = pd.Timestamp.now(tz=NY_TZ)
    if bool(st.session_state.get("ui_custom_alert_stale_enabled", False)):
        stale_lim = float(st.session_state.get("ui_custom_alert_stale_min", 10) or 10)
        fresh = hs.get("freshness_min")
        if fresh is None or float(fresh) > stale_lim:
            notices.append(
                {
                    "ts": now,
                    "severity": "high",
                    "title": "Custom rule breach: stale event stream",
                    "detail": f"Freshness {fresh if fresh is not None else '-'}m exceeds limit {stale_lim:.1f}m",
                    "source": "custom_rule",
                }
            )
    if bool(st.session_state.get("ui_custom_alert_min_cycles_enabled", False)):
        min_cycles = int(st.session_state.get("ui_custom_alert_min_cycles_24h", 1) or 1)
        c24 = int(hs.get("cycles_24h", 0))
        if c24 < min_cycles:
            notices.append(
                {
                    "ts": now,
                    "severity": "medium",
                    "title": "Custom rule breach: low cycle count",
                    "detail": f"Cycles24h={c24} below required minimum {min_cycles}",
                    "source": "custom_rule",
                }
            )
    if bool(st.session_state.get("ui_custom_alert_error_enabled", False)):
        max_err = int(st.session_state.get("ui_custom_alert_error_max_24h", 0) or 0)
        e24 = int(hs.get("error_events_24h", 0))
        if e24 > max_err:
            notices.append(
                {
                    "ts": now,
                    "severity": "high",
                    "title": "Custom rule breach: error events",
                    "detail": f"Error-like events24h={e24} exceeds maximum {max_err}",
                    "source": "custom_rule",
                }
            )
    # Dynamic rule-builder rules.
    metric_map: dict[str, float] = {
        "freshness_min": float(pd.to_numeric(hs.get("freshness_min"), errors="coerce") or 0.0),
        "events_5m": float(pd.to_numeric(hs.get("events_5m"), errors="coerce") or 0.0),
        "events_1h": float(pd.to_numeric(hs.get("events_1h"), errors="coerce") or 0.0),
        "cycles_24h": float(pd.to_numeric(hs.get("cycles_24h"), errors="coerce") or 0.0),
        "order_events_24h": float(pd.to_numeric(hs.get("order_events_24h"), errors="coerce") or 0.0),
        "error_events_24h": float(pd.to_numeric(hs.get("error_events_24h"), errors="coerce") or 0.0),
    }

    def _rule_hit(cur: float, op: str, th: float) -> bool:
        op_n = str(op or "").strip()
        if op_n == ">":
            return cur > th
        if op_n == ">=":
            return cur >= th
        if op_n == "<":
            return cur < th
        if op_n == "<=":
            return cur <= th
        if op_n == "==":
            return abs(cur - th) <= 1e-9
        if op_n == "!=":
            return abs(cur - th) > 1e-9
        return False

    dyn_rules = st.session_state.get("ui_alert_rules", [])
    if isinstance(dyn_rules, list):
        for rr in dyn_rules:
            if not isinstance(rr, dict):
                continue
            metric = str(rr.get("metric", "") or "").strip()
            if metric not in metric_map:
                continue
            try:
                threshold = float(pd.to_numeric(rr.get("threshold"), errors="coerce"))
            except Exception:
                continue
            operator = str(rr.get("operator", ">") or ">").strip()
            cur_v = float(metric_map.get(metric, 0.0))
            if _rule_hit(cur_v, operator, threshold):
                sev = str(rr.get("severity", "medium") or "medium").strip().lower()
                if sev not in {"high", "medium", "info"}:
                    sev = "medium"
                title = str(rr.get("title", "") or "").strip() or f"Rule breach: {metric} {operator} {threshold}"
                notices.append(
                    {
                        "ts": now,
                        "severity": sev,
                        "title": title,
                        "detail": f"{metric}={cur_v:.4f} breached rule `{metric} {operator} {threshold}`",
                        "source": "rule_builder",
                    }
                )
    return pd.DataFrame(notices)


def _send_webhook_message(url: str, payload: dict[str, Any]) -> tuple[bool, str]:
    endpoint = str(url or "").strip()
    if not endpoint:
        return False, "Webhook URL is empty."
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(endpoint, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = int(getattr(resp, "status", 200))
        if 200 <= code < 300:
            return True, f"Webhook delivered (HTTP {code})."
        return False, f"Webhook failed (HTTP {code})."
    except Exception as exc:
        return False, f"Webhook error: {exc}"


def _send_email_message(subject: str, body: str, to_addr: str) -> tuple[bool, str]:
    host = str(os.getenv("SWITCH_UI_SMTP_HOST", "") or "").strip()
    port = int(pd.to_numeric(os.getenv("SWITCH_UI_SMTP_PORT", "587"), errors="coerce") or 587)
    user = str(os.getenv("SWITCH_UI_SMTP_USER", "") or "").strip()
    pwd = str(os.getenv("SWITCH_UI_SMTP_PASSWORD", "") or "").strip()
    sender = str(os.getenv("SWITCH_UI_SMTP_FROM", user or "") or "").strip()
    if not host or not sender:
        return False, "SMTP not configured (need SWITCH_UI_SMTP_HOST and SWITCH_UI_SMTP_FROM/SWITCH_UI_SMTP_USER)."
    if not to_addr:
        return False, "Recipient address is empty."
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_addr
    msg.set_content(body)
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls(context=context)
            if user and pwd:
                server.login(user, pwd)
            server.send_message(msg)
        return True, "Email delivered."
    except Exception as exc:
        return False, f"Email error: {exc}"


def _global_filter_toolbar(events_df: pd.DataFrame) -> pd.DataFrame:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-caption'>Global filters and workspace sync controls (applied to all tabs unless overridden).</div>",
        unsafe_allow_html=True,
    )
    enabled = st.checkbox("Enable Global Filters", value=bool(st.session_state.get("ui_global_filters_enabled", False)), key="ui_global_filters_enabled")
    c1, c2, c3 = st.columns([1.0, 1.0, 2.0])
    with c1:
        st.selectbox(
            "Global Window",
            options=list(TIME_WINDOW_OPTIONS),
            index=list(TIME_WINDOW_OPTIONS).index(str(st.session_state.get("ui_global_time_window", "1M")) if str(st.session_state.get("ui_global_time_window", "1M")) in TIME_WINDOW_OPTIONS else "1M"),
            key="ui_global_time_window",
            disabled=not enabled,
        )
    with c2:
        st.text_input(
            "Global Search",
            value=str(st.session_state.get("ui_global_filter_query", "") or ""),
            key="ui_global_filter_query",
            placeholder="symbol/event/variant/order type...",
            disabled=not enabled,
        )
    all_symbols = sorted(events_df["symbol"].dropna().astype(str).str.upper().unique().tolist()) if (not events_df.empty and "symbol" in events_df.columns) else []
    all_events = sorted(events_df["event_type"].dropna().astype(str).unique().tolist()) if (not events_df.empty and "event_type" in events_df.columns) else []
    all_variants = sorted([v for v in events_df.get("variant", pd.Series(dtype=str)).dropna().astype(str).unique().tolist() if v]) if not events_df.empty else []
    all_sides = sorted([s for s in events_df.get("side", pd.Series(dtype=str)).dropna().astype(str).str.lower().unique().tolist() if s]) if not events_df.empty else []
    all_order_types = sorted([s for s in events_df.get("order_type", pd.Series(dtype=str)).dropna().astype(str).str.lower().unique().tolist() if s]) if not events_df.empty else []
    sym_defaults = [s for s in list(st.session_state.get("ui_global_symbols", [])) if s in all_symbols]
    ev_defaults = [s for s in list(st.session_state.get("ui_global_event_types", [])) if s in all_events]
    var_defaults = [s for s in list(st.session_state.get("ui_global_variants", [])) if s in all_variants]
    side_defaults = [s for s in list(st.session_state.get("ui_global_sides", [])) if s in all_sides]
    ord_defaults = [s for s in list(st.session_state.get("ui_global_order_types", [])) if s in all_order_types]
    c4, c5 = st.columns(2)
    with c4:
        st.multiselect("Global Symbols", options=all_symbols, default=sym_defaults, key="ui_global_symbols", disabled=not enabled)
        st.multiselect("Global Variants", options=all_variants, default=var_defaults, key="ui_global_variants", disabled=not enabled)
    with c5:
        st.multiselect("Global Event Types", options=all_events, default=ev_defaults, key="ui_global_event_types", disabled=not enabled)
        st.multiselect("Global Sides", options=all_sides, default=side_defaults, key="ui_global_sides", disabled=not enabled)
        st.multiselect("Global Order Types", options=all_order_types, default=ord_defaults, key="ui_global_order_types", disabled=not enabled)
    if enabled:
        filtered = _apply_advanced_event_filters(
            events_df,
            window=str(st.session_state.get("ui_global_time_window", "1M")),
            symbols=list(st.session_state.get("ui_global_symbols", [])),
            event_types=list(st.session_state.get("ui_global_event_types", [])),
            variants=list(st.session_state.get("ui_global_variants", [])),
            sides=list(st.session_state.get("ui_global_sides", [])),
            order_types=list(st.session_state.get("ui_global_order_types", [])),
            query=str(st.session_state.get("ui_global_filter_query", "")),
        )
        st.caption(f"Global filters ON | rows={len(filtered):,} from source rows={len(events_df):,}")
    else:
        filtered = events_df.copy()
        st.caption("Global filters OFF")
    st.markdown("</div>", unsafe_allow_html=True)
    return filtered


def _render_status_bar(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    hs = _health_snapshot(events_df, state_df)
    fresh = hs.get("freshness_min")
    if fresh is None:
        freshness_text = "EventStream: unknown"
        freshness_class = "status-warn"
    elif fresh <= 10:
        freshness_text = f"EventStream: healthy ({fresh:.1f}m)"
        freshness_class = "status-good"
    elif fresh <= 60:
        freshness_text = f"EventStream: delayed ({fresh:.1f}m)"
        freshness_class = "status-warn"
    else:
        freshness_text = f"EventStream: stale ({fresh:.1f}m)"
        freshness_class = "status-bad"

    latest_profile = _state_value(state_df, "switch_last_profile", "unknown")
    last_day = _state_value(state_df, "switch_executed_day", "-")
    mode_text = "Profile: aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m_switch_v1"
    # This status bar mirrors operator-console style badges from the UX docs.
    st.markdown(
        (
            "<div class='status-bar'>"
            "<span class='status-badge status-good'>Feed: SIP-assumed</span>"
            f"<span class='status-badge {freshness_class}'>{freshness_text}</span>"
            f"<span class='status-badge'>Cycles24h: {int(hs['cycles_24h'])}</span>"
            f"<span class='status-badge'>Orders24h: {int(hs['order_events_24h'])}</span>"
            f"<span class='status-badge'>LastCycleDay: {last_day}</span>"
            f"<span class='status-badge'>ActiveProfile: {latest_profile}</span>"
            f"<span class='status-badge mode-chip'>{mode_text}</span>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _target_allocations_table(state_df: pd.DataFrame) -> pd.DataFrame:
    target = _state_value(state_df, "switch_last_final_target", {})
    if not isinstance(target, dict) or not target:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for symbol, weight in target.items():
        try:
            w = float(weight)
        except Exception:
            continue
        rows.append(
            {
                "symbol": str(symbol).upper(),
                "target_weight": w,
                "target_weight_pct": w * 100.0,
            }
        )
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows).sort_values("target_weight", ascending=False).reset_index(drop=True)
    return out


def _target_allocations_chart(state_df: pd.DataFrame) -> alt.Chart | None:
    alloc = _target_allocations_table(state_df)
    if alloc.empty:
        return None
    return (
        alt.Chart(alloc)
        .mark_bar(cornerRadiusTopLeft=5, cornerRadiusTopRight=5, opacity=0.9)
        .encode(
            x=alt.X("symbol:N", sort="-y", title="Symbol"),
            y=alt.Y("target_weight_pct:Q", title="Target Weight %"),
            color=alt.Color(
                "symbol:N",
                legend=None,
                scale=alt.Scale(range=["#00c805", "#52db66", "#9bec74", "#aab4be", "#f3b13c", "#ff5d7a"]),
            ),
            tooltip=["symbol:N", alt.Tooltip("target_weight_pct:Q", format=".2f")],
        )
        .properties(height=250)
        .configure_axis(
            labelColor="#afc3e6",
            titleColor="#d8e6ff",
            gridColor="rgba(159,179,213,0.2)",
        )
        .configure_view(strokeOpacity=0)
    )


def _estimated_positions_table(events_df: pd.DataFrame, state_df: pd.DataFrame) -> pd.DataFrame:
    orders = _orders_table(events_df)
    if orders.empty:
        return pd.DataFrame()
    orders = orders.dropna(subset=["symbol"]).copy()
    if orders.empty:
        return pd.DataFrame()
    orders["symbol"] = orders["symbol"].astype(str).str.upper()
    orders["qty"] = pd.to_numeric(orders["qty"], errors="coerce").fillna(0.0)
    orders["buy_qty"] = orders.apply(lambda r: float(r["qty"]) if str(r["side"]).lower() == "buy" else 0.0, axis=1)
    orders["sell_qty"] = orders.apply(lambda r: float(r["qty"]) if str(r["side"]).lower() == "sell" else 0.0, axis=1)

    grouped = (
        orders.groupby("symbol", as_index=False)
        .agg(
            buy_qty=("buy_qty", "sum"),
            sell_qty=("sell_qty", "sum"),
            last_ts=("ts", "max"),
            event_count=("id", "count"),
        )
        .sort_values("event_count", ascending=False)
    )
    grouped["net_qty_est"] = grouped["buy_qty"] - grouped["sell_qty"]

    alloc = _target_allocations_table(state_df)
    target_map = {row["symbol"]: float(row["target_weight_pct"]) for _, row in alloc.iterrows()} if not alloc.empty else {}
    grouped["target_weight_pct"] = grouped["symbol"].map(lambda s: target_map.get(str(s).upper()))

    columns = [
        "symbol",
        "net_qty_est",
        "buy_qty",
        "sell_qty",
        "target_weight_pct",
        "event_count",
        "last_ts",
    ]
    return grouped[columns].reset_index(drop=True)


def _orders_by_symbol_chart(events_df: pd.DataFrame) -> alt.Chart | None:
    orders = _orders_table(events_df)
    if orders.empty:
        return None
    frame = orders.dropna(subset=["symbol"]).copy()
    if frame.empty:
        return None
    grouped = frame.groupby("symbol", as_index=False).size().rename(columns={"size": "count"})
    return (
        alt.Chart(grouped)
        .mark_bar(cornerRadiusTopLeft=5, cornerRadiusTopRight=5, opacity=0.92)
        .encode(
            x=alt.X("symbol:N", sort="-y", title="Symbol"),
            y=alt.Y("count:Q", title="Order Events"),
            color=alt.value("#00c805"),
            tooltip=["symbol:N", "count:Q"],
        )
        .properties(height=250)
        .configure_axis(
            labelColor="#afc3e6",
            titleColor="#d8e6ff",
            gridColor="rgba(159,179,213,0.2)",
        )
        .configure_view(strokeOpacity=0)
    )


def _variant_timeline_chart(events_df: pd.DataFrame) -> alt.Chart | None:
    if events_df.empty:
        return None
    cycles = events_df[events_df["event_type"] == "switch_cycle_complete"].copy()
    if cycles.empty:
        return None
    cycles = cycles.dropna(subset=["ts_ny"]).copy()
    if cycles.empty:
        return None
    cycles["variant"] = cycles["payload"].map(lambda p: p.get("variant") if isinstance(p, dict) else None)
    cycles = cycles.dropna(subset=["variant"])
    if cycles.empty:
        return None
    cycles = cycles.sort_values("ts_ny")
    cycles["idx"] = range(1, len(cycles) + 1)
    return (
        alt.Chart(cycles)
        .mark_line(point=True, strokeWidth=2.3)
        .encode(
            x=alt.X("ts_ny:T", title="Cycle Time (NY)"),
            y=alt.Y("idx:Q", title="Cycle Index"),
            color=alt.Color(
                "variant:N",
                scale=alt.Scale(range=["#00c805", "#ff5d7a", "#f3b13c", "#52db66"]),
                title="Variant",
            ),
            tooltip=["ts_ny:T", "variant:N", "idx:Q"],
        )
        .properties(height=250)
        .configure_axis(
            labelColor="#afc3e6",
            titleColor="#d8e6ff",
            gridColor="rgba(159,179,213,0.2)",
        )
        .configure_legend(labelColor="#d8e6ff", titleColor="#d8e6ff")
        .configure_view(strokeOpacity=0)
    )


def _threshold_trend_chart(events_df: pd.DataFrame) -> alt.Chart | None:
    cycles = _extract_cycle_events(events_df)
    if cycles.empty:
        return None
    frame = cycles.dropna(subset=["threshold_pct"])
    if frame.empty:
        return None
    return (
        alt.Chart(frame)
        .mark_line(point=True, color="#00c805", strokeWidth=2.4)
        .encode(
            x=alt.X("ts_ny:T", title="Time (NY)"),
            y=alt.Y("threshold_pct:Q", title="Threshold %"),
            tooltip=["ts_ny:T", alt.Tooltip("threshold_pct:Q", format=".3f"), "variant:N", "variant_reason:N"],
        )
        .properties(height=240)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_view(strokeOpacity=0)
    )


def _orders_submitted_trend_chart(events_df: pd.DataFrame) -> alt.Chart | None:
    cycles = _extract_cycle_events(events_df)
    if cycles.empty:
        return None
    frame = cycles.dropna(subset=["orders_submitted"])
    if frame.empty:
        return None
    return (
        alt.Chart(frame)
        .mark_bar(opacity=0.88, cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color="#52db66")
        .encode(
            x=alt.X("ts_ny:T", title="Time (NY)"),
            y=alt.Y("orders_submitted:Q", title="Orders Submitted"),
            tooltip=["ts_ny:T", "orders_submitted:Q", "variant:N"],
        )
        .properties(height=240)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_view(strokeOpacity=0)
    )


def _regime_metric_trend_chart(events_df: pd.DataFrame, metric: str, title: str, color: str) -> alt.Chart | None:
    cycles = _extract_cycle_events(events_df)
    if cycles.empty or metric not in cycles.columns:
        return None
    frame = cycles.dropna(subset=[metric]).copy()
    if frame.empty:
        return None
    return (
        alt.Chart(frame)
        .mark_line(point=True, strokeWidth=2.1, color=color)
        .encode(
            x=alt.X("ts_ny:T", title="Time (NY)"),
            y=alt.Y(f"{metric}:Q", title=title),
            tooltip=["ts_ny:T", alt.Tooltip(f"{metric}:Q", format=".3f"), "variant:N"],
        )
        .properties(height=220)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_view(strokeOpacity=0)
    )


def _state_age_table(state_df: pd.DataFrame) -> pd.DataFrame:
    if state_df.empty:
        return pd.DataFrame()
    now = pd.Timestamp.now(tz=NY_TZ)
    frame = state_df.copy()
    frame["age_min"] = frame["updated_at_ny"].map(
        lambda ts: None if pd.isna(ts) else float(max(0.0, (now - ts).total_seconds() / 60.0))
    )
    frame = frame[["key", "updated_at_ny", "age_min"]].rename(columns={"updated_at_ny": "updated_at"})
    return frame.sort_values("age_min", ascending=False)


def _order_lifecycle_table(events_df: pd.DataFrame, symbol_filter: str = "") -> pd.DataFrame:
    orders = _orders_table(events_df)
    if orders.empty:
        return pd.DataFrame()
    frame = orders.sort_values("ts").copy()
    if symbol_filter.strip():
        frame = frame[frame["symbol"].astype(str).str.upper() == symbol_filter.strip().upper()]
    if frame.empty:
        return pd.DataFrame()
    frame["ts"] = pd.to_datetime(frame["ts"], errors="coerce")
    frame = frame.dropna(subset=["ts"]).sort_values("ts").copy()
    if frame.empty:
        return pd.DataFrame()
    frame = frame.reset_index(drop=True)
    frame["step"] = frame.index + 1
    frame["event_label"] = frame["event_type"].astype(str) + " | " + frame["side"].astype(str) + " | " + frame["symbol"].astype(str)
    return frame[["step", "ts", "event_type", "event_label", "symbol", "side", "qty", "order_type", "threshold_pct"]]


def _order_lifecycle_chart(events_df: pd.DataFrame, symbol_filter: str = "", chart_type: str = "Line") -> alt.Chart | None:
    frame = _order_lifecycle_table(events_df, symbol_filter=symbol_filter)
    if frame.empty:
        return None
    mode = str(chart_type or "Line").strip().lower()
    if mode == "bar":
        return (
            alt.Chart(frame)
            .mark_bar(opacity=0.9, size=10)
            .encode(
                x=alt.X("ts:T", title="Event Time (NY)"),
                y=alt.Y("step:Q", title="Lifecycle Step"),
                color=alt.Color(
                    "event_type:N",
                    scale=alt.Scale(range=["#00c805", "#52db66", "#f3b13c", "#ff5d7a"]),
                    title="Event",
                ),
                tooltip=["step:Q", "ts:T", "event_type:N", "symbol:N", "side:N", "qty:Q", "order_type:N"],
            )
            .properties(height=280)
            .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
            .configure_legend(labelColor="#d8e6ff", titleColor="#d8e6ff")
            .configure_view(strokeOpacity=0)
        )
    return (
        alt.Chart(frame)
        .mark_line(point=True, strokeWidth=2.1)
        .encode(
            x=alt.X("ts:T", title="Event Time (NY)"),
            y=alt.Y("step:Q", title="Lifecycle Step"),
            color=alt.Color(
                "event_type:N",
                scale=alt.Scale(range=["#00c805", "#52db66", "#f3b13c", "#ff5d7a"]),
                title="Event",
            ),
            tooltip=["step:Q", "ts:T", "event_type:N", "symbol:N", "side:N", "qty:Q", "order_type:N"],
        )
        .properties(height=280)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_legend(labelColor="#d8e6ff", titleColor="#d8e6ff")
        .configure_view(strokeOpacity=0)
    )


def _symbol_universe(events_df: pd.DataFrame, state_df: pd.DataFrame) -> list[str]:
    symbols: set[str] = set()
    if not events_df.empty and "symbol" in events_df.columns:
        for s in events_df["symbol"].dropna().astype(str).tolist():
            if s.strip():
                symbols.add(s.strip().upper())

    target = _state_value(state_df, "switch_last_final_target", {})
    if isinstance(target, dict):
        for s in target.keys():
            if str(s).strip():
                symbols.add(str(s).strip().upper())

    baseline = _state_value(state_df, "switch_last_baseline_target", {})
    if isinstance(baseline, dict):
        for s in baseline.keys():
            if str(s).strip():
                symbols.add(str(s).strip().upper())
    return sorted(symbols)


def _normalized_pinned_symbols(symbols: list[str]) -> list[str]:
    valid = {str(s).strip().upper() for s in symbols if str(s).strip()}
    raw = st.session_state.get("ui_watchlist_pins", [])
    if not isinstance(raw, list):
        st.session_state["ui_watchlist_pins"] = []
        return []
    clean: list[str] = []
    seen: set[str] = set()
    for s in raw:
        sym = str(s).strip().upper()
        if (not sym) or (sym in seen):
            continue
        if valid and sym not in valid:
            continue
        seen.add(sym)
        clean.append(sym)
    if clean != raw:
        st.session_state["ui_watchlist_pins"] = clean
    return clean


def _set_watchlist_pin(symbol: str, pinned: bool) -> None:
    sym = str(symbol or "").strip().upper()
    if not sym:
        return
    current = _normalized_pinned_symbols([])
    pin_set = set(current)
    if pinned:
        pin_set.add(sym)
    else:
        pin_set.discard(sym)
    st.session_state["ui_watchlist_pins"] = sorted(pin_set)


def _events_time_col(events_df: pd.DataFrame) -> str:
    if "ts_ny" in events_df.columns:
        return "ts_ny"
    if "ts" in events_df.columns:
        return "ts"
    return ""


def _events_in_time_window(events_df: pd.DataFrame, window: str) -> pd.DataFrame:
    if events_df.empty:
        return events_df.copy()
    col = _events_time_col(events_df)
    if not col:
        return events_df.copy()
    frame = events_df.copy()
    # Normalize timestamps to NY tz for stable runtime windowing.
    frame[col] = pd.to_datetime(frame[col], errors="coerce", utc=True).dt.tz_convert(NY_TZ)
    frame = frame.dropna(subset=[col]).copy()
    if frame.empty:
        return frame
    win = str(window or "All").strip().upper()
    if win == "ALL":
        return frame.sort_values(col).copy()
    delta = TIME_WINDOW_DELTAS.get(win)
    if delta is None:
        return frame.sort_values(col).copy()
    # Runtime windows should be relative to "now", not just the latest loaded row.
    anchor = pd.Timestamp.now(tz=NY_TZ)
    cutoff = anchor - delta
    return frame[frame[col] >= cutoff].sort_values(col).copy()


def _window_scope_label(events_df: pd.DataFrame, window: str, sync_enabled: bool) -> str:
    if not sync_enabled:
        return "Range Sync OFF | All events"
    if events_df.empty:
        return f"Range {window} | no events"
    col = _events_time_col(events_df)
    if not col:
        return f"Range {window} | {len(events_df)} events"
    start_ts = events_df[col].min()
    end_ts = events_df[col].max()
    start_txt = pd.to_datetime(start_ts).strftime("%Y-%m-%d %H:%M")
    end_txt = pd.to_datetime(end_ts).strftime("%Y-%m-%d %H:%M")
    return f"Range {window} | {len(events_df)} events | {start_txt} -> {end_txt}"


def _time_window_cutoff(window: str) -> pd.Timestamp | None:
    win = str(window or "ALL").strip().upper()
    if win == "ALL":
        return None
    delta = TIME_WINDOW_DELTAS.get(win)
    if delta is None:
        return None
    return pd.Timestamp.now(tz=NY_TZ) - delta


def _apply_advanced_event_filters(
    events_df: pd.DataFrame,
    *,
    window: str,
    symbols: list[str],
    event_types: list[str],
    variants: list[str],
    sides: list[str],
    order_types: list[str],
    query: str,
) -> pd.DataFrame:
    frame = events_df.copy()
    if frame.empty:
        return frame
    if window:
        frame = _events_in_time_window(frame, window)
    if symbols and "symbol" in frame.columns:
        allowed = {str(s).strip().upper() for s in symbols if str(s).strip()}
        frame = frame[frame["symbol"].astype(str).str.upper().isin(allowed)]
    if event_types and "event_type" in frame.columns:
        allowed = {str(s).strip() for s in event_types if str(s).strip()}
        frame = frame[frame["event_type"].astype(str).isin(allowed)]
    if variants and "variant" in frame.columns:
        allowed = {str(s).strip() for s in variants if str(s).strip()}
        frame = frame[frame["variant"].astype(str).isin(allowed)]
    if sides and "side" in frame.columns:
        allowed = {str(s).strip().lower() for s in sides if str(s).strip()}
        frame = frame[frame["side"].astype(str).str.lower().isin(allowed)]
    if order_types and "order_type" in frame.columns:
        allowed = {str(s).strip().lower() for s in order_types if str(s).strip()}
        frame = frame[frame["order_type"].astype(str).str.lower().isin(allowed)]
    needle = str(query or "").strip().lower()
    if needle:
        hay_cols = [c for c in ["payload_text", "event_type", "symbol", "variant", "order_type", "side"] if c in frame.columns]
        if hay_cols:
            mask = pd.Series(False, index=frame.index)
            for col in hay_cols:
                mask = mask | frame[col].astype(str).str.lower().str.contains(needle, na=False)
            frame = frame[mask]
    return frame.copy()


def _paged_view(df: pd.DataFrame, *, key: str, default_page_size: int = 100) -> pd.DataFrame:
    if df.empty:
        return df
    compact_mode = bool(st.session_state.get("ui_mobile_compact_mode", False))
    if compact_mode:
        default_page_size = min(int(default_page_size), 50)
    sizes = [25, 50, 100, 250, 500, 1000]
    size_default = default_page_size if default_page_size in sizes else 100
    c1, c2, c3 = st.columns([1.0, 1.0, 2.2])
    with c1:
        page_size = st.selectbox(
            "Page Size",
            options=sizes,
            index=sizes.index(size_default),
            key=f"{key}_page_size",
        )
    total_pages = max(1, int((len(df) + int(page_size) - 1) // int(page_size)))
    with c2:
        page_no = st.number_input(
            "Page",
            min_value=1,
            max_value=total_pages,
            value=1,
            step=1,
            key=f"{key}_page_no",
        )
    with c3:
        st.caption(f"Rows: {len(df):,} | Page {int(page_no)} / {total_pages}")
    start = (int(page_no) - 1) * int(page_size)
    end = start + int(page_size)
    return df.iloc[start:end].copy()


@st.cache_data(ttl=120, show_spinner=False)
def _fetch_market_ohlcv(symbol: str, window: str, feed: str) -> tuple[pd.DataFrame, str]:
    sym = str(symbol or "").strip().upper()
    if not sym:
        return pd.DataFrame(), "missing_symbol"
    key = str(os.getenv("ALPACA_API_KEY", "") or "").strip()
    sec = str(os.getenv("ALPACA_API_SECRET", "") or os.getenv("ALPACA_SECRET_KEY", "") or "").strip()
    if not key or not sec:
        return pd.DataFrame(), "missing_alpaca_credentials"
    try:
        from soxl_growth.data.alpaca_data import AlpacaBarLoader, BarRequestSpec
    except Exception:
        return pd.DataFrame(), "alpaca_loader_unavailable"
    win = str(window or "1M").strip().upper()
    now = pd.Timestamp.now(tz=NY_TZ)
    if win in {"1D", "3D"}:
        timeframe = "5Min"
    elif win in {"1W", "2W"}:
        timeframe = "15Min"
    elif win in {"1M", "3M"}:
        timeframe = "1Hour"
    else:
        timeframe = "1Day"
    cutoff = _time_window_cutoff(win)
    if cutoff is None:
        cutoff = now - pd.Timedelta(days=365)
    loader = AlpacaBarLoader(key, sec)
    try:
        bars = loader.get_bars(
            BarRequestSpec(
                symbols=[sym],
                start=cutoff.to_pydatetime(),
                end=now.to_pydatetime(),
                timeframe=timeframe,
                adjustment="all",
                feed=str(feed or "sip"),
            )
        )
    except Exception as exc:
        return pd.DataFrame(), f"alpaca_fetch_error: {exc}"
    if not bars:
        return pd.DataFrame(), "no_market_bars"
    rows: list[dict[str, Any]] = []
    for b in bars:
        ts = pd.Timestamp(b.timestamp)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        rows.append(
            {
                "ts_ny": ts.tz_convert(NY_TZ),
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": float(b.volume),
            }
        )
    frame = pd.DataFrame(rows).sort_values("ts_ny").reset_index(drop=True)
    if frame.empty:
        return frame, "no_market_bars"
    vol = frame["volume"].replace(0.0, pd.NA).fillna(0.0)
    num = (frame["close"] * vol).cumsum()
    den = vol.cumsum().replace(0.0, pd.NA)
    frame["vwap"] = (num / den).fillna(frame["close"])
    return frame, "ok"


def _market_candlestick_chart(symbol: str, window: str, feed: str = "sip") -> tuple[alt.Chart | None, str]:
    frame, status = _fetch_market_ohlcv(symbol, window, feed)
    if frame.empty:
        return None, status
    frame = frame.copy()
    frame["candle_color"] = frame.apply(lambda r: "up" if float(r["close"]) >= float(r["open"]) else "down", axis=1)
    wick = (
        alt.Chart(frame)
        .mark_rule()
        .encode(
            x=alt.X("ts_ny:T", title="Time (NY)"),
            y=alt.Y("low:Q", title="Price"),
            y2="high:Q",
            color=alt.Color("candle_color:N", scale=alt.Scale(domain=["up", "down"], range=["#00c805", "#ff5d7a"]), legend=None),
            tooltip=["ts_ny:T", "open:Q", "high:Q", "low:Q", "close:Q", "volume:Q", "vwap:Q"],
        )
    )
    body = (
        alt.Chart(frame)
        .mark_bar(size=8)
        .encode(
            x="ts_ny:T",
            y="open:Q",
            y2="close:Q",
            color=alt.Color("candle_color:N", scale=alt.Scale(domain=["up", "down"], range=["#00c805", "#ff5d7a"]), legend=None),
        )
    )
    vwap_line = (
        alt.Chart(frame)
        .mark_line(color="#66d7ff", strokeWidth=1.7)
        .encode(x="ts_ny:T", y=alt.Y("vwap:Q", title="Price"))
    )
    volume = (
        alt.Chart(frame)
        .mark_bar(opacity=0.6, color="#2f8aff")
        .encode(
            x=alt.X("ts_ny:T", title="Time (NY)"),
            y=alt.Y("volume:Q", title="Volume"),
            tooltip=["ts_ny:T", "volume:Q"],
        )
        .properties(height=100)
    )
    price_panel = (wick + body + vwap_line).properties(height=300)
    return (
        alt.vconcat(price_panel, volume)
        .resolve_scale(x="shared")
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_view(strokeOpacity=0),
        status,
    )


def _tradeboard_scope_metrics(events_df: pd.DataFrame, state_df: pd.DataFrame, symbol: str) -> dict[str, Any]:
    scoped_orders = _orders_table(events_df)
    symbol_orders = _symbol_activity_df(events_df, symbol)
    col = _events_time_col(events_df)
    latest_ts_label = "-"
    if col and col in events_df.columns:
        latest_ts = pd.to_datetime(events_df[col], errors="coerce").dropna()
        if not latest_ts.empty:
            latest_ts_label = str(latest_ts.max().strftime("%Y-%m-%d %H:%M"))
    target = _state_value(state_df, "switch_last_final_target", {})
    target_count = len(target) if isinstance(target, dict) else 0
    return {
        "scope_events": int(len(events_df)),
        "scope_orders": int(len(scoped_orders)),
        "symbol_orders": int(len(symbol_orders)),
        "symbols_in_target": int(target_count),
        "latest_event_ts": latest_ts_label,
    }


def _watchlist_df(events_df: pd.DataFrame, state_df: pd.DataFrame) -> pd.DataFrame:
    symbols = _symbol_universe(events_df, state_df)
    if not symbols:
        return pd.DataFrame()

    orders = _orders_table(events_df)
    target = _target_allocations_table(state_df)
    target_map = {r["symbol"]: float(r["target_weight_pct"]) for _, r in target.iterrows()} if not target.empty else {}

    rows: list[dict[str, Any]] = []
    for sym in symbols:
        if orders.empty:
            sym_orders = pd.DataFrame()
        else:
            sym_orders = orders[orders["symbol"].astype(str).str.upper() == sym].copy()
        buy_qty = 0.0 if sym_orders.empty else float(
            pd.to_numeric(sym_orders[sym_orders["side"].astype(str).str.lower() == "buy"]["qty"], errors="coerce").fillna(0.0).sum()
        )
        sell_qty = 0.0 if sym_orders.empty else float(
            pd.to_numeric(sym_orders[sym_orders["side"].astype(str).str.lower() == "sell"]["qty"], errors="coerce").fillna(0.0).sum()
        )
        net = buy_qty - sell_qty
        last_ts = "-" if sym_orders.empty else str(sym_orders["ts"].max())
        last_side = "-" if sym_orders.empty else str(sym_orders.iloc[0]["side"]).upper()
        rows.append(
            {
                "symbol": sym,
                "target_wt_%": target_map.get(sym),
                "net_qty_est": net,
                "order_events": 0 if sym_orders.empty else int(len(sym_orders)),
                "last_side": last_side,
                "last_event_ts": last_ts,
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["activity_rank"] = out["order_events"].rank(ascending=False, method="dense")
    return out.sort_values(["order_events", "symbol"], ascending=[False, True]).reset_index(drop=True)


def _watchlist_selector(events_df: pd.DataFrame, state_df: pd.DataFrame, max_rows: int = 14) -> str:
    watch = _watchlist_df(events_df, state_df)
    if watch.empty:
        return ""
    pin_list = _normalized_pinned_symbols(watch["symbol"].astype(str).tolist())
    pin_set = set(pin_list)
    watch = watch.copy()
    watch["pinned"] = watch["symbol"].astype(str).str.upper().isin(pin_set)
    watch = watch.sort_values(["pinned", "order_events", "symbol"], ascending=[False, False, True]).reset_index(drop=True)

    pinned_only = st.checkbox("Pinned Only", key="ui_watchlist_pinned_only")
    if pinned_only:
        pinned_view = watch[watch["pinned"]].copy()
        if not pinned_view.empty:
            watch = pinned_view.reset_index(drop=True)

    symbols = watch["symbol"].astype(str).tolist()
    current = st.session_state.get("ui_selected_symbol", symbols[0])
    if current not in symbols:
        current = symbols[0]
    st.session_state.ui_selected_symbol = current

    shown = watch.head(max_rows).copy()
    labels: dict[str, str] = {}
    for _, row in shown.iterrows():
        symbol = str(row["symbol"])
        tw = row.get("target_wt_%")
        oq = row.get("order_events")
        star = "★ " if bool(row.get("pinned", False)) else ""
        labels[symbol] = f"{star}{symbol}  |  wt {('-' if pd.isna(tw) else f'{float(tw):.1f}%')}  |  ev {int(oq) if not pd.isna(oq) else 0}"

    opts = shown["symbol"].astype(str).tolist()
    idx = opts.index(st.session_state.ui_selected_symbol) if st.session_state.ui_selected_symbol in opts else 0
    selected = st.radio(
        "Watchlist",
        options=opts,
        index=idx,
        format_func=lambda s: labels.get(s, s),
        key="ui_watchlist_radio",
        label_visibility="collapsed",
    )
    st.session_state.ui_selected_symbol = selected

    pin_left, pin_right = st.columns([1.2, 1.0])
    selected_is_pinned = selected in pin_set
    with pin_left:
        if st.button(("Unpin ★" if selected_is_pinned else "Pin ★"), use_container_width=True, key=f"ui_watchlist_pin_toggle_{selected}"):
            _set_watchlist_pin(selected, pinned=not selected_is_pinned)
            st.session_state["ui_palette_status"] = f"{'Pinned' if not selected_is_pinned else 'Unpinned'} {selected}."
            st.rerun()
    with pin_right:
        if st.button("Clear Pins", use_container_width=True, key="ui_watchlist_pin_clear"):
            st.session_state["ui_watchlist_pins"] = []
            st.session_state["ui_palette_status"] = "Cleared watchlist pins."
            st.rerun()

    st.caption(f"Pinned: {len(pin_set)}")
    compact = shown[["pinned", "symbol", "target_wt_%", "net_qty_est", "order_events", "last_side"]].copy()
    compact["pinned"] = compact["pinned"].map(lambda v: "★" if bool(v) else "")
    st.dataframe(compact, use_container_width=True, hide_index=True, height=280)
    return st.session_state.ui_selected_symbol


def _render_tradeboard_command_palette(events_df: pd.DataFrame, state_df: pd.DataFrame, symbols: list[str]) -> None:
    actions = [
        "Refresh Data Now",
        "Toggle Auto Refresh",
        "Focus Highest Activity Symbol",
        "Pin Current Symbol",
        "Unpin Current Symbol",
        "Set View: Intraday Activity",
        "Set View: Lifecycle",
        "Set View: Event Mix",
        "Set Chart: Line",
        "Set Chart: Bar",
        "Set Chart: Candles",
        "Set Time Window: 1D",
        "Set Time Window: 1W",
        "Set Time Window: 1M",
        "Set Time Window: 3M",
        "Set Time Window: All",
    ]
    search = st.text_input(
        "Palette Search",
        value=str(st.session_state.get("ui_command_palette_search", "") or ""),
        placeholder="Type command name (supports fuzzy match)",
        key="ui_command_palette_search",
    )
    filtered_actions = actions
    needle = str(search or "").strip().lower()
    if needle:
        filtered_actions = [a for a in actions if needle in a.lower()]
        if not filtered_actions:
            fuzzy = difflib.get_close_matches(needle, actions, n=8, cutoff=0.2)
            filtered_actions = fuzzy if fuzzy else actions
    default_action = str(st.session_state.get("ui_command_palette_action", filtered_actions[0] if filtered_actions else actions[0]) or (filtered_actions[0] if filtered_actions else actions[0]))
    if default_action not in filtered_actions:
        default_action = filtered_actions[0]
    c1, c2 = st.columns([2.6, 0.8])
    with c1:
        action = st.selectbox(
            "Command Palette",
            options=filtered_actions,
            index=filtered_actions.index(default_action),
            key="ui_command_palette_action",
        )
    with c2:
        run = st.button("Run", use_container_width=True, key="ui_command_palette_run")
    st.caption("Command Palette 2.0: search/filter commands, then run. Keyboard target: `Ctrl/Cmd+K` in browser focus.")

    if run:
        selected_symbol = str(st.session_state.get("ui_selected_symbol", "") or "").strip().upper()
        _append_ui_audit("palette_run", {"action": action, "selected_symbol": selected_symbol})
        if action == "Refresh Data Now":
            _load_runtime_db.clear()
            st.session_state["ui_palette_status"] = "Data cache cleared. Runtime data refreshed."
            st.rerun()
        if action == "Toggle Auto Refresh":
            next_auto = not bool(st.session_state.get("ui_auto_refresh_enabled", False))
            st.session_state["ui_pending_auto_refresh_enabled"] = next_auto
            st.session_state["ui_palette_status"] = f"Auto refresh {'enabled' if next_auto else 'disabled'}."
            st.rerun()
        if action == "Focus Highest Activity Symbol":
            watch = _watchlist_df(events_df, state_df)
            if not watch.empty:
                top = str(watch.iloc[0]["symbol"]).strip().upper()
                if top:
                    st.session_state["ui_pending_selected_symbol"] = top
                    st.session_state["ui_palette_status"] = f"Focused highest activity symbol: {top}."
                    st.rerun()
            st.session_state["ui_palette_status"] = "No symbol activity data available."
            st.rerun()
        if action == "Pin Current Symbol":
            if selected_symbol:
                _set_watchlist_pin(selected_symbol, pinned=True)
                st.session_state["ui_palette_status"] = f"Pinned {selected_symbol}."
                st.rerun()
        if action == "Unpin Current Symbol":
            if selected_symbol:
                _set_watchlist_pin(selected_symbol, pinned=False)
                st.session_state["ui_palette_status"] = f"Unpinned {selected_symbol}."
                st.rerun()
        if action.startswith("Set View:"):
            view_name = action.split(":", 1)[1].strip()
            st.session_state["ui_pending_symbol_view_mode"] = view_name
            st.session_state["ui_palette_status"] = f"View changed to {view_name}."
            st.rerun()
        if action.startswith("Set Chart:"):
            chart_name = action.split(":", 1)[1].strip()
            st.session_state["ui_pending_symbol_chart_type"] = chart_name
            st.session_state["ui_palette_status"] = f"Chart type changed to {chart_name}."
            st.rerun()
        if action.startswith("Set Time Window:"):
            win = action.split(":", 1)[1].strip().upper()
            st.session_state["ui_pending_time_window"] = win
            st.session_state["ui_palette_status"] = f"Time window set to {win}."
            st.rerun()

    status = str(st.session_state.get("ui_palette_status", "") or "").strip()
    if status:
        st.caption(f"Last command: {status}")


def _symbol_activity_df(events_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    orders = _orders_table(events_df)
    if orders.empty or not symbol:
        return pd.DataFrame()
    sym_orders = orders[orders["symbol"].astype(str).str.upper() == symbol.upper()].copy()
    if sym_orders.empty:
        return pd.DataFrame()
    sym_orders["qty"] = pd.to_numeric(sym_orders["qty"], errors="coerce").fillna(0.0)
    sym_orders["signed_qty"] = sym_orders.apply(
        lambda r: float(r["qty"]) if str(r["side"]).lower() == "buy" else -float(r["qty"]),
        axis=1,
    )
    sym_orders = sym_orders.sort_values("ts").copy()
    sym_orders["cum_position"] = sym_orders["signed_qty"].cumsum()
    sym_orders["idx"] = range(1, len(sym_orders) + 1)
    return sym_orders


def _liquidity_ladder_df(events_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    frame = _symbol_activity_df(events_df, symbol)
    if frame.empty:
        return pd.DataFrame()
    frame["qty_abs"] = frame["qty"].abs()
    bins = pd.qcut(frame["qty_abs"], q=min(5, frame["qty_abs"].nunique()), duplicates="drop")
    frame["qty_bin"] = bins.astype(str)
    grouped = frame.groupby(["qty_bin", "side"], as_index=False).agg(total_qty=("qty_abs", "sum"), events=("idx", "count"))
    if grouped.empty:
        return pd.DataFrame()
    grouped = grouped.sort_values(["qty_bin", "side"], ascending=[True, True]).reset_index(drop=True)
    return grouped


def _liquidity_ladder_chart(events_df: pd.DataFrame, symbol: str) -> alt.Chart | None:
    grouped = _liquidity_ladder_df(events_df, symbol)
    if grouped.empty:
        return None
    return (
        alt.Chart(grouped)
        .mark_bar(opacity=0.9, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("qty_bin:N", title="Qty Bucket"),
            y=alt.Y("total_qty:Q", title="Total Qty"),
            color=alt.Color("side:N", scale=alt.Scale(domain=["buy", "sell"], range=["#00c805", "#ff5d7a"])),
            tooltip=["qty_bin:N", "side:N", "total_qty:Q", "events:Q"],
        )
        .properties(height=220)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_legend(labelColor="#d8e6ff", titleColor="#d8e6ff")
        .configure_view(strokeOpacity=0)
    )


def _symbol_price_event_df(events_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    orders = _orders_table(events_df)
    if orders.empty or not symbol:
        return pd.DataFrame()
    frame = orders[orders["symbol"].astype(str).str.upper() == symbol.upper()].copy()
    if frame.empty:
        return pd.DataFrame()
    for col in ["price", "fill_price", "avg_fill_price", "limit_price", "stop_price", "trigger_price", "take_profit_price", "stop_loss_price"]:
        if col not in frame.columns:
            frame[col] = None
    frame["execution_price"] = (
        pd.to_numeric(frame["fill_price"], errors="coerce")
        .fillna(pd.to_numeric(frame["avg_fill_price"], errors="coerce"))
        .fillna(pd.to_numeric(frame["price"], errors="coerce"))
        .fillna(pd.to_numeric(frame["limit_price"], errors="coerce"))
        .fillna(pd.to_numeric(frame["stop_price"], errors="coerce"))
        .fillna(pd.to_numeric(frame["trigger_price"], errors="coerce"))
        .fillna(pd.to_numeric(frame["take_profit_price"], errors="coerce"))
        .fillna(pd.to_numeric(frame["stop_loss_price"], errors="coerce"))
    )
    frame = frame.dropna(subset=["execution_price", "ts"]).copy()
    frame = frame[frame["execution_price"] > 0].copy()
    if frame.empty:
        return pd.DataFrame()
    frame["qty_num"] = pd.to_numeric(frame["qty"], errors="coerce").fillna(0.0)
    frame["bucket"] = pd.to_datetime(frame["ts"], errors="coerce").dt.floor("30min")
    frame = frame.dropna(subset=["bucket"])
    return frame


def _symbol_candlestick_chart(events_df: pd.DataFrame, symbol: str) -> alt.Chart | None:
    frame = _symbol_price_event_df(events_df, symbol)
    if frame.empty:
        return None
    ohlc = (
        frame.groupby("bucket", as_index=False)
        .agg(
            open=("execution_price", "first"),
            high=("execution_price", "max"),
            low=("execution_price", "min"),
            close=("execution_price", "last"),
            volume=("qty_num", "sum"),
        )
        .sort_values("bucket")
    )
    if ohlc.empty:
        return None
    ohlc["candle_color"] = ohlc.apply(lambda r: "up" if float(r["close"]) >= float(r["open"]) else "down", axis=1)

    wick = (
        alt.Chart(ohlc)
        .mark_rule()
        .encode(
            x=alt.X("bucket:T", title="Time (NY, 30m buckets)"),
            y=alt.Y("low:Q", title="Price"),
            y2="high:Q",
            color=alt.Color(
                "candle_color:N",
                scale=alt.Scale(domain=["up", "down"], range=["#00c805", "#ff5d7a"]),
                legend=None,
            ),
            tooltip=[
                "bucket:T",
                alt.Tooltip("open:Q", format=".4f"),
                alt.Tooltip("high:Q", format=".4f"),
                alt.Tooltip("low:Q", format=".4f"),
                alt.Tooltip("close:Q", format=".4f"),
                alt.Tooltip("volume:Q", format=",.2f"),
            ],
        )
    )
    body = (
        alt.Chart(ohlc)
        .mark_bar(size=9)
        .encode(
            x="bucket:T",
            y=alt.Y("open:Q", title="Price"),
            y2="close:Q",
            color=alt.Color(
                "candle_color:N",
                scale=alt.Scale(domain=["up", "down"], range=["#00c805", "#ff5d7a"]),
                legend=None,
            ),
            tooltip=[
                "bucket:T",
                alt.Tooltip("open:Q", format=".4f"),
                alt.Tooltip("high:Q", format=".4f"),
                alt.Tooltip("low:Q", format=".4f"),
                alt.Tooltip("close:Q", format=".4f"),
                alt.Tooltip("volume:Q", format=",.2f"),
            ],
        )
    )
    return (
        (wick + body)
        .properties(height=360)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_view(strokeOpacity=0)
    )


def _symbol_activity_chart(events_df: pd.DataFrame, symbol: str, chart_type: str = "Line") -> alt.Chart | None:
    frame = _symbol_activity_df(events_df, symbol)
    if frame.empty:
        return None
    chart_mode = str(chart_type or "Line").strip().lower()
    if chart_mode == "bar":
        bar_frame = frame.copy()
        bar_frame["side_label"] = bar_frame["side"].astype(str).str.lower().map(
            lambda s: "buy" if s == "buy" else ("sell" if s == "sell" else "other")
        )
        bar_frame["ts_parsed"] = pd.to_datetime(bar_frame["ts"], errors="coerce")
        bar_frame = bar_frame.dropna(subset=["ts_parsed"]).copy()
        if bar_frame.empty:
            return None
        bar_frame = bar_frame.sort_values("ts_parsed").copy()
        bar_frame["ts_label"] = bar_frame["ts_parsed"].dt.strftime("%Y-%m-%d %H:%M")

        # If too many unique timestamps, aggregate to daily buckets so bars remain visible.
        if int(bar_frame["ts_label"].nunique()) > 90:
            bar_frame["bucket"] = bar_frame["ts_parsed"].dt.strftime("%Y-%m-%d")
            plot_frame = (
                bar_frame.groupby(["bucket", "side_label"], as_index=False)
                .agg(
                    signed_qty=("signed_qty", "sum"),
                    first_ts=("ts_parsed", "min"),
                    events=("id", "count"),
                )
                .sort_values("first_ts")
            )
            x_title = "Date Bucket (NY)"
            tooltip_fields: list[Any] = [
                "bucket:N",
                "side_label:N",
                alt.Tooltip("signed_qty:Q", format=",.6f"),
                "events:Q",
            ]
        else:
            plot_frame = bar_frame.copy()
            plot_frame["bucket"] = plot_frame["ts_label"]
            plot_frame["first_ts"] = plot_frame["ts_parsed"]
            x_title = "Time (NY)"
            tooltip_fields = [
                "ts:T",
                "event_type:N",
                "side:N",
                "qty:Q",
                "signed_qty:Q",
                "cum_position:Q",
                "order_type:N",
            ]

        return (
            alt.Chart(plot_frame)
            .mark_bar(opacity=0.92, size=10)
            .encode(
                x=alt.X(
                    "bucket:N",
                    title=x_title,
                    sort=alt.SortField("first_ts", order="ascending"),
                    axis=alt.Axis(labelAngle=-35, labelLimit=130),
                ),
                y=alt.Y("signed_qty:Q", title="Filled Qty (Signed)"),
                color=alt.Color(
                    "side_label:N",
                    title="Side",
                    scale=alt.Scale(domain=["buy", "sell", "other"], range=["#00c805", "#ff5d7a", "#aab4be"]),
                ),
                tooltip=tooltip_fields,
            )
            .properties(height=360)
            .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
            .configure_legend(labelColor="#d8e6ff", titleColor="#d8e6ff")
            .configure_view(strokeOpacity=0)
        )
    return (
        alt.Chart(frame)
        .mark_line(point=True, strokeWidth=2.4)
        .encode(
            x=alt.X("ts:T", title="Time (NY)"),
            y=alt.Y("cum_position:Q", title="Estimated Net Position"),
            color=alt.value("#00c805"),
            tooltip=["ts:T", "event_type:N", "side:N", "qty:Q", "cum_position:Q", "order_type:N"],
        )
        .properties(height=360)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_view(strokeOpacity=0)
    )


def _symbol_event_mix_chart(events_df: pd.DataFrame, symbol: str) -> alt.Chart | None:
    frame = _symbol_activity_df(events_df, symbol)
    if frame.empty:
        return None
    grouped = frame.groupby("event_type", as_index=False).size().rename(columns={"size": "count"})
    return (
        alt.Chart(grouped)
        .mark_bar(opacity=0.9, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("event_type:N", sort="-y", title="Event"),
            y=alt.Y("count:Q", title="Count"),
            color=alt.Color("event_type:N", legend=None),
            tooltip=["event_type:N", "count:Q"],
        )
        .properties(height=220)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_view(strokeOpacity=0)
    )


def _symbol_summary(events_df: pd.DataFrame, state_df: pd.DataFrame, symbol: str) -> dict[str, Any]:
    wl = _watchlist_df(events_df, state_df)
    if wl.empty or not symbol:
        return {}
    row = wl[wl["symbol"] == symbol.upper()]
    if row.empty:
        return {}
    r = row.iloc[0]
    return {
        "symbol": symbol.upper(),
        "target_wt_%": r.get("target_wt_%"),
        "net_qty_est": r.get("net_qty_est"),
        "order_events": r.get("order_events"),
        "last_side": r.get("last_side"),
        "last_event_ts": r.get("last_event_ts"),
    }


def _orders_table(events_df: pd.DataFrame) -> pd.DataFrame:
    if events_df.empty:
        return pd.DataFrame()

    order_events = events_df[
        events_df["event_type"].isin(
            ["switch_rebalance_order", "switch_profit_lock_close", "switch_profit_lock_intraday_close"]
        )
    ].copy()

    if order_events.empty:
        return pd.DataFrame()

    cols = [
        "id",
        "ts_ny",
        "event_type",
        "symbol",
        "side",
        "qty",
        "price",
        "fill_price",
        "avg_fill_price",
        "limit_price",
        "stop_price",
        "trigger_price",
        "order_type",
        "variant",
        "threshold_pct",
        "take_profit_price",
        "stop_loss_price",
        "broker_order_id",
        "client_order_id",
        "order_status",
        "submitted_at",
        "filled_at",
        "canceled_at",
        "profile",
        "variant_reason",
    ]
    for col in cols:
        if col not in order_events.columns:
            order_events[col] = None

    order_events = order_events[cols].rename(columns={"ts_ny": "ts"})
    # Keep both timestamp labels for compatibility across workspace widgets.
    if "ts" in order_events.columns and "ts_ny" not in order_events.columns:
        order_events["ts_ny"] = order_events["ts"]
    order_events = order_events.sort_values("id", ascending=False)
    return order_events


def _execution_quality_frame(orders: pd.DataFrame) -> pd.DataFrame:
    if orders.empty:
        return pd.DataFrame()
    frame = orders.copy().sort_values("ts").reset_index(drop=True)
    frame["qty_num"] = pd.to_numeric(frame["qty"], errors="coerce").fillna(0.0)
    frame["abs_qty"] = frame["qty_num"].abs()
    frame["side_norm"] = frame["side"].astype(str).str.lower().map(lambda s: "buy" if s == "buy" else ("sell" if s == "sell" else "other"))
    frame["order_type_norm"] = frame["order_type"].astype(str).str.lower().replace({"nan": "unknown", "none": "unknown"})
    frame["ts_parsed"] = pd.to_datetime(frame["ts"], errors="coerce")
    frame["gap_sec"] = frame["ts_parsed"].diff().dt.total_seconds()
    frame["exec_price"] = (
        pd.to_numeric(frame["fill_price"], errors="coerce")
        .fillna(pd.to_numeric(frame["avg_fill_price"], errors="coerce"))
        .fillna(pd.to_numeric(frame["price"], errors="coerce"))
        .fillna(pd.to_numeric(frame["limit_price"], errors="coerce"))
        .fillna(pd.to_numeric(frame["stop_price"], errors="coerce"))
        .fillna(pd.to_numeric(frame["trigger_price"], errors="coerce"))
    )
    return frame


def _orders_blotter_enriched(orders: pd.DataFrame) -> pd.DataFrame:
    frame = _execution_quality_frame(orders)
    if frame.empty:
        return pd.DataFrame()
    frame = frame.copy()
    frame["ts_parsed"] = pd.to_datetime(frame["ts"], errors="coerce")
    frame["submitted_parsed"] = pd.to_datetime(frame.get("submitted_at"), errors="coerce")
    frame["filled_parsed"] = pd.to_datetime(frame.get("filled_at"), errors="coerce")
    frame["fill_latency_sec"] = (frame["filled_parsed"] - frame["submitted_parsed"]).dt.total_seconds()
    frame["reference_price"] = (
        pd.to_numeric(frame.get("trigger_price"), errors="coerce")
        .fillna(pd.to_numeric(frame.get("limit_price"), errors="coerce"))
        .fillna(pd.to_numeric(frame.get("stop_price"), errors="coerce"))
        .fillna(pd.to_numeric(frame.get("price"), errors="coerce"))
    )
    side_dir = frame["side_norm"].map(lambda s: 1.0 if s == "buy" else (-1.0 if s == "sell" else 0.0))
    # Positive value means adverse slippage against side.
    frame["adverse_slippage_bps"] = (
        (frame["exec_price"] - frame["reference_price"]) / frame["reference_price"].replace(0, pd.NA) * side_dir * 10000.0
    )
    frame["symbol_norm"] = frame["symbol"].astype(str).str.upper()
    frame["variant_norm"] = frame["variant"].astype(str).replace({"": "unknown", "nan": "unknown", "None": "unknown"})
    frame["notional_abs"] = frame["exec_price"].fillna(0.0) * frame["abs_qty"].fillna(0.0)
    frame["signed_notional"] = frame["notional_abs"] * frame["side_norm"].map(lambda s: 1.0 if s == "buy" else (-1.0 if s == "sell" else 0.0))
    return frame


def _execution_slippage_summary(orders: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = _orders_blotter_enriched(orders)
    if frame.empty:
        return pd.DataFrame(), {}
    s = frame.dropna(subset=["adverse_slippage_bps"]).copy()
    if s.empty:
        return pd.DataFrame(), {}
    by_symbol = (
        s.groupby("symbol_norm", as_index=False)
        .agg(
            events=("id", "count"),
            avg_adverse_bps=("adverse_slippage_bps", "mean"),
            p95_adverse_bps=("adverse_slippage_bps", lambda x: float(pd.Series(x).quantile(0.95))),
            avg_fill_latency_sec=("fill_latency_sec", "mean"),
        )
        .sort_values("events", ascending=False)
    )
    summary = {
        "rows": int(len(s)),
        "avg_adverse_bps": float(s["adverse_slippage_bps"].mean()),
        "p95_adverse_bps": float(s["adverse_slippage_bps"].quantile(0.95)),
        "avg_fill_latency_sec": float(s["fill_latency_sec"].dropna().mean()) if s["fill_latency_sec"].dropna().size else None,
    }
    return by_symbol.reset_index(drop=True), summary


def _pnl_attribution_v2(orders: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frame = _orders_blotter_enriched(orders)
    if frame.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    frame = frame.copy()
    frame["day_ny"] = pd.to_datetime(frame["ts_parsed"], errors="coerce").dt.strftime("%Y-%m-%d")
    by_symbol = (
        frame.groupby("symbol_norm", as_index=False)
        .agg(
            events=("id", "count"),
            buy_notional=("signed_notional", lambda x: float(pd.Series(x)[pd.Series(x) > 0].sum())),
            sell_notional=("signed_notional", lambda x: float((-pd.Series(x)[pd.Series(x) < 0]).sum())),
            net_notional=("signed_notional", "sum"),
            abs_notional=("notional_abs", "sum"),
        )
        .sort_values("abs_notional", ascending=False)
    )
    by_variant = (
        frame.groupby("variant_norm", as_index=False)
        .agg(
            events=("id", "count"),
            buy_notional=("signed_notional", lambda x: float(pd.Series(x)[pd.Series(x) > 0].sum())),
            sell_notional=("signed_notional", lambda x: float((-pd.Series(x)[pd.Series(x) < 0]).sum())),
            net_notional=("signed_notional", "sum"),
            abs_notional=("notional_abs", "sum"),
        )
        .sort_values("abs_notional", ascending=False)
    )
    by_day = (
        frame.groupby("day_ny", as_index=False)
        .agg(
            events=("id", "count"),
            buy_notional=("signed_notional", lambda x: float(pd.Series(x)[pd.Series(x) > 0].sum())),
            sell_notional=("signed_notional", lambda x: float((-pd.Series(x)[pd.Series(x) < 0]).sum())),
            net_notional=("signed_notional", "sum"),
            abs_notional=("notional_abs", "sum"),
        )
        .sort_values("day_ny")
    )
    return by_symbol.reset_index(drop=True), by_variant.reset_index(drop=True), by_day.reset_index(drop=True)


def _target_vs_event_drift_table(events_df: pd.DataFrame, state_df: pd.DataFrame) -> pd.DataFrame:
    target = _target_allocations_table(state_df).copy()
    pos = _estimated_positions_table(events_df, state_df).copy()
    if target.empty and pos.empty:
        return pd.DataFrame()
    if target.empty:
        target = pd.DataFrame(columns=["symbol", "target_weight_pct"])
    if pos.empty:
        pos = pd.DataFrame(columns=["symbol", "net_qty_est"])
    pos["symbol"] = pos["symbol"].astype(str).str.upper()
    target["symbol"] = target["symbol"].astype(str).str.upper()
    total_abs = float(pos["net_qty_est"].astype(float).abs().sum()) if not pos.empty else 0.0
    if total_abs > 0:
        pos["event_exposure_pct"] = (pos["net_qty_est"].astype(float).abs() / total_abs) * 100.0
    else:
        pos["event_exposure_pct"] = 0.0
    out = target[["symbol", "target_weight_pct"]].merge(pos[["symbol", "event_exposure_pct", "net_qty_est"]], on="symbol", how="outer")
    out["target_weight_pct"] = pd.to_numeric(out["target_weight_pct"], errors="coerce").fillna(0.0)
    out["event_exposure_pct"] = pd.to_numeric(out["event_exposure_pct"], errors="coerce").fillna(0.0)
    out["net_qty_est"] = pd.to_numeric(out["net_qty_est"], errors="coerce").fillna(0.0)
    out["abs_drift_pct"] = (out["event_exposure_pct"] - out["target_weight_pct"]).abs()
    out["drift_direction"] = out["event_exposure_pct"] - out["target_weight_pct"]
    return out.sort_values("abs_drift_pct", ascending=False).reset_index(drop=True)


def _orders_blotter_filters(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    c1, c2, c3, c4 = st.columns(4)
    symbols = sorted([s for s in frame["symbol_norm"].dropna().astype(str).unique().tolist() if s])
    sides = sorted([s for s in frame["side_norm"].dropna().astype(str).unique().tolist() if s])
    order_types = sorted([s for s in frame["order_type_norm"].dropna().astype(str).unique().tolist() if s])
    ev_types = sorted([s for s in frame["event_type"].dropna().astype(str).unique().tolist() if s])
    with c1:
        f_symbols = st.multiselect("Blotter Symbols", options=symbols, default=symbols, key="exec_blotter_symbols")
    with c2:
        f_sides = st.multiselect("Blotter Sides", options=sides, default=sides, key="exec_blotter_sides")
    with c3:
        f_otypes = st.multiselect("Blotter Order Types", options=order_types, default=order_types, key="exec_blotter_order_types")
    with c4:
        f_evt = st.multiselect("Blotter Event Types", options=ev_types, default=ev_types, key="exec_blotter_event_types")
    q = st.text_input("Blotter Search", placeholder="symbol / order_id / status / variant", key="exec_blotter_search")
    out = frame.copy()
    if f_symbols:
        out = out[out["symbol_norm"].isin(set(f_symbols))]
    if f_sides:
        out = out[out["side_norm"].isin(set(f_sides))]
    if f_otypes:
        out = out[out["order_type_norm"].isin(set(f_otypes))]
    if f_evt:
        out = out[out["event_type"].isin(set(f_evt))]
    needle = str(q or "").strip().lower()
    if needle:
        hay = pd.Series(False, index=out.index)
        for col in ["symbol_norm", "event_type", "order_type_norm", "order_status", "variant_norm", "broker_order_id", "client_order_id"]:
            if col in out.columns:
                hay = hay | out[col].astype(str).str.lower().str.contains(needle, na=False)
        out = out[hay]
    return out


def _execution_quality_metrics(orders: pd.DataFrame) -> dict[str, Any]:
    frame = _execution_quality_frame(orders)
    if frame.empty:
        return {}
    total = int(len(frame))
    buys = int((frame["side_norm"] == "buy").sum())
    sells = int((frame["side_norm"] == "sell").sum())
    avg_qty = float(frame["abs_qty"].mean()) if total > 0 else None
    med_qty = float(frame["abs_qty"].median()) if total > 0 else None
    price_cov = float(frame["exec_price"].notna().mean() * 100.0) if total > 0 else None
    med_gap = float(frame["gap_sec"].dropna().median()) if frame["gap_sec"].dropna().size > 0 else None
    p95_gap = float(frame["gap_sec"].dropna().quantile(0.95)) if frame["gap_sec"].dropna().size > 0 else None
    sym = frame["symbol"].astype(str).str.upper().value_counts()
    top_symbol = str(sym.index[0]) if not sym.empty else "-"
    top_symbol_share = float((sym.iloc[0] / total) * 100.0) if not sym.empty else None
    return {
        "total_orders": total,
        "buy_orders": buys,
        "sell_orders": sells,
        "avg_abs_qty": avg_qty,
        "median_abs_qty": med_qty,
        "price_coverage_pct": price_cov,
        "median_interarrival_sec": med_gap,
        "p95_interarrival_sec": p95_gap,
        "top_symbol": top_symbol,
        "top_symbol_share_pct": top_symbol_share,
    }


def _execution_quality_order_type_chart(orders: pd.DataFrame) -> alt.Chart | None:
    frame = _execution_quality_frame(orders)
    if frame.empty:
        return None
    grouped = frame.groupby("order_type_norm", as_index=False).size().rename(columns={"size": "count"})
    if grouped.empty:
        return None
    return (
        alt.Chart(grouped)
        .mark_bar(opacity=0.9, cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color="#66d7ff")
        .encode(
            x=alt.X("order_type_norm:N", sort="-y", title="Order Type"),
            y=alt.Y("count:Q", title="Count"),
            tooltip=["order_type_norm:N", "count:Q"],
        )
        .properties(height=220)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_view(strokeOpacity=0)
    )


def _execution_quality_interarrival_chart(orders: pd.DataFrame) -> alt.Chart | None:
    frame = _execution_quality_frame(orders)
    if frame.empty:
        return None
    gaps = frame["gap_sec"].dropna()
    if gaps.empty:
        return None
    gdf = pd.DataFrame({"gap_sec": gaps})
    return (
        alt.Chart(gdf)
        .mark_bar(opacity=0.9, color="#f3b13c")
        .encode(
            x=alt.X("gap_sec:Q", bin=alt.Bin(maxbins=24), title="Inter-arrival Seconds"),
            y=alt.Y("count()", title="Frequency"),
            tooltip=[alt.Tooltip("count()", title="count")],
        )
        .properties(height=220)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_view(strokeOpacity=0)
    )


def _execution_quality_symbol_table(orders: pd.DataFrame) -> pd.DataFrame:
    frame = _execution_quality_frame(orders)
    if frame.empty:
        return pd.DataFrame()
    grouped = (
        frame.assign(symbol_norm=frame["symbol"].astype(str).str.upper())
        .groupby("symbol_norm", as_index=False)
        .agg(
            orders=("id", "count"),
            abs_qty_total=("abs_qty", "sum"),
            avg_abs_qty=("abs_qty", "mean"),
            buy_orders=("side_norm", lambda s: int((s == "buy").sum())),
            sell_orders=("side_norm", lambda s: int((s == "sell").sum())),
        )
        .rename(columns={"symbol_norm": "symbol"})
        .sort_values("orders", ascending=False)
    )
    return grouped.reset_index(drop=True)


def _broker_vs_event_position_table(events_df: pd.DataFrame, broker_positions: list[dict[str, Any]]) -> pd.DataFrame:
    est = _estimated_positions_table(events_df, pd.DataFrame())
    est_map: dict[str, float] = {}
    if not est.empty:
        for _, r in est.iterrows():
            sym = str(r.get("symbol", "")).strip().upper()
            if not sym:
                continue
            est_map[sym] = float(pd.to_numeric(r.get("net_qty_est"), errors="coerce") or 0.0)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in broker_positions or []:
        sym = str(p.get("symbol", "")).strip().upper()
        if not sym:
            continue
        seen.add(sym)
        bqty = float(pd.to_numeric(p.get("qty"), errors="coerce") or 0.0)
        eqty = float(est_map.get(sym, 0.0))
        rows.append(
            {
                "symbol": sym,
                "broker_qty": bqty,
                "event_qty_est": eqty,
                "qty_drift": bqty - eqty,
                "market_value": float(pd.to_numeric(p.get("market_value"), errors="coerce") or 0.0),
                "avg_entry_price": float(pd.to_numeric(p.get("avg_entry_price"), errors="coerce") or 0.0),
                "unrealized_plpc_pct": float(pd.to_numeric(p.get("unrealized_plpc"), errors="coerce") or 0.0) * 100.0,
            }
        )
    for sym, eqty in est_map.items():
        if sym in seen:
            continue
        rows.append(
            {
                "symbol": sym,
                "broker_qty": 0.0,
                "event_qty_est": float(eqty),
                "qty_drift": -float(eqty),
                "market_value": 0.0,
                "avg_entry_price": 0.0,
                "unrealized_plpc_pct": 0.0,
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values("symbol").reset_index(drop=True)


def _execution_notional_attribution(orders: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frame = _execution_quality_frame(orders)
    if frame.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    frame = frame.copy()
    frame["symbol_norm"] = frame["symbol"].astype(str).str.upper()
    frame["variant_norm"] = frame["variant"].astype(str).replace({"": "unknown", "nan": "unknown", "None": "unknown"})
    frame["action_norm"] = frame["event_type"].astype(str)
    frame["signed_notional"] = frame["exec_price"].fillna(0.0) * frame["qty_num"].fillna(0.0) * frame["side_norm"].map(
        lambda s: 1.0 if s == "buy" else (-1.0 if s == "sell" else 0.0)
    )
    frame["abs_notional"] = frame["signed_notional"].abs()
    by_symbol = (
        frame.groupby("symbol_norm", as_index=False)
        .agg(events=("id", "count"), abs_notional=("abs_notional", "sum"), net_notional=("signed_notional", "sum"))
        .rename(columns={"symbol_norm": "bucket"})
        .sort_values("abs_notional", ascending=False)
    )
    by_variant = (
        frame.groupby("variant_norm", as_index=False)
        .agg(events=("id", "count"), abs_notional=("abs_notional", "sum"), net_notional=("signed_notional", "sum"))
        .rename(columns={"variant_norm": "bucket"})
        .sort_values("abs_notional", ascending=False)
    )
    by_action = (
        frame.groupby("action_norm", as_index=False)
        .agg(events=("id", "count"), abs_notional=("abs_notional", "sum"), net_notional=("signed_notional", "sum"))
        .rename(columns={"action_norm": "bucket"})
        .sort_values("abs_notional", ascending=False)
    )
    return by_symbol.reset_index(drop=True), by_variant.reset_index(drop=True), by_action.reset_index(drop=True)


def _attribution_bar_chart(df: pd.DataFrame, title: str) -> alt.Chart | None:
    if df.empty:
        return None
    frame = df.copy().head(20)
    return (
        alt.Chart(frame)
        .mark_bar(opacity=0.9)
        .encode(
            x=alt.X("bucket:N", sort="-y", title=title),
            y=alt.Y("abs_notional:Q", title="Abs Notional (proxy)"),
            color=alt.Color("net_notional:Q", scale=alt.Scale(scheme="redyellowgreen"), title="Net Notional"),
            tooltip=["bucket:N", "events:Q", alt.Tooltip("abs_notional:Q", format=",.2f"), alt.Tooltip("net_notional:Q", format=",.2f")],
        )
        .properties(height=250)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_view(strokeOpacity=0)
    )


def _intraday_session_heatmap(events_df: pd.DataFrame) -> alt.Chart | None:
    if events_df.empty or "ts_ny" not in events_df.columns:
        return None
    frame = events_df.dropna(subset=["ts_ny"]).copy()
    if frame.empty:
        return None
    ts = pd.to_datetime(frame["ts_ny"], errors="coerce")
    frame["hour"] = ts.dt.hour
    frame["weekday"] = ts.dt.day_name().fillna("Unknown")
    frame["event"] = frame["event_type"].astype(str)
    grouped = frame.groupby(["weekday", "hour"], as_index=False).size().rename(columns={"size": "count"})
    if grouped.empty:
        return None
    weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Unknown"]
    return (
        alt.Chart(grouped)
        .mark_rect()
        .encode(
            x=alt.X("hour:O", title="Hour (NY)"),
            y=alt.Y("weekday:N", sort=weekday_order, title="Weekday"),
            color=alt.Color("count:Q", scale=alt.Scale(scheme="teals"), title="Event Count"),
            tooltip=["weekday:N", "hour:O", "count:Q"],
        )
        .properties(height=230)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff")
        .configure_view(strokeOpacity=0)
    )


def _what_changed_rows(events_df: pd.DataFrame) -> pd.DataFrame:
    if events_df.empty:
        return pd.DataFrame()
    cycles = events_df[events_df["event_type"] == "switch_cycle_complete"].copy()
    if cycles.empty:
        return pd.DataFrame()
    cycles = cycles.sort_values("ts_ny", ascending=False).reset_index(drop=True)
    if len(cycles) < 2:
        return pd.DataFrame()
    cur = cycles.iloc[0].get("payload")
    prev = cycles.iloc[1].get("payload")
    if not isinstance(cur, dict) or not isinstance(prev, dict):
        return pd.DataFrame()
    keys = [
        "day",
        "variant",
        "variant_reason",
        "threshold_pct",
        "intent_count",
        "orders_submitted",
        "profit_lock_order_type",
        "rebalance_order_type",
        "inverse_note",
    ]
    rows: list[dict[str, Any]] = []
    for k in keys:
        cv = cur.get(k)
        pv = prev.get(k)
        if str(cv) != str(pv):
            rows.append({"field": k, "current": cv, "previous": pv})
    return pd.DataFrame(rows)


def _incident_timeline_frame(events_df: pd.DataFrame, notices_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if not events_df.empty:
        interesting = events_df[
            events_df["event_type"].isin(
                [
                    "switch_variant_changed",
                    "switch_rebalance_order",
                    "switch_profit_lock_close",
                    "switch_profit_lock_intraday_close",
                    "switch_cycle_complete",
                ]
            )
        ].copy()
        for _, r in interesting.iterrows():
            rows.append(
                {
                    "ts": pd.to_datetime(r.get("ts_ny"), errors="coerce"),
                    "source": "event",
                    "kind": str(r.get("event_type", "")),
                    "severity": "info",
                    "symbol": str(r.get("symbol", "") or ""),
                    "detail": str(r.get("payload_text", "") or "")[:220],
                }
            )
    if notices_df is not None and (not notices_df.empty):
        for _, r in notices_df.iterrows():
            rows.append(
                {
                    "ts": pd.to_datetime(r.get("ts"), errors="coerce"),
                    "source": "notice",
                    "kind": str(r.get("title", "")),
                    "severity": str(r.get("severity", "info")),
                    "symbol": "",
                    "detail": str(r.get("detail", "")),
                }
            )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out.dropna(subset=["ts"]).sort_values("ts", ascending=False).reset_index(drop=True)
    return out


def _execution_timeline_frame(orders: pd.DataFrame, symbol_filter: str = "") -> pd.DataFrame:
    if orders.empty:
        return pd.DataFrame()
    frame = orders.copy()
    if symbol_filter.strip():
        frame = frame[frame["symbol"].astype(str).str.upper() == symbol_filter.strip().upper()]
    if frame.empty:
        return pd.DataFrame()
    frame["ts_parsed"] = pd.to_datetime(frame["ts"], errors="coerce")
    frame = frame.dropna(subset=["ts_parsed"]).sort_values("ts_parsed").copy()
    if frame.empty:
        return pd.DataFrame()
    frame["symbol_norm"] = frame["symbol"].astype(str).str.upper()
    frame["event_lane"] = frame["event_type"].astype(str) + " | " + frame["side"].astype(str)
    frame["latency_sec"] = frame.groupby("symbol_norm")["ts_parsed"].diff().dt.total_seconds()
    if "broker_order_id" not in frame.columns:
        frame["broker_order_id"] = None
    if "client_order_id" not in frame.columns:
        frame["client_order_id"] = None
    return frame


def _execution_timeline_chart(orders: pd.DataFrame, symbol_filter: str = "") -> alt.Chart | None:
    frame = _execution_timeline_frame(orders, symbol_filter=symbol_filter)
    if frame.empty:
        return None
    return (
        alt.Chart(frame)
        .mark_circle(size=95, opacity=0.9)
        .encode(
            x=alt.X("ts_parsed:T", title="Event Time (NY)"),
            y=alt.Y("event_lane:N", title="Lifecycle Lane"),
            color=alt.Color("symbol_norm:N", title="Symbol"),
            shape=alt.Shape("side:N", title="Side"),
            tooltip=[
                "ts_parsed:T",
                "symbol_norm:N",
                "event_type:N",
                "side:N",
                "order_type:N",
                "qty:Q",
                alt.Tooltip("latency_sec:Q", format=".2f"),
                "broker_order_id:N",
                "client_order_id:N",
            ],
        )
        .properties(height=300)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_legend(labelColor="#d8e6ff", titleColor="#d8e6ff")
        .configure_view(strokeOpacity=0)
    )


def _cycle_metrics_table(events_df: pd.DataFrame) -> pd.DataFrame:
    if events_df.empty:
        return pd.DataFrame()

    cycles = events_df[events_df["event_type"] == "switch_cycle_complete"].copy()
    if cycles.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for _, row in cycles.iterrows():
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        metrics = payload.get("regime_metrics", {}) if isinstance(payload.get("regime_metrics"), dict) else {}
        rows.append(
            {
                "id": row.get("id"),
                "ts": row.get("ts_ny"),
                "day": payload.get("day"),
                "variant": payload.get("variant"),
                "variant_reason": payload.get("variant_reason"),
                "threshold_pct": payload.get("threshold_pct"),
                "intent_count": payload.get("intent_count"),
                "orders_submitted": payload.get("orders_submitted"),
                "rv20_ann": metrics.get("rv20_ann"),
                "crossovers20": metrics.get("crossovers20"),
                "dd20_pct": metrics.get("dd20_pct"),
                "slope20_pct": metrics.get("slope20_pct"),
                "slope60_pct": metrics.get("slope60_pct"),
                "close": metrics.get("close"),
                "ma20": metrics.get("ma20"),
                "ma60": metrics.get("ma60"),
                "ma200": metrics.get("ma200"),
            }
        )

    return pd.DataFrame(rows).sort_values("id", ascending=False)


def _variant_changes_table(events_df: pd.DataFrame) -> pd.DataFrame:
    if events_df.empty:
        return pd.DataFrame()

    changes = events_df[events_df["event_type"] == "switch_variant_changed"].copy()
    if changes.empty:
        return pd.DataFrame()

    out = pd.DataFrame(
        {
            "id": changes["id"],
            "ts": changes["ts_ny"],
            "from": changes["payload"].map(lambda p: p.get("from") if isinstance(p, dict) else None),
            "to": changes["payload"].map(lambda p: p.get("to") if isinstance(p, dict) else None),
            "reason": changes["payload"].map(lambda p: p.get("reason") if isinstance(p, dict) else None),
        }
    )
    return out.sort_values("id", ascending=False)


def _render_trader_desk_tab(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-caption'>Trading desk view: allocation intent, event-derived exposure, and switch behavior timeline.</div>",
        unsafe_allow_html=True,
    )

    latest_profile = _state_value(state_df, "switch_last_profile", "-")
    latest_variant = _state_value(state_df, "switch_last_variant", "-")
    last_final_target = _state_value(state_df, "switch_last_final_target", {})
    target_count = len(last_final_target) if isinstance(last_final_target, dict) else 0
    latest_order_type = "-"
    if not events_df.empty and "order_type" in events_df.columns:
        t = events_df["order_type"].dropna()
        if not t.empty:
            latest_order_type = str(t.iloc[0])

    st.markdown(
        (
            "<div class='stat-chip-row'>"
            f"<span class='stat-chip'>profile={latest_profile}</span>"
            f"<span class='stat-chip'>variant={latest_variant}</span>"
            f"<span class='stat-chip'>symbols_in_target={target_count}</span>"
            f"<span class='stat-chip'>last_order_type={latest_order_type}</span>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )

    top_left, top_right = st.columns([1.05, 1.25])
    with top_left:
        st.subheader("Target Allocation (Latest)")
        alloc_chart = _target_allocations_chart(state_df)
        if alloc_chart is None:
            st.info("No target allocation in state yet (`switch_last_final_target`).")
        else:
            st.altair_chart(alloc_chart, use_container_width=True)
    with top_right:
        st.subheader("Variant Timeline")
        vt_chart = _variant_timeline_chart(events_df)
        if vt_chart is None:
            st.info("No `switch_cycle_complete` variant timeline available yet.")
        else:
            st.altair_chart(vt_chart, use_container_width=True)

    mid_left, mid_right = st.columns([1.2, 1.0])
    with mid_left:
        st.subheader("Estimated Positions (Event-Derived)")
        pos_df = _estimated_positions_table(events_df, state_df)
        if pos_df.empty:
            st.info("No order events yet to infer event-derived positions.")
        else:
            st.dataframe(pos_df, use_container_width=True, hide_index=True)
            st.caption("Event-derived exposure estimate from logged buy/sell quantities; broker position truth remains source-of-truth.")
    with mid_right:
        st.subheader("Order Events by Symbol")
        o_chart = _orders_by_symbol_chart(events_df)
        if o_chart is None:
            st.info("No order events available yet.")
        else:
            st.altair_chart(o_chart, use_container_width=True)

    st.markdown("</div>", unsafe_allow_html=True)


def _render_terminal_workspace(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-caption'>Primary tradeboard for symbol focus, execution timing, and event-level position validation.</div>",
        unsafe_allow_html=True,
    )

    symbols = _symbol_universe(events_df, state_df)
    if not symbols:
        st.info("No symbols in current runtime state/events yet.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    _apply_terminal_pending_ui_state(symbols)

    default_symbol = symbols[0]
    if "ui_selected_symbol" not in st.session_state or st.session_state.ui_selected_symbol not in symbols:
        st.session_state.ui_selected_symbol = default_symbol

    top_ctrl_left, top_ctrl_right = st.columns([1.0, 2.2])
    with top_ctrl_left:
        jump_symbol = st.selectbox(
            "Quick Symbol Jump",
            options=symbols,
            index=symbols.index(st.session_state.ui_selected_symbol),
            key="ui_symbol_jump_select",
        )
        st.session_state.ui_selected_symbol = jump_symbol
    with top_ctrl_right:
        view_col, chart_col = st.columns([1.5, 1.0])
        with view_col:
            st.radio(
                "View",
                options=["Intraday Activity", "Lifecycle", "Event Mix"],
                horizontal=True,
                key="ui_symbol_view_mode",
            )
        with chart_col:
            current_view = str(st.session_state.get("ui_symbol_view_mode", "Intraday Activity") or "Intraday Activity")
            if current_view == "Intraday Activity":
                chart_options = ["Line", "Bar", "Candles"]
            elif current_view == "Lifecycle":
                chart_options = ["Line", "Bar"]
            else:
                chart_options = ["Bar"]
            current_chart = str(st.session_state.get("ui_symbol_chart_type", "Line") or "Line")
            if current_chart not in chart_options:
                current_chart = chart_options[0]
                st.session_state["ui_symbol_chart_type"] = current_chart
            st.selectbox(
                "Chart Type",
                options=chart_options,
                index=chart_options.index(current_chart),
                key="ui_symbol_chart_type",
                disabled=len(chart_options) == 1,
            )
    preset_cols = st.columns([0.55, 0.55, 0.55, 0.55, 0.55, 2.25])
    for idx, preset in enumerate(["1D", "1W", "1M", "3M", "All"]):
        with preset_cols[idx]:
            if st.button(preset, use_container_width=True, key=f"ui_tw_preset_{preset}"):
                st.session_state["ui_time_window"] = preset
                st.session_state["ui_palette_status"] = f"Time window set to {preset}."
                st.rerun()
    sync_left, sync_mid, sync_right = st.columns([1.1, 1.0, 2.0])
    with sync_left:
        current_window = str(st.session_state.get("ui_time_window", "1D") or "1D")
        if current_window not in TIME_WINDOW_OPTIONS:
            current_window = "1D"
        st.selectbox("Time Window", options=list(TIME_WINDOW_OPTIONS), index=list(TIME_WINDOW_OPTIONS).index(current_window), key="ui_time_window")
    with sync_mid:
        st.checkbox("Sync Charts", value=bool(st.session_state.get("ui_sync_charts", True)), key="ui_sync_charts")
    with sync_right:
        _render_tradeboard_command_palette(events_df, state_df, symbols)

    selected_symbol = st.session_state.ui_selected_symbol
    sync_enabled = bool(st.session_state.get("ui_sync_charts", True))
    window = str(st.session_state.get("ui_time_window", "1D") or "1D")
    scoped_events_df = _events_in_time_window(events_df, window) if sync_enabled else events_df.copy()
    summary = _symbol_summary(scoped_events_df, state_df, selected_symbol)
    st.caption(_window_scope_label(scoped_events_df, window=window, sync_enabled=sync_enabled))
    kpi = _tradeboard_scope_metrics(scoped_events_df, state_df, selected_symbol)
    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        st.metric("Scope Events", f"{int(kpi.get('scope_events', 0)):,}")
    with k2:
        st.metric("Scope Orders", f"{int(kpi.get('scope_orders', 0)):,}")
    with k3:
        st.metric(f"{selected_symbol} Orders", f"{int(kpi.get('symbol_orders', 0)):,}")
    with k4:
        st.metric("Target Symbols", f"{int(kpi.get('symbols_in_target', 0)):,}")
    with k5:
        st.metric("Latest Event (NY)", str(kpi.get("latest_event_ts", "-")))

    left, center, right = st.columns([0.8, 1.95, 0.75])

    with left:
        st.markdown("<div class='terminal-shell'>", unsafe_allow_html=True)
        st.markdown("<div class='terminal-title'>Watchlist</div>", unsafe_allow_html=True)
        selected_symbol = _watchlist_selector(events_df, state_df, max_rows=12) or selected_symbol
        st.session_state.ui_selected_symbol = selected_symbol
        st.markdown("</div>", unsafe_allow_html=True)

    with center:
        st.markdown("<div class='terminal-shell-hero'>", unsafe_allow_html=True)
        st.markdown(f"<div class='rh-hero-symbol'>{selected_symbol}</div>", unsafe_allow_html=True)
        chart_type = st.session_state.get("ui_symbol_chart_type", "Line")
        st.markdown(
            f"<div class='rh-hero-sub'>aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m_switch_v1 | {st.session_state.get('ui_symbol_view_mode', 'Intraday Activity')} | {chart_type} | {window}{' sync' if sync_enabled else ' all'}</div>",
            unsafe_allow_html=True,
        )
        mode = st.session_state.get("ui_symbol_view_mode", "Intraday Activity")
        if mode == "Intraday Activity":
            if str(chart_type).strip().lower() == "candles":
                feed = str(os.getenv("ALPACA_DATA_FEED", "sip") or "sip").strip().lower()
                chart, market_status = _market_candlestick_chart(selected_symbol, window=window, feed=feed)
                if chart is not None:
                    st.caption(f"Market candles source: Alpaca `{feed}` ({window}) with VWAP overlay.")
                else:
                    st.caption(f"Market candles unavailable ({market_status}). Falling back to execution-derived candles.")
                    chart = _symbol_candlestick_chart(scoped_events_df, selected_symbol)
                if chart is None and sync_enabled:
                    chart = _symbol_candlestick_chart(events_df, selected_symbol)
                    if chart is not None:
                        st.caption("No candle-priced events in selected time window; showing full available range for symbol.")
                if chart is None:
                    # Runtime market-order events often omit explicit fill prices. Keep the
                    # panel useful by falling back to execution-activity bars.
                    chart = _symbol_activity_chart(scoped_events_df, selected_symbol, chart_type="Bar")
                    if chart is None and sync_enabled:
                        chart = _symbol_activity_chart(events_df, selected_symbol, chart_type="Bar")
                    if chart is not None:
                        st.caption("Candles unavailable (no priced execution events). Showing execution bars instead.")
            else:
                chart = _symbol_activity_chart(scoped_events_df, selected_symbol, chart_type=chart_type)
        elif mode == "Lifecycle":
            chart = _order_lifecycle_chart(scoped_events_df, symbol_filter=selected_symbol, chart_type=chart_type)
            if chart is None:
                chart = _order_lifecycle_chart(scoped_events_df, symbol_filter="", chart_type=chart_type)
                if chart is not None:
                    st.caption("No lifecycle events for selected symbol in this range; showing all symbols.")
        else:
            chart = _symbol_event_mix_chart(scoped_events_df, selected_symbol)

        if chart is None:
            st.info("No chartable execution data for selected symbol yet.")
        else:
            st.altair_chart(chart, use_container_width=True)
        export_a, export_b = st.columns([1.0, 1.0])
        with export_a:
            scoped_export = scoped_events_df.copy()
            if not scoped_export.empty:
                for tcol in ["ts_ny", "ts"]:
                    if tcol in scoped_export.columns:
                        scoped_export[tcol] = pd.to_datetime(scoped_export[tcol], errors="coerce").astype(str)
            st.download_button(
                "Export Scope Events CSV",
                data=scoped_export.to_csv(index=False).encode("utf-8"),
                file_name=f"tradeboard_scope_{window.lower()}_{selected_symbol}.csv",
                mime="text/csv",
                use_container_width=True,
                key=f"ui_tradeboard_export_scope_{selected_symbol}_{window}",
            )
        with export_b:
            symbol_export = _symbol_activity_df(scoped_events_df, selected_symbol).copy()
            if not symbol_export.empty and "ts" in symbol_export.columns:
                symbol_export["ts"] = pd.to_datetime(symbol_export["ts"], errors="coerce").astype(str)
            st.download_button(
                f"Export {selected_symbol} Executions CSV",
                data=symbol_export.to_csv(index=False).encode("utf-8"),
                file_name=f"tradeboard_exec_{selected_symbol}_{window.lower()}.csv",
                mime="text/csv",
                use_container_width=True,
                key=f"ui_tradeboard_export_symbol_{selected_symbol}_{window}",
            )
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div class='terminal-shell'>", unsafe_allow_html=True)
        st.markdown("<div class='terminal-title'>Recent Executions</div>", unsafe_allow_html=True)
        sym_table = _symbol_activity_df(scoped_events_df, selected_symbol)
        if not sym_table.empty:
            cols = ["ts", "event_type", "side", "qty", "order_type", "cum_position"]
            for c in cols:
                if c not in sym_table.columns:
                    sym_table[c] = None
            st.dataframe(sym_table[cols].sort_values("ts", ascending=False), use_container_width=True, hide_index=True, height=220)
        else:
            st.info("No executions available.")
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown("<div class='terminal-shell'>", unsafe_allow_html=True)
        st.markdown("<div class='terminal-title'>Key Stats</div>", unsafe_allow_html=True)
        if summary:
            def _fmt(v: Any) -> str:
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    return "-"
                if isinstance(v, float):
                    return f"{v:.3f}"
                return str(v)

            st.markdown(
                (
                    "<div class='rh-stats-grid'>"
                    f"<div class='rh-stat'><div class='rh-stat-k'>Symbol</div><div class='rh-stat-v'>{_fmt(summary.get('symbol'))}</div></div>"
                    f"<div class='rh-stat'><div class='rh-stat-k'>Target Wt %</div><div class='rh-stat-v'>{_fmt(summary.get('target_wt_%'))}</div></div>"
                    f"<div class='rh-stat'><div class='rh-stat-k'>Net Qty (est)</div><div class='rh-stat-v'>{_fmt(summary.get('net_qty_est'))}</div></div>"
                    f"<div class='rh-stat'><div class='rh-stat-k'>Order Events</div><div class='rh-stat-v'>{_fmt(summary.get('order_events'))}</div></div>"
                    "</div>"
                ),
                unsafe_allow_html=True,
            )
        else:
            st.info("No symbol summary data.")

        st.markdown(
            (
                "<div class='rh-action-row'>"
                "<span class='rh-action-chip'>Paper Mode</span>"
                "<span class='rh-action-chip'>Risk Guard</span>"
                "<span class='rh-action-chip'>Auto Switch</span>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div class='terminal-shell'>", unsafe_allow_html=True)
        st.markdown("<div class='terminal-title'>Liquidity Ladder (Event Buckets)</div>", unsafe_allow_html=True)
        ladder = _liquidity_ladder_chart(scoped_events_df, selected_symbol)
        if ladder is None:
            st.info("No ladder data for selected symbol.")
        else:
            st.altair_chart(ladder, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='terminal-shell'>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='terminal-title'>Portfolio / Positions (Event-Derived{', Range Scoped' if sync_enabled else ''})</div>",
        unsafe_allow_html=True,
    )
    pos_df = _estimated_positions_table(scoped_events_df, state_df)
    if pos_df.empty:
        st.info("No event-derived position data available.")
    else:
        st.dataframe(pos_df, use_container_width=True, hide_index=True, height=240)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


def _render_live_overview_workspace(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-caption'>Portfolio pulse view: allocation posture, activity heartbeat, and real-time operator alerts.</div>",
        unsafe_allow_html=True,
    )

    alerts = _generate_operator_alerts(events_df, state_df)
    if alerts:
        st.subheader("Active Alerts")
        for a in alerts[:5]:
            sev = a["severity"]
            color = "status-bad" if sev == "high" else "status-warn"
            st.markdown(
                (
                    "<div class='alert-card'>"
                    f"<div class='alert-title'><span class='status-badge {color}'>{sev.upper()}</span> {a['title']}</div>"
                    f"<div class='alert-text'>{a['detail']}</div>"
                    "</div>"
                ),
                unsafe_allow_html=True,
            )
    else:
        st.success("No active operator alerts. Runtime flow appears healthy.")

    c1, c2 = st.columns([1.05, 1.2])
    with c1:
        st.subheader("Target Allocation")
        ac = _target_allocations_chart(state_df)
        if ac is None:
            st.info("No target allocation available yet.")
        else:
            st.altair_chart(ac, use_container_width=True)
    with c2:
        st.subheader("Event Pulse (Hourly)")
        ec = _events_by_hour_chart(events_df)
        if ec is None:
            st.info("No event pulse data available yet.")
        else:
            st.altair_chart(ec, use_container_width=True)

    c3, c4 = st.columns([1.25, 1.0])
    with c3:
        st.subheader("Estimated Positions (Event-Derived)")
        pos_df = _estimated_positions_table(events_df, state_df)
        if pos_df.empty:
            st.info("No event-derived position snapshot available yet.")
        else:
            st.dataframe(pos_df, use_container_width=True, hide_index=True)
    with c4:
        st.subheader("Event Distribution")
        dc = _event_type_distribution(events_df)
        if dc is None:
            st.info("No event distribution available yet.")
        else:
            st.altair_chart(dc, use_container_width=True)

    st.subheader("Latest Cycle Snapshot")
    payload = _latest_cycle_payload(events_df)
    if payload is None:
        st.info("No `switch_cycle_complete` payload found yet.")
    else:
        st.json(payload, expanded=False)

    with st.expander("What Changed Since Last Cycle", expanded=False):
        wc = _what_changed_rows(events_df)
        if wc.empty:
            st.info("No comparable cycle delta yet.")
        else:
            st.dataframe(wc, use_container_width=True, hide_index=True)

    with st.expander("Intraday Session Heatmap", expanded=False):
        hm = _intraday_session_heatmap(events_df)
        if hm is None:
            st.info("No session heatmap data available.")
        else:
            st.altair_chart(hm, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_execution_orders_workspace(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-caption'>Execution workspace: order blotter, lifecycle timeline, and manual action planning.</div>",
        unsafe_allow_html=True,
    )
    orders = _orders_table(events_df)
    if orders.empty:
        st.info("No order/exit events found yet.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    orders_enriched = _orders_blotter_enriched(orders)

    with st.expander("Pre-Trade Risk Guard Panel", expanded=False):
        st.caption("UI guardrails before placing orders. This does not mutate runtime logic.")
        c1, c2, c3 = st.columns(3)
        with c1:
            max_concentration = st.number_input("Max Symbol Exposure %", min_value=1.0, max_value=100.0, value=80.0, step=1.0, key="risk_max_symbol_exposure")
        with c2:
            max_abs_drift = st.number_input("Max Target Drift %", min_value=0.5, max_value=100.0, value=20.0, step=0.5, key="risk_max_abs_drift")
        with c3:
            max_adverse_bps = st.number_input("Max Avg Adverse Slippage (bps)", min_value=0.0, max_value=2000.0, value=60.0, step=1.0, key="risk_max_adverse_bps")
        drift = _target_vs_event_drift_table(events_df, state_df)
        slip_tbl, slip_sum = _execution_slippage_summary(orders)
        max_exp = float(drift["event_exposure_pct"].max()) if (not drift.empty) else 0.0
        max_drift = float(drift["abs_drift_pct"].max()) if (not drift.empty) else 0.0
        avg_adv = float(slip_sum.get("avg_adverse_bps", 0.0) or 0.0) if slip_sum else 0.0
        g1, g2, g3 = st.columns(3)
        g1.metric("Current Max Exposure %", f"{max_exp:.2f}")
        g2.metric("Current Max Drift %", f"{max_drift:.2f}")
        g3.metric("Current Avg Adverse bps", f"{avg_adv:.2f}")
        rule_rows = [
            {
                "guardrail": "Symbol exposure",
                "current": round(max_exp, 4),
                "limit": round(float(max_concentration), 4),
                "status": "PASS" if max_exp <= float(max_concentration) else "BREACH",
            },
            {
                "guardrail": "Target drift",
                "current": round(max_drift, 4),
                "limit": round(float(max_abs_drift), 4),
                "status": "PASS" if max_drift <= float(max_abs_drift) else "BREACH",
            },
            {
                "guardrail": "Avg adverse slippage bps",
                "current": round(avg_adv, 4),
                "limit": round(float(max_adverse_bps), 4),
                "status": "PASS" if avg_adv <= float(max_adverse_bps) else "BREACH",
            },
        ]
        st.dataframe(pd.DataFrame(rule_rows), use_container_width=True, hide_index=True)

    with st.expander("Real Broker Orders + Position Truth", expanded=False):
        c0, c1, c2 = st.columns([1.0, 1.0, 1.0])
        with c0:
            broker_mode = st.selectbox("Broker Mode", options=["paper", "live"], index=0, key="exec_broker_mode")
        with c1:
            broker_feed = st.selectbox("Broker Feed", options=["sip", "iex"], index=0, key="exec_broker_feed")
        with c2:
            if st.button("Refresh Broker Snapshot", use_container_width=True, key="exec_refresh_broker"):
                _load_broker_runtime_snapshot.clear()
                _append_ui_audit("refresh_broker_snapshot", {"mode": broker_mode, "feed": broker_feed})
                st.rerun()
        snap = _load_broker_runtime_snapshot(mode=broker_mode, data_feed=broker_feed)
        if str(snap.get("status")) != "ok":
            st.warning(f"Broker snapshot unavailable: {snap.get('detail')}")
        else:
            acct = snap.get("account", {}) if isinstance(snap.get("account", {}), dict) else {}
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Broker Equity", f"${float(pd.to_numeric(acct.get('equity'), errors='coerce') or 0.0):,.2f}")
            m2.metric("Cash", f"${float(pd.to_numeric(acct.get('cash'), errors='coerce') or 0.0):,.2f}")
            m3.metric("Buying Power", f"${float(pd.to_numeric(acct.get('buying_power'), errors='coerce') or 0.0):,.2f}")
            m4.metric("Open Orders", f"{len(snap.get('open_orders', []) or []):,}")
            open_orders_df = pd.DataFrame(snap.get("open_orders", []) or [])
            if not open_orders_df.empty:
                st.subheader("Broker Open Orders")
                st.dataframe(_paged_view(open_orders_df, key="broker_open_orders", default_page_size=50), use_container_width=True, hide_index=True)
            else:
                st.caption("No broker open orders.")
            pos = list(snap.get("positions", []) or [])
            drift = _broker_vs_event_position_table(events_df, pos)
            st.subheader("Broker vs Event-Derived Position Drift")
            if drift.empty:
                st.info("No comparable position data.")
            else:
                st.dataframe(drift, use_container_width=True, hide_index=True)

    with st.expander("Live PnL Attribution (Execution Proxy)", expanded=False):
        st.caption("Proxy attribution from execution notional (fill/price-derived), grouped by symbol/variant/action.")
        by_symbol, by_variant, by_action = _execution_notional_attribution(orders)
        c1, c2, c3 = st.columns(3)
        with c1:
            ch = _attribution_bar_chart(by_symbol, "Symbol")
            if ch is None:
                st.info("No symbol attribution data.")
            else:
                st.altair_chart(ch, use_container_width=True)
        with c2:
            ch = _attribution_bar_chart(by_variant, "Variant")
            if ch is None:
                st.info("No variant attribution data.")
            else:
                st.altair_chart(ch, use_container_width=True)
        with c3:
            ch = _attribution_bar_chart(by_action, "Action Type")
            if ch is None:
                st.info("No action attribution data.")
            else:
                st.altair_chart(ch, use_container_width=True)
        t1, t2, t3 = st.columns(3)
        with t1:
            st.dataframe(by_symbol, use_container_width=True, hide_index=True)
        with t2:
            st.dataframe(by_variant, use_container_width=True, hide_index=True)
        with t3:
            st.dataframe(by_action, use_container_width=True, hide_index=True)

    left, right = st.columns([1.15, 1.0])
    with left:
        st.subheader("Advanced Orders Blotter")
        filtered = _orders_blotter_filters(orders_enriched if not orders_enriched.empty else orders)
        if filtered.empty:
            st.info("No rows after blotter filters.")
        else:
            show_cols = [
                c
                for c in [
                    "id",
                    "ts",
                    "symbol_norm",
                    "event_type",
                    "side_norm",
                    "qty_num",
                    "exec_price",
                    "reference_price",
                    "adverse_slippage_bps",
                    "fill_latency_sec",
                    "order_type_norm",
                    "order_status",
                    "broker_order_id",
                    "client_order_id",
                    "variant_norm",
                ]
                if c in filtered.columns
            ]
            st.dataframe(_paged_view(filtered[show_cols], key="exec_blotter", default_page_size=100), use_container_width=True, hide_index=True)
            st.download_button(
                "Download Filtered Blotter CSV",
                data=filtered.to_csv(index=False).encode("utf-8"),
                file_name="execution_blotter_filtered.csv",
                mime="text/csv",
                use_container_width=True,
                key="exec_blotter_export",
            )
    with right:
        st.subheader("Order Events by Symbol")
        symbol_chart = _orders_by_symbol_chart(events_df)
        if symbol_chart is None:
            st.info("No symbol breakdown available yet.")
        else:
            st.altair_chart(symbol_chart, use_container_width=True)

    st.subheader("Execution Timeline (Lifecycle Lanes)")
    timeline_cols = st.columns([1.1, 2.2])
    with timeline_cols[0]:
        sym_opts = ["All"] + sorted([str(s) for s in orders["symbol"].dropna().astype(str).str.upper().unique().tolist()])
        tl_symbol = st.selectbox("Timeline Symbol", options=sym_opts, index=0, key="exec_timeline_symbol")
        tl_filter = "" if tl_symbol == "All" else tl_symbol
    with timeline_cols[1]:
        timeline = _execution_timeline_chart(orders, symbol_filter=tl_filter)
        if timeline is None:
            st.info("No timeline points available.")
        else:
            st.altair_chart(timeline, use_container_width=True)
    timeline_tbl = _execution_timeline_frame(orders, symbol_filter=("" if tl_symbol == "All" else tl_symbol))
    if not timeline_tbl.empty:
        cols = ["ts_parsed", "symbol_norm", "event_type", "side", "order_type", "qty", "latency_sec", "broker_order_id", "client_order_id"]
        for c in cols:
            if c not in timeline_tbl.columns:
                timeline_tbl[c] = None
        st.dataframe(
            _paged_view(timeline_tbl[cols].sort_values("ts_parsed", ascending=False), key="exec_timeline_tbl", default_page_size=50),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Lifecycle Inspector")
    symbols = sorted([str(s) for s in orders["symbol"].dropna().astype(str).str.upper().unique().tolist()])
    selected_symbol = st.selectbox("Focus Symbol (optional)", options=["All"] + symbols, index=0)
    selected = "" if selected_symbol == "All" else selected_symbol
    lc = _order_lifecycle_chart(events_df, symbol_filter=selected)
    if lc is None:
        st.info("Lifecycle timeline unavailable for selection.")
    else:
        st.altair_chart(lc, use_container_width=True)
    lt = _order_lifecycle_table(events_df, symbol_filter=selected)
    if not lt.empty:
        st.dataframe(
            _paged_view(lt.sort_values("step", ascending=False), key="exec_lifecycle_tbl", default_page_size=50),
            use_container_width=True,
            hide_index=True,
        )

    with st.expander("Trade Detail Drawer", expanded=False):
        st.caption("Deep inspection for one order/exit event with estimated before/after position impact.")
        ts_col = "ts_ny" if "ts_ny" in orders.columns else ("ts" if "ts" in orders.columns else "")
        if not ts_col:
            st.info("No timestamp column available for trade detail inspection.")
            ts_col = "ts"
        order_rows = orders.copy().sort_values(ts_col).reset_index(drop=True)
        selector_opts = [
            f"id={int(row.get('id'))} | {str(row.get(ts_col))} | {str(row.get('symbol'))} | {str(row.get('side'))} | qty={str(row.get('qty'))}"
            for _, row in order_rows.iterrows()
        ]
        if not selector_opts:
            st.info("No trade events available for inspection.")
        else:
            pick = st.selectbox("Select Event", options=selector_opts, index=len(selector_opts) - 1, key="trade_drawer_pick")
            picked_id = int(str(pick).split("|", 1)[0].replace("id=", "").strip())
            picked = order_rows[order_rows["id"] == picked_id]
            if picked.empty:
                st.info("Selected event not found.")
            else:
                row = picked.iloc[0]
                payload = row.get("payload")
                symbol = str(row.get("symbol") or "").strip().upper()
                ts_sel = row.get(ts_col)

                def _signed_qty(side_value: Any, qty_value: Any) -> float:
                    q = _safe_float(qty_value) or 0.0
                    s = str(side_value or "").strip().lower()
                    return q if s in {"buy", "long"} else (-q if s in {"sell", "short"} else 0.0)

                prior = order_rows[(order_rows["symbol"].astype(str).str.upper() == symbol) & (order_rows[ts_col] < ts_sel)]
                upto = order_rows[(order_rows["symbol"].astype(str).str.upper() == symbol) & (order_rows[ts_col] <= ts_sel)]
                before_qty = float(sum(_signed_qty(r.side, r.qty) for r in prior.itertuples()))
                after_qty = float(sum(_signed_qty(r.side, r.qty) for r in upto.itertuples()))
                st.write(
                    {
                        "id": int(row.get("id")),
                        "ts_ny": str(ts_sel),
                        "event_type": str(row.get("event_type")),
                        "symbol": symbol,
                        "side": str(row.get("side")),
                        "qty": _safe_float(row.get("qty")),
                        "order_type": str(row.get("order_type")),
                        "profile": str(row.get("profile")),
                        "variant": str(row.get("variant")),
                        "variant_reason": str(row.get("variant_reason")),
                        "estimated_position_before_qty": round(before_qty, 6),
                        "estimated_position_after_qty": round(after_qty, 6),
                    }
                )
                if isinstance(payload, dict):
                    st.json(payload, expanded=False)
                else:
                    st.code(str(row.get("payload_text", ""))[:2000], language="text")

    with st.expander("Execution Quality", expanded=False):
        st.caption("Quality diagnostics for execution pacing, order-type mix, quantity profile, and symbol concentration.")
        q = _execution_quality_metrics(orders)
        if not q:
            st.info("No execution quality data available.")
        else:
            q1, q2, q3, q4, q5 = st.columns(5)
            q1.metric("Orders", str(int(q.get("total_orders", 0))))
            q2.metric("Buy/Sell", f"{int(q.get('buy_orders', 0))}/{int(q.get('sell_orders', 0))}")
            q3.metric("Avg Abs Qty", "-" if q.get("avg_abs_qty") is None else f"{float(q['avg_abs_qty']):.2f}")
            q4.metric("Median Gap", "-" if q.get("median_interarrival_sec") is None else f"{float(q['median_interarrival_sec']):.1f}s")
            q5.metric("Price Coverage", "-" if q.get("price_coverage_pct") is None else f"{float(q['price_coverage_pct']):.1f}%")

            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Order Type Mix")
                otc = _execution_quality_order_type_chart(orders)
                if otc is None:
                    st.info("Order type mix unavailable.")
                else:
                    st.altair_chart(otc, use_container_width=True)
            with c2:
                st.subheader("Inter-arrival Distribution")
                itc = _execution_quality_interarrival_chart(orders)
                if itc is None:
                    st.info("Inter-arrival chart unavailable.")
                else:
                    st.altair_chart(itc, use_container_width=True)

            st.subheader("Symbol Execution Mix")
            sym_mix = _execution_quality_symbol_table(orders)
            if sym_mix.empty:
                st.info("Symbol-level execution mix unavailable.")
            else:
                st.dataframe(sym_mix, use_container_width=True, hide_index=True)
            st.subheader("Execution Quality Slippage")
            sl_tbl, sl_summary = _execution_slippage_summary(orders)
            if not sl_summary:
                st.info("Slippage summary unavailable (missing reference/exec prices).")
            else:
                qx1, qx2, qx3 = st.columns(3)
                qx1.metric("Rows with Slippage", f"{int(sl_summary.get('rows', 0)):,}")
                qx2.metric("Avg Adverse (bps)", f"{float(sl_summary.get('avg_adverse_bps', 0.0)):.2f}")
                qx3.metric("P95 Adverse (bps)", f"{float(sl_summary.get('p95_adverse_bps', 0.0)):.2f}")
                if sl_summary.get("avg_fill_latency_sec") is not None:
                    st.caption(f"Average fill latency: {float(sl_summary['avg_fill_latency_sec']):.2f}s")
                st.dataframe(sl_tbl, use_container_width=True, hide_index=True)

    # UI-side staged actions only; all destructive actions must still be executed via runtime CLI/backend controls.
    with st.expander("Action Planner (Safe UI Preview, No Direct Execution)", expanded=False):
        action = st.selectbox(
            "Planned Action",
            ["Cancel All Open Orders", "Flatten All Positions", "Pause Runtime Loop", "Resume Runtime Loop"],
            index=0,
        )
        ack = st.text_input("Type CONFIRM to generate suggested command", value="")
        if st.button("Generate Suggested Command", use_container_width=True):
            if ack.strip().upper() != "CONFIRM":
                st.warning("Type `CONFIRM` to generate command preview.")
            else:
                cmds = {
                    "Cancel All Open Orders": "Use broker/API bulk cancel path from runtime controls (paper/live safe flow).",
                    "Flatten All Positions": "Use broker/API flatten path with extra confirmation and audit logging.",
                    "Pause Runtime Loop": "Stop process/session running `runtime_switch_loop.py` (tmux/systemd).",
                    "Resume Runtime Loop": "Start `runtime_switch_loop.py` with your locked profile and env file.",
                }
                st.code(cmds[action], language="text")
    st.markdown("</div>", unsafe_allow_html=True)


def _render_backtest_runtime_compare_panel(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    with st.expander("Backtest vs Runtime Comparison", expanded=False):
        curve = _equity_curve_frame(events_df, state_df)
        snap = _pnl_snapshot(curve, state_df)
        runtime_row = pd.DataFrame(
            [
                {
                    "source": "runtime_db",
                    "start_equity": snap.get("start_equity"),
                    "end_equity": snap.get("end_equity"),
                    "pnl": snap.get("pnl"),
                    "return_pct": snap.get("return_pct"),
                    "max_drawdown_pct": snap.get("max_drawdown_pct"),
                }
            ]
        )
        st.dataframe(runtime_row, use_container_width=True, hide_index=True)

        reports_root = Path(__file__).resolve().parents[2] / "composer_original" / "reports"
        candidates: list[Path] = []
        if reports_root.exists():
            candidates.extend(sorted(reports_root.glob("*.csv")))
            candidates.extend(sorted(reports_root.glob("*.json")))
        if not candidates:
            st.info("No report files detected under composer_original/reports.")
            return

        selected = st.selectbox(
            "Select report file",
            options=[str(p) for p in candidates],
            index=0,
            key="compare_report_file",
        )
        path = Path(selected)
        st.caption(f"Loaded report: `{path.name}`")
        try:
            if path.suffix.lower() == ".csv":
                rep = pd.read_csv(path)
                if rep.empty:
                    st.info("Selected CSV report is empty.")
                else:
                    st.dataframe(rep.head(30), use_container_width=True, hide_index=True)
            else:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    rep = pd.DataFrame(data)
                    st.dataframe(rep.head(30), use_container_width=True, hide_index=True)
                elif isinstance(data, dict):
                    # Flatten one level for readable side-by-side comparison.
                    flat_rows: list[dict[str, Any]] = []
                    for k, v in data.items():
                        if isinstance(v, dict):
                            row = {"section": k}
                            row.update(v)
                            flat_rows.append(row)
                        else:
                            flat_rows.append({"section": k, "value": v})
                    rep = pd.DataFrame(flat_rows)
                    st.dataframe(rep.head(50), use_container_width=True, hide_index=True)
                else:
                    st.code(str(data)[:4000], language="text")
        except Exception as exc:
            st.error(f"Failed to load report: {exc}")


def _cycle_compare_snapshot(events_df: pd.DataFrame, profile: str, window: str) -> dict[str, Any]:
    cycles = events_df[events_df["event_type"] == "switch_cycle_complete"].copy() if not events_df.empty else pd.DataFrame()
    if cycles.empty:
        return {
            "profile": profile,
            "window": window,
            "cycles": 0,
            "orders_submitted_sum": 0,
            "orders_submitted_avg": None,
            "threshold_avg_pct": None,
            "variant_changes": 0,
        }
    if profile and profile != "All":
        cycles = cycles[cycles["profile"].astype(str) == str(profile)]
    if cycles.empty:
        return {
            "profile": profile,
            "window": window,
            "cycles": 0,
            "orders_submitted_sum": 0,
            "orders_submitted_avg": None,
            "threshold_avg_pct": None,
            "variant_changes": 0,
        }
    cutoff = _time_window_cutoff(window)
    if cutoff is not None and "ts_ny" in cycles.columns:
        cycles = cycles[pd.to_datetime(cycles["ts_ny"], errors="coerce") >= cutoff]
    if cycles.empty:
        return {
            "profile": profile,
            "window": window,
            "cycles": 0,
            "orders_submitted_sum": 0,
            "orders_submitted_avg": None,
            "threshold_avg_pct": None,
            "variant_changes": 0,
        }
    orders = pd.to_numeric(cycles["orders_submitted"], errors="coerce")
    th = pd.to_numeric(cycles["threshold_pct"], errors="coerce")
    vc = events_df[events_df["event_type"] == "switch_variant_changed"].copy()
    if cutoff is not None and (not vc.empty) and ("ts_ny" in vc.columns):
        vc = vc[pd.to_datetime(vc["ts_ny"], errors="coerce") >= cutoff]
    return {
        "profile": profile,
        "window": window,
        "cycles": int(len(cycles)),
        "orders_submitted_sum": int(orders.fillna(0).sum()),
        "orders_submitted_avg": float(orders.mean()) if orders.notna().any() else None,
        "threshold_avg_pct": float(th.mean()) if th.notna().any() else None,
        "variant_changes": int(len(vc)),
    }


def _render_profile_compare_mode(events_df: pd.DataFrame) -> None:
    with st.expander("Compare Mode (Side-by-Side Profiles/Windows)", expanded=False):
        profiles = sorted([p for p in events_df.get("profile", pd.Series(dtype=str)).dropna().astype(str).unique().tolist() if p]) if not events_df.empty else []
        profile_opts = ["All"] + profiles if profiles else ["All"]
        windows = ["1D", "1W", "1M", "3M", "All"]
        l1, l2 = st.columns(2)
        with l1:
            l_profile = st.selectbox("Left Profile", options=profile_opts, index=0, key="cmp_left_profile")
            l_win = st.selectbox("Left Window", options=windows, index=2, key="cmp_left_window")
        with l2:
            r_profile = st.selectbox("Right Profile", options=profile_opts, index=min(1, len(profile_opts) - 1), key="cmp_right_profile")
            r_win = st.selectbox("Right Window", options=windows, index=3, key="cmp_right_window")
        left = _cycle_compare_snapshot(events_df, l_profile, l_win)
        right = _cycle_compare_snapshot(events_df, r_profile, r_win)
        rows = pd.DataFrame([left, right])
        st.dataframe(rows, use_container_width=True, hide_index=True)
        delta = pd.DataFrame(
            [
                {
                    "metric": "cycles",
                    "left": left["cycles"],
                    "right": right["cycles"],
                    "delta_right_minus_left": right["cycles"] - left["cycles"],
                },
                {
                    "metric": "orders_submitted_sum",
                    "left": left["orders_submitted_sum"],
                    "right": right["orders_submitted_sum"],
                    "delta_right_minus_left": right["orders_submitted_sum"] - left["orders_submitted_sum"],
                },
                {
                    "metric": "orders_submitted_avg",
                    "left": left["orders_submitted_avg"],
                    "right": right["orders_submitted_avg"],
                    "delta_right_minus_left": (None if (left["orders_submitted_avg"] is None or right["orders_submitted_avg"] is None) else right["orders_submitted_avg"] - left["orders_submitted_avg"]),
                },
                {
                    "metric": "threshold_avg_pct",
                    "left": left["threshold_avg_pct"],
                    "right": right["threshold_avg_pct"],
                    "delta_right_minus_left": (None if (left["threshold_avg_pct"] is None or right["threshold_avg_pct"] is None) else right["threshold_avg_pct"] - left["threshold_avg_pct"]),
                },
                {
                    "metric": "variant_changes",
                    "left": left["variant_changes"],
                    "right": right["variant_changes"],
                    "delta_right_minus_left": right["variant_changes"] - left["variant_changes"],
                },
            ]
        )
        st.dataframe(delta, use_container_width=True, hide_index=True)


def _render_strategy_analytics_workspace(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-caption'>Strategy analytics for variant transitions, threshold adaptation, and regime metrics.</div>",
        unsafe_allow_html=True,
    )

    top_left, top_right = st.columns([1.0, 1.2])
    with top_left:
        st.subheader("Variant Transitions")
        changes = _variant_changes_table(events_df)
        if changes.empty:
            st.info("No variant transition events available.")
        else:
            st.dataframe(changes, use_container_width=True, hide_index=True)
    with top_right:
        st.subheader("Variant Timeline")
        vt = _variant_timeline_chart(events_df)
        if vt is None:
            st.info("No timeline data available.")
        else:
            st.altair_chart(vt, use_container_width=True)

    row1c1, row1c2 = st.columns(2)
    with row1c1:
        st.subheader("Adaptive Threshold Trend")
        tc = _threshold_trend_chart(events_df)
        if tc is None:
            st.info("No threshold trend available.")
        else:
            st.altair_chart(tc, use_container_width=True)
    with row1c2:
        st.subheader("Orders Submitted per Cycle")
        oc = _orders_submitted_trend_chart(events_df)
        if oc is None:
            st.info("No order-submission trend available.")
        else:
            st.altair_chart(oc, use_container_width=True)

    row2c1, row2c2 = st.columns(2)
    with row2c1:
        st.subheader("Regime Volatility (rv20_ann)")
        rvc = _regime_metric_trend_chart(events_df, metric="rv20_ann", title="rv20_ann", color="#f3b13c")
        if rvc is None:
            st.info("No `rv20_ann` series available.")
        else:
            st.altair_chart(rvc, use_container_width=True)
    with row2c2:
        st.subheader("Regime Drawdown (dd20_pct)")
        ddc = _regime_metric_trend_chart(events_df, metric="dd20_pct", title="dd20_pct", color="#ff5d7a")
        if ddc is None:
            st.info("No `dd20_pct` series available.")
        else:
            st.altair_chart(ddc, use_container_width=True)

    st.subheader("Current Regime State")
    regime_state = _state_value(state_df, "switch_regime_state", {})
    if isinstance(regime_state, dict) and regime_state:
        st.json(regime_state, expanded=False)
    else:
        st.info("No `switch_regime_state` key available.")

    with st.expander("Scenario Sandbox (State-Snapshot Simulation)", expanded=False):
        baseline = _state_value(state_df, "switch_last_baseline_target", {})
        final_target = _state_value(state_df, "switch_last_final_target", {})
        if not isinstance(baseline, dict):
            baseline = {}
        if not isinstance(final_target, dict):
            final_target = {}
        if not baseline and not final_target:
            st.info("No baseline/final target in state yet.")
        else:
            scenario = st.selectbox(
                "Scenario Target",
                options=["Use Final Target", "Force Baseline Target", "Half-Risk Blend (50% final / 50% baseline)", "Equal Weight of Final Symbols"],
                index=0,
                key="scenario_target_mode",
            )
            threshold_override = st.number_input(
                "Threshold Override (%)",
                min_value=0.0,
                max_value=50.0,
                value=float(_safe_float(_latest_cycle_payload(events_df).get("threshold_pct") if isinstance(_latest_cycle_payload(events_df), dict) else 0.0) or 0.0),
                step=0.25,
                key="scenario_threshold_override",
            )
            if scenario == "Use Final Target":
                sim_target = dict(final_target)
            elif scenario == "Force Baseline Target":
                sim_target = dict(baseline)
            elif scenario == "Half-Risk Blend (50% final / 50% baseline)":
                keys = sorted(set(final_target.keys()) | set(baseline.keys()))
                sim_target = {k: float(final_target.get(k, 0.0)) * 0.5 + float(baseline.get(k, 0.0)) * 0.5 for k in keys}
            else:
                keys = [k for k, v in final_target.items() if float(pd.to_numeric(v, errors="coerce") or 0.0) > 0]
                if not keys:
                    keys = list(final_target.keys()) or list(baseline.keys())
                w = (100.0 / len(keys)) if keys else 0.0
                sim_target = {k: w for k in keys}

            rows: list[dict[str, Any]] = []
            keys = sorted(set(sim_target.keys()) | set(final_target.keys()) | set(baseline.keys()))
            for k in keys:
                base_w = float(pd.to_numeric(baseline.get(k, 0.0), errors="coerce") or 0.0)
                cur_w = float(pd.to_numeric(final_target.get(k, 0.0), errors="coerce") or 0.0)
                sim_w = float(pd.to_numeric(sim_target.get(k, 0.0), errors="coerce") or 0.0)
                rows.append(
                    {
                        "symbol": k,
                        "baseline_wt_pct": base_w,
                        "current_final_wt_pct": cur_w,
                        "scenario_wt_pct": sim_w,
                        "delta_vs_current_pct": sim_w - cur_w,
                    }
                )
            sdf = pd.DataFrame(rows).sort_values("delta_vs_current_pct", ascending=False)
            st.caption(f"Scenario threshold override: {threshold_override:.2f}% (informational only, no runtime mutation).")
            st.dataframe(sdf, use_container_width=True, hide_index=True)
    _render_backtest_runtime_compare_panel(events_df=events_df, state_df=state_df)
    _render_profile_compare_mode(events_df=events_df)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_pnl_monitor_workspace(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-caption'>P&L monitor with equity curve, return stats, and drawdown tracking.</div>",
        unsafe_allow_html=True,
    )

    curve = _equity_curve_frame(events_df, state_df)
    snapshot = _pnl_snapshot(curve, state_df)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Start Equity", "-" if snapshot.get("start_equity") is None else f"${snapshot['start_equity']:,.2f}")
    m2.metric("Current Equity", "-" if snapshot.get("end_equity") is None else f"${snapshot['end_equity']:,.2f}")
    m3.metric("Total PnL", "-" if snapshot.get("pnl") is None else f"${snapshot['pnl']:,.2f}")
    m4.metric("Return %", "-" if snapshot.get("return_pct") is None else f"{snapshot['return_pct']:.2f}%")
    m5.metric("Max Drawdown %", "-" if snapshot.get("max_drawdown_pct") is None else f"{snapshot['max_drawdown_pct']:.2f}%")

    c1, c2 = st.columns([1.35, 1.0])
    with c1:
        st.subheader("Equity Curve")
        chart = _equity_curve_chart(curve)
        if chart is None:
            st.info("No equity series found in current runtime DB. (Tip: load simulated 90d/1y DB for a populated curve.)")
        else:
            st.altair_chart(chart, use_container_width=True)
    with c2:
        st.subheader("Daily Delta (latest)")
        if curve.empty:
            st.info("No cycle-level return rows available.")
        else:
            view = curve[["ts_ny", "day", "equity", "pnl", "ret_pct", "drawdown_pct", "variant"]].copy()
            view = view.rename(columns={"ts_ny": "ts"})
            st.dataframe(view.sort_values("ts", ascending=False).head(20), use_container_width=True, hide_index=True, height=300)

    row2c1, row2c2 = st.columns(2)
    with row2c1:
        st.subheader("Drawdown Curve")
        dd_chart = _drawdown_curve_chart(curve)
        if dd_chart is None:
            st.info("Drawdown curve unavailable.")
        else:
            st.altair_chart(dd_chart, use_container_width=True)
    with row2c2:
        st.subheader("Rolling Sharpe")
        sh_chart = _rolling_sharpe_chart(curve, window=20)
        if sh_chart is None:
            st.info("Rolling Sharpe requires enough return points.")
        else:
            st.altair_chart(sh_chart, use_container_width=True)

    st.subheader("Variant Exposure Heatmap")
    heatmap = _variant_exposure_heatmap(curve)
    if heatmap is None:
        st.info("Exposure heatmap unavailable.")
    else:
        st.altair_chart(heatmap, use_container_width=True)

    with st.expander("PnL Attribution 2.0 (Execution Proxy)", expanded=False):
        st.caption("Proxy decomposition from execution notional grouped by symbol, variant, and day.")
        orders = _orders_table(events_df)
        by_symbol, by_variant, by_day = _pnl_attribution_v2(orders)
        c1, c2, c3 = st.columns(3)
        with c1:
            st.subheader("By Symbol")
            if by_symbol.empty:
                st.info("No symbol attribution rows.")
            else:
                st.dataframe(by_symbol, use_container_width=True, hide_index=True)
        with c2:
            st.subheader("By Variant")
            if by_variant.empty:
                st.info("No variant attribution rows.")
            else:
                st.dataframe(by_variant, use_container_width=True, hide_index=True)
        with c3:
            st.subheader("By Day")
            if by_day.empty:
                st.info("No day attribution rows.")
            else:
                st.dataframe(by_day.sort_values("day_ny", ascending=False), use_container_width=True, hide_index=True)
        if not by_day.empty:
            day_chart = (
                alt.Chart(by_day)
                .mark_bar(opacity=0.9)
                .encode(
                    x=alt.X("day_ny:N", title="Day (NY)", sort=None),
                    y=alt.Y("net_notional:Q", title="Net Notional (proxy)"),
                    color=alt.Color("net_notional:Q", scale=alt.Scale(scheme="redyellowgreen"), title="Net"),
                    tooltip=["day_ny:N", "events:Q", alt.Tooltip("buy_notional:Q", format=",.2f"), alt.Tooltip("sell_notional:Q", format=",.2f"), alt.Tooltip("net_notional:Q", format=",.2f")],
                )
                .properties(height=260)
                .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
                .configure_view(strokeOpacity=0)
            )
            st.altair_chart(day_chart, use_container_width=True)

    with st.expander("Export Snapshot Pack (CSV/JSON/PDF)", expanded=False):
        payload = {
            "generated_at_ny": pd.Timestamp.now(tz=NY_TZ).isoformat(),
            "snapshot": snapshot,
            "rows": [] if curve.empty else curve.tail(250).to_dict(orient="records"),
        }
        csv_bytes = b"" if curve.empty else curve.to_csv(index=False).encode("utf-8")
        json_bytes = json.dumps(payload, indent=2, default=str).encode("utf-8")
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("snapshot.json", json_bytes)
            zf.writestr("equity_curve.csv", csv_bytes if csv_bytes else b"")
        st.download_button(
            "Download ZIP (snapshot.json + equity_curve.csv)",
            data=zbuf.getvalue(),
            file_name="pnl_snapshot_pack.zip",
            mime="application/zip",
            use_container_width=True,
        )
        # Optional PDF export if matplotlib is available.
        try:
            import matplotlib.pyplot as plt
            from matplotlib.backends.backend_pdf import PdfPages

            pbuf = io.BytesIO()
            with PdfPages(pbuf) as pdf:
                fig = plt.figure(figsize=(8.27, 11.69))
                ax = fig.add_subplot(111)
                ax.axis("off")
                lines = [
                    "Automated Trading Dashboard - P&L Snapshot",
                    f"Generated: {pd.Timestamp.now(tz=NY_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
                    "",
                    f"Start Equity: {snapshot.get('start_equity')}",
                    f"Current Equity: {snapshot.get('end_equity')}",
                    f"Total PnL: {snapshot.get('pnl')}",
                    f"Return %: {snapshot.get('return_pct')}",
                    f"Max Drawdown %: {snapshot.get('max_drawdown_pct')}",
                ]
                ax.text(0.02, 0.98, "\n".join(lines), va="top", ha="left", fontsize=11)
                pdf.savefig(fig, bbox_inches="tight")
                plt.close(fig)
            st.download_button(
                "Download PDF Summary",
                data=pbuf.getvalue(),
                file_name="pnl_snapshot_summary.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        except Exception:
            st.caption("PDF export unavailable (matplotlib backend not available in this environment).")

    st.markdown("</div>", unsafe_allow_html=True)


def _render_notifications_workspace(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-caption'>Notification center for health alerts, variant transitions, and execution events.</div>",
        unsafe_allow_html=True,
    )
    with st.expander("Alerts Workbench (Custom Rules)", expanded=False):
        st.caption("Define operator alert rules for stale streams, low cycle cadence, and error-event ceilings.")
        r1, r2, r3 = st.columns(3)
        with r1:
            st.checkbox("Rule: freshness > X min", value=bool(st.session_state.get("ui_custom_alert_stale_enabled", False)), key="ui_custom_alert_stale_enabled")
            st.number_input("X minutes", min_value=1, max_value=180, value=int(st.session_state.get("ui_custom_alert_stale_min", 10) or 10), key="ui_custom_alert_stale_min")
        with r2:
            st.checkbox("Rule: cycles24h < Y", value=bool(st.session_state.get("ui_custom_alert_min_cycles_enabled", False)), key="ui_custom_alert_min_cycles_enabled")
            st.number_input("Y cycles", min_value=0, max_value=50, value=int(st.session_state.get("ui_custom_alert_min_cycles_24h", 1) or 1), key="ui_custom_alert_min_cycles_24h")
        with r3:
            st.checkbox("Rule: error_events24h > Z", value=bool(st.session_state.get("ui_custom_alert_error_enabled", False)), key="ui_custom_alert_error_enabled")
            st.number_input("Z max errors", min_value=0, max_value=500, value=int(st.session_state.get("ui_custom_alert_error_max_24h", 0) or 0), key="ui_custom_alert_error_max_24h")
    with st.expander("Alert Rule Builder 2.0", expanded=False):
        st.caption("Build reusable rules without editing code. Rules are persisted in user preferences.")
        rb1, rb2, rb3 = st.columns(3)
        with rb1:
            rule_title = st.text_input("Rule Title", value="", key="ui_rule_builder_title", placeholder="e.g. Freshness Hard Breach")
            metric = st.selectbox(
                "Metric",
                options=["freshness_min", "events_5m", "events_1h", "cycles_24h", "order_events_24h", "error_events_24h"],
                index=0,
                key="ui_rule_builder_metric",
            )
        with rb2:
            operator = st.selectbox("Operator", options=[">", ">=", "<", "<=", "==", "!="], index=0, key="ui_rule_builder_operator")
            threshold = st.number_input("Threshold", value=10.0, step=0.5, key="ui_rule_builder_threshold")
        with rb3:
            severity = st.selectbox("Severity", options=["high", "medium", "info"], index=1, key="ui_rule_builder_severity")
            if st.button("Add Rule", use_container_width=True, key="ui_rule_builder_add"):
                rules = st.session_state.get("ui_alert_rules", [])
                if not isinstance(rules, list):
                    rules = []
                rules.append(
                    {
                        "title": str(rule_title or "").strip() or f"{metric} {operator} {threshold}",
                        "metric": metric,
                        "operator": operator,
                        "threshold": float(threshold),
                        "severity": severity,
                    }
                )
                st.session_state["ui_alert_rules"] = rules
                _append_ui_audit("rule_builder_add", {"metric": metric, "operator": operator, "threshold": float(threshold), "severity": severity})
                st.success("Rule added.")
                st.rerun()
        rules_now = st.session_state.get("ui_alert_rules", [])
        if isinstance(rules_now, list) and rules_now:
            df_rules = pd.DataFrame(rules_now)
            df_rules["index"] = list(range(len(df_rules)))
            st.dataframe(df_rules, use_container_width=True, hide_index=True)
            d1, d2 = st.columns([2, 1])
            with d1:
                drop_idx = st.number_input("Delete Rule Index", min_value=0, max_value=max(0, len(df_rules) - 1), value=0, step=1, key="ui_rule_builder_drop_idx")
            with d2:
                if st.button("Delete Rule", use_container_width=True, key="ui_rule_builder_delete"):
                    rules = st.session_state.get("ui_alert_rules", [])
                    if isinstance(rules, list) and 0 <= int(drop_idx) < len(rules):
                        rules.pop(int(drop_idx))
                        st.session_state["ui_alert_rules"] = rules
                        _append_ui_audit("rule_builder_delete", {"index": int(drop_idx)})
                        st.success("Rule deleted.")
                        st.rerun()
        else:
            st.caption("No dynamic rules configured.")
    with st.expander("Alert Channels (Webhook / Email)", expanded=False):
        st.caption("Send test alerts to external channels. Store channel secrets in env vars for production.")
        webhook_url = st.text_input("Webhook URL", value=str(st.session_state.get("ui_alert_webhook_url", "") or ""), key="ui_alert_webhook_url", type="password")
        email_to = st.text_input("Test Email Recipient", value=str(st.session_state.get("ui_alert_email_to", "") or ""), key="ui_alert_email_to")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Send Test Webhook", use_container_width=True, key="ui_send_test_webhook"):
                ok, msg = _send_webhook_message(
                    webhook_url,
                    {
                        "event": "switch_runtime_alert_test",
                        "ts_ny": pd.Timestamp.now(tz=NY_TZ).isoformat(),
                        "user": str(st.session_state.get("auth_user", "")),
                        "summary": "UI test alert",
                    },
                )
                _append_ui_audit("send_test_webhook", {"ok": ok})
                (st.success if ok else st.error)(msg)
        with c2:
            if st.button("Send Test Email", use_container_width=True, key="ui_send_test_email"):
                ok, msg = _send_email_message(
                    subject="Switch Runtime UI Test Alert",
                    body=f"Test alert generated at {pd.Timestamp.now(tz=NY_TZ).isoformat()}",
                    to_addr=email_to,
                )
                _append_ui_audit("send_test_email", {"ok": ok})
                (st.success if ok else st.error)(msg)
    notices = _notification_center_table(events_df, state_df).copy()
    custom = _custom_alert_notices(events_df, state_df)
    if not custom.empty:
        notices = pd.concat([custom, notices], ignore_index=True)
        notices["ts"] = pd.to_datetime(notices["ts"], errors="coerce")
        notices = notices.sort_values("ts", ascending=False).reset_index(drop=True)
    if notices.empty:
        st.success("No notifications available.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    actions = st.session_state.get("notice_actions", {})
    if not isinstance(actions, dict):
        actions = {}
    now = pd.Timestamp.now(tz=NY_TZ)

    def _alert_key(row: pd.Series) -> str:
        base = f"{row.get('ts')}|{row.get('severity')}|{row.get('title')}|{row.get('source')}"
        return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]

    notices["alert_key"] = notices.apply(_alert_key, axis=1)
    status_col: list[str] = []
    snooze_col: list[str] = []
    for _, row in notices.iterrows():
        key = str(row["alert_key"])
        rec = actions.get(key, {})
        status = str(rec.get("status", "open"))
        snooze_until = rec.get("snooze_until")
        if snooze_until:
            try:
                snooze_ts = pd.Timestamp(snooze_until)
                if snooze_ts.tzinfo is None:
                    snooze_ts = snooze_ts.tz_localize(NY_TZ)
                if now < snooze_ts:
                    status = "snoozed"
                else:
                    if status == "snoozed":
                        status = "open"
            except Exception:
                pass
        status_col.append(status)
        snooze_col.append(str(snooze_until or ""))
    notices["status"] = status_col
    notices["snooze_until"] = snooze_col

    active_notices = notices[notices["status"] != "acknowledged"].copy()
    high_n = int((active_notices["severity"] == "high").sum())
    med_n = int((active_notices["severity"] == "medium").sum())
    info_n = int((active_notices["severity"] == "info").sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("High", str(high_n))
    c2.metric("Medium", str(med_n))
    c3.metric("Info", str(info_n))

    left, right = st.columns([1.2, 0.9])
    with left:
        selected = st.multiselect(
            "Severity Filter",
            options=["high", "medium", "info"],
            default=["high", "medium", "info"],
            key="notice_severity_filter",
        )
        status_filter = st.multiselect(
            "Status Filter",
            options=["open", "snoozed", "acknowledged", "escalated"],
            default=["open", "snoozed", "escalated"],
            key="notice_status_filter",
        )
        view = active_notices if selected else notices.copy()
        if selected:
            view = view[view["severity"].isin(selected)]
        if status_filter:
            view = view[view["status"].isin(status_filter)]
        cols = ["ts", "severity", "status", "title", "detail", "source", "snooze_until", "alert_key"]
        st.dataframe(_paged_view(view[cols], key="notice_tbl", default_page_size=50), use_container_width=True, hide_index=True, height=360)
    with right:
        ch = _notification_severity_chart(notices)
        if ch is not None:
            st.altair_chart(ch, use_container_width=True)
        st.caption("Use this panel as your operational inbox before/after each cycle.")
        if view.empty:
            st.info("No alerts in current filter.")
        else:
            options = [f"{r.alert_key} | {r.severity} | {r.title}" for r in view.itertuples()]
            selected_alert = st.selectbox("Manage Alert", options=options, index=0, key="manage_alert_select")
            selected_key = selected_alert.split("|", 1)[0].strip()
            btn1, btn2 = st.columns(2)
            with btn1:
                if st.button("Acknowledge", use_container_width=True, key="notice_ack_btn"):
                    rec = actions.get(selected_key, {})
                    rec["status"] = "acknowledged"
                    rec["updated_at"] = now.isoformat()
                    actions[selected_key] = rec
                    st.session_state.notice_actions = actions
                    _append_ui_audit("alert_acknowledge", {"alert_key": selected_key})
                    st.success("Alert acknowledged.")
                    st.rerun()
                if st.button("Snooze 1h", use_container_width=True, key="notice_snooze_btn"):
                    rec = actions.get(selected_key, {})
                    rec["status"] = "snoozed"
                    rec["snooze_until"] = (now + pd.Timedelta(hours=1)).isoformat()
                    rec["updated_at"] = now.isoformat()
                    actions[selected_key] = rec
                    st.session_state.notice_actions = actions
                    _append_ui_audit("alert_snooze", {"alert_key": selected_key, "hours": 1})
                    st.success("Alert snoozed for 1 hour.")
                    st.rerun()
            with btn2:
                if st.button("Escalate", use_container_width=True, key="notice_escalate_btn"):
                    rec = actions.get(selected_key, {})
                    rec["status"] = "escalated"
                    rec["updated_at"] = now.isoformat()
                    actions[selected_key] = rec
                    st.session_state.notice_actions = actions
                    _append_ui_audit("alert_escalate", {"alert_key": selected_key})
                    st.warning("Alert escalated.")
                    st.rerun()
                if st.button("Reset", use_container_width=True, key="notice_reset_btn"):
                    actions.pop(selected_key, None)
                    st.session_state.notice_actions = actions
                    _append_ui_audit("alert_reset", {"alert_key": selected_key})
                    st.info("Alert status reset.")
                    st.rerun()

    with st.expander("Incident Timeline Mode", expanded=False):
        inc = _incident_timeline_frame(events_df, notices)
        if inc.empty:
            st.info("No incident timeline data yet.")
        else:
            chart = (
                alt.Chart(inc)
                .mark_point(filled=True, size=75)
                .encode(
                    x=alt.X("ts:T", title="Time (NY)"),
                    y=alt.Y("source:N", title="Source"),
                    color=alt.Color("severity:N", scale=alt.Scale(domain=["high", "medium", "info"], range=["#ff5d7a", "#f3b13c", "#52db66"])),
                    tooltip=["ts:T", "source:N", "kind:N", "severity:N", "symbol:N", "detail:N"],
                )
                .properties(height=260)
                .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
                .configure_view(strokeOpacity=0)
            )
            st.altair_chart(chart, use_container_width=True)
            st.dataframe(_paged_view(inc, key="incident_timeline", default_page_size=50), use_container_width=True, hide_index=True)

    st.markdown("</div>", unsafe_allow_html=True)


def _render_system_health_workspace(events_df: pd.DataFrame, state_df: pd.DataFrame, db_path: str) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-caption'>System health & data quality workspace with SLO-style checks and state freshness.</div>",
        unsafe_allow_html=True,
    )
    hs = _health_snapshot(events_df, state_df)
    db = Path(db_path)
    db_size = db.stat().st_size if db.exists() else 0
    db_mtime = pd.Timestamp(db.stat().st_mtime, unit="s", tz="UTC").tz_convert(NY_TZ) if db.exists() else None

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Event Freshness (m)", "-" if hs["freshness_min"] is None else f"{hs['freshness_min']:.1f}")
    m2.metric("Events (5m)", f"{int(hs['events_5m'])}")
    m3.metric("Events (1h)", f"{int(hs['events_1h'])}")
    m4.metric("Cycles (24h)", f"{int(hs['cycles_24h'])}")
    m5.metric("DB Size (KB)", f"{db_size/1024:.1f}")
    m6.metric("State Keys", f"{int(hs['state_key_count'])}")

    if db_mtime is not None:
        st.caption(f"DB modified: `{db_mtime}` | Path: `{db_path}`")
    else:
        st.caption(f"DB file not found at `{db_path}`")

    left, right = st.columns([1.05, 1.15])
    with left:
        st.subheader("SLO Checks")
        st.dataframe(_slo_checks_table(events_df, state_df), use_container_width=True, hide_index=True)
    with right:
        st.subheader("State Key Freshness")
        sat = _state_age_table(state_df)
        if sat.empty:
            st.info("No state keys available.")
        else:
            st.dataframe(sat, use_container_width=True, hide_index=True)

    st.subheader("Risk Guardrails")
    guard = _risk_guardrail_table(events_df, state_df)
    st.dataframe(guard, use_container_width=True, hide_index=True)

    st.subheader("State Drift Monitor")
    drift = _target_vs_event_drift_table(events_df, state_df)
    if drift.empty:
        st.info("No drift data available (requires target + event-derived position data).")
    else:
        drift_limit = st.number_input("Alert when abs drift exceeds %", min_value=0.5, max_value=100.0, value=20.0, step=0.5, key="ops_drift_limit")
        drift["status"] = drift["abs_drift_pct"].map(lambda v: "BREACH" if float(v) > float(drift_limit) else "OK")
        d1, d2, d3 = st.columns(3)
        d1.metric("Symbols in Drift Table", f"{int(len(drift)):,}")
        d2.metric("Max Abs Drift %", f"{float(drift['abs_drift_pct'].max()):.2f}")
        d3.metric("Breaches", f"{int((drift['status'] == 'BREACH').sum()):,}")
        st.dataframe(drift, use_container_width=True, hide_index=True)

    st.subheader("Event Types (Last 24h)")
    if events_df.empty or events_df["ts_ny"].isna().all():
        st.info("No event data available.")
    else:
        cutoff = pd.Timestamp.now(tz=NY_TZ) - pd.Timedelta(hours=24)
        recent = events_df[events_df["ts_ny"] >= cutoff]
        if recent.empty:
            st.info("No events in the last 24h.")
        else:
            summary = recent.groupby("event_type", as_index=False).size().rename(columns={"size": "count"}).sort_values("count", ascending=False)
            st.dataframe(summary, use_container_width=True, hide_index=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_audit_workspace(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-caption'>Audit workspace for append-only event review, payload forensics, and state snapshots.</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Audit model: runtime emits append-only `events` and point-in-time `state_kv` projections. "
        "Use this panel for investigation and post-mortem traceability."
    )

    left, right = st.columns([1.3, 1.0])
    with left:
        st.subheader("Event Explorer")
        if events_df.empty:
            st.info("No events available.")
        else:
            all_types = sorted(events_df["event_type"].dropna().unique().tolist())
            selected = st.multiselect("Event Types", all_types, default=all_types, key="audit_types")
            query = st.text_input("Payload Contains", placeholder="e.g. variant, SOXL, market_order", key="audit_query")

            view = events_df.copy()
            if selected:
                view = view[view["event_type"].isin(selected)]
            if query.strip():
                needle = query.strip().lower()
                view = view[view["payload_text"].str.lower().str.contains(needle, na=False)]

            cols = ["id", "ts_ny", "event_type", "symbol", "side", "qty", "profile", "variant", "order_type", "payload_text"]
            for col in cols:
                if col not in view.columns:
                    view[col] = None
            audit_view = view[cols].rename(columns={"ts_ny": "ts"})
            st.dataframe(_paged_view(audit_view, key="audit_explorer", default_page_size=100), use_container_width=True, hide_index=True)
    with right:
        st.subheader("Cycle Metrics Ledger")
        cmt = _cycle_metrics_table(events_df)
        if cmt.empty:
            st.info("No cycle metrics entries found.")
        else:
            st.dataframe(cmt, use_container_width=True, hide_index=True)

        st.subheader("Current State Snapshot")
        if state_df.empty:
            st.info("No state keys available.")
        else:
            state_render = state_df[["key", "value", "updated_at_ny"]].rename(columns={"updated_at_ny": "updated_at"})
            st.dataframe(state_render, use_container_width=True, hide_index=True)

    with st.expander("Replay Day (Audit Scrubber)", expanded=False):
        if events_df.empty or ("ts_ny" not in events_df.columns):
            st.info("Replay unavailable: no timestamped events.")
        else:
            replay = events_df.dropna(subset=["ts_ny"]).copy().sort_values("ts_ny")
            if replay.empty:
                st.info("Replay unavailable: no timestamped events.")
            else:
                replay["day_ny"] = pd.to_datetime(replay["ts_ny"], errors="coerce").dt.strftime("%Y-%m-%d")
                days = sorted([d for d in replay["day_ny"].dropna().unique().tolist() if d])
                if not days:
                    st.info("Replay unavailable: no valid day buckets.")
                else:
                    day_sel = st.selectbox("Replay Day", options=days, index=max(0, len(days) - 1), key="audit_replay_day")
                    day_df = replay[replay["day_ny"] == day_sel].copy().reset_index(drop=True)
                    if day_df.empty:
                        st.info("No events for selected day.")
                    else:
                        p1, p2, p3 = st.columns([1.2, 1.0, 2.0])
                        with p1:
                            playing = st.checkbox("Auto Play Replay", value=bool(st.session_state.get("audit_replay_playing", False)), key="audit_replay_playing")
                        with p2:
                            speed = st.selectbox("Tick (sec)", options=[1, 2, 3, 5, 10], index=1, key="audit_replay_speed_sec", disabled=not playing)
                        with p3:
                            cprev, cnext = st.columns(2)
                            with cprev:
                                if st.button("Step Back", use_container_width=True, key="audit_step_back"):
                                    cur_idx = int(st.session_state.get("audit_replay_idx", len(day_df)) or len(day_df))
                                    st.session_state["audit_replay_idx"] = max(1, cur_idx - 1)
                                    st.rerun()
                            with cnext:
                                if st.button("Step Forward", use_container_width=True, key="audit_step_fwd"):
                                    cur_idx = int(st.session_state.get("audit_replay_idx", 1) or 1)
                                    st.session_state["audit_replay_idx"] = min(len(day_df), cur_idx + 1)
                                    st.rerun()
                        if "audit_replay_idx" not in st.session_state:
                            st.session_state["audit_replay_idx"] = len(day_df)
                        if int(st.session_state.get("audit_replay_idx", 1)) > len(day_df):
                            st.session_state["audit_replay_idx"] = len(day_df)
                        if int(st.session_state.get("audit_replay_idx", 1)) < 1:
                            st.session_state["audit_replay_idx"] = 1
                        if playing and len(day_df) > 1:
                            cur_idx = int(st.session_state.get("audit_replay_idx", 1) or 1)
                            next_idx = min(len(day_df), cur_idx + 1)
                            st.session_state["audit_replay_idx"] = next_idx
                            if next_idx < len(day_df):
                                _auto_refresh_pulse(enabled=True, interval_seconds=int(speed))
                        if len(day_df) <= 1:
                            idx = 1
                            st.caption("Replay Event Index: 1/1")
                        else:
                            idx = st.slider(
                                "Replay Event Index",
                                min_value=1,
                                max_value=len(day_df),
                                value=len(day_df),
                                step=1,
                                key="audit_replay_idx",
                            )
                        current = day_df.iloc[int(idx) - 1]
                        st.caption(f"Replay {idx}/{len(day_df)} | ts={current.get('ts_ny')} | event={current.get('event_type')}")
                        replay_cols = ["id", "ts_ny", "event_type", "symbol", "side", "qty", "variant", "order_type"]
                        for col in replay_cols:
                            if col not in day_df.columns:
                                day_df[col] = None
                        st.dataframe(day_df.iloc[: int(idx)][replay_cols].sort_values("ts_ny", ascending=False), use_container_width=True, hide_index=True, height=280)
                        payload = current.get("payload")
                        if isinstance(payload, dict):
                            st.json(payload, expanded=False)
                        else:
                            st.code(str(current.get("payload_text", ""))[:4000], language="text")

    with st.expander("UI Command Audit Log", expanded=False):
        logs = _load_ui_audit(limit=5000)
        if logs.empty:
            st.info("No UI command audit rows yet.")
        else:
            st.dataframe(_paged_view(logs, key="ui_audit_log_tbl", default_page_size=100), use_container_width=True, hide_index=True)
            st.download_button(
                "Download UI Audit CSV",
                data=logs.to_csv(index=False).encode("utf-8"),
                file_name="ui_command_audit.csv",
                mime="text/csv",
                use_container_width=True,
                key="ui_audit_download",
            )
    st.markdown("</div>", unsafe_allow_html=True)


def _render_overview_tab(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown("<div class='section-caption'>High-density command center for event pulse, distribution, and latest cycle output.</div>", unsafe_allow_html=True)
    left, right = st.columns([1.7, 1.0])

    with left:
        st.subheader("Event Flow (Hourly)")
        chart = _events_by_hour_chart(events_df)
        if chart is None:
            st.info("No events available for charting yet.")
        else:
            st.altair_chart(chart, use_container_width=True)

    with right:
        st.subheader("Event Distribution")
        chart2 = _event_type_distribution(events_df)
        if chart2 is None:
            st.info("No events available yet.")
        else:
            st.altair_chart(chart2, use_container_width=True)

    st.subheader("Latest Cycle Snapshot")
    payload = _latest_cycle_payload(events_df)
    if payload is None:
        st.warning("No `switch_cycle_complete` event found yet.")
    else:
        st.json(payload, expanded=False)

    st.subheader("State Keys")
    if state_df.empty:
        st.info("No state keys found in selected DB.")
    else:
        render = state_df[["key", "value", "updated_at_ny"]].rename(columns={"updated_at_ny": "updated_at"})
        st.dataframe(render, use_container_width=True, hide_index=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_orders_tab(events_df: pd.DataFrame) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown("<div class='section-caption'>Execution tape for rebalance orders, profit-lock exits, and symbol activity.</div>", unsafe_allow_html=True)
    st.subheader("Order and Exit Activity")
    orders = _orders_table(events_df)
    if orders.empty:
        st.info("No order/exit events found yet.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    c1, c2 = st.columns(2)
    with c1:
        by_type = orders.groupby("event_type").size().reset_index(name="count")
        st.altair_chart(
            alt.Chart(by_type)
            .mark_bar(opacity=0.88, cornerRadiusTopRight=4, cornerRadiusTopLeft=4)
            .encode(
                x=alt.X("event_type:N", sort="-y"),
                y="count:Q",
                color=alt.Color("event_type:N", legend=None),
                tooltip=["event_type", "count"],
            )
            .properties(height=280),
            use_container_width=True,
        )

    with c2:
        by_symbol = orders.dropna(subset=["symbol"]).groupby("symbol").size().reset_index(name="count")
        if by_symbol.empty:
            st.info("No symbol-level order data yet.")
        else:
            st.altair_chart(
                alt.Chart(by_symbol)
                .mark_bar(opacity=0.88, cornerRadiusTopRight=4, cornerRadiusTopLeft=4, color="#00c805")
                .encode(x=alt.X("symbol:N", sort="-y"), y="count:Q", tooltip=["symbol", "count"])
                .properties(height=280),
                use_container_width=True,
            )

    st.dataframe(orders, use_container_width=True, hide_index=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_regime_tab(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown("<div class='section-caption'>Regime desk for transition events, active state, and risk telemetry.</div>", unsafe_allow_html=True)
    st.subheader("Variant Transitions")
    changes = _variant_changes_table(events_df)
    if changes.empty:
        st.info("No variant transition events yet.")
    else:
        st.dataframe(changes, use_container_width=True, hide_index=True)

    st.subheader("Current Regime State")
    regime_state = _state_value(state_df, "switch_regime_state", {})
    if isinstance(regime_state, dict) and regime_state:
        st.json(regime_state, expanded=False)
    else:
        st.info("No `switch_regime_state` key found yet.")

    st.subheader("Cycle Regime Metrics")
    cycle_df = _cycle_metrics_table(events_df)
    if cycle_df.empty:
        st.info("No cycle metrics available yet.")
    else:
        st.dataframe(cycle_df, use_container_width=True, hide_index=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_event_explorer_tab(events_df: pd.DataFrame) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown("<div class='section-caption'>Searchable event inspector with payload-level filtering and forensic context.</div>", unsafe_allow_html=True)
    st.subheader("Event Explorer")
    if events_df.empty:
        st.info("No events in current DB selection.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    all_types = sorted(events_df["event_type"].dropna().unique().tolist())
    selected = st.multiselect("Event Types", all_types, default=all_types)
    query = st.text_input("Payload Contains", placeholder="e.g. SOXL, baseline, bracket")

    view = events_df.copy()
    if selected:
        view = view[view["event_type"].isin(selected)]
    if query.strip():
        needle = query.strip().lower()
        view = view[view["payload_text"].str.lower().str.contains(needle, na=False)]

    cols = [
        "id",
        "ts_ny",
        "event_type",
        "symbol",
        "side",
        "qty",
        "variant",
        "variant_reason",
        "order_type",
        "payload_text",
    ]
    for col in cols:
        if col not in view.columns:
            view[col] = None

    explorer_view = view[cols].rename(columns={"ts_ny": "ts"})
    st.dataframe(
        _paged_view(explorer_view, key="event_explorer", default_page_size=100),
        use_container_width=True,
        hide_index=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def _render_backtest_import_hub(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-caption'>Backtest import hub for drag/drop reports and runtime-vs-backtest comparison overlays.</div>",
        unsafe_allow_html=True,
    )
    uploaded = st.file_uploader(
        "Upload Backtest Files (CSV/JSON)",
        type=["csv", "json"],
        accept_multiple_files=True,
        key="backtest_import_files",
    )
    curve = _equity_curve_frame(events_df, state_df)
    snap = _pnl_snapshot(curve, state_df)
    runtime_ref = pd.DataFrame(
        [
            {
                "source": "runtime_current",
                "start_equity": snap.get("start_equity"),
                "end_equity": snap.get("end_equity"),
                "pnl": snap.get("pnl"),
                "return_pct": snap.get("return_pct"),
                "max_drawdown_pct": snap.get("max_drawdown_pct"),
            }
        ]
    )
    st.subheader("Runtime Reference")
    st.dataframe(runtime_ref, use_container_width=True, hide_index=True)
    with st.expander("Backtest vs Runtime Compare View", expanded=False):
        st.caption("Upload one runtime-equity CSV and one backtest-equity CSV to compare parity and drift.")
        rc1, rc2 = st.columns(2)
        with rc1:
            runtime_file = st.file_uploader("Runtime CSV", type=["csv"], key="cmp_runtime_csv")
        with rc2:
            backtest_file = st.file_uploader("Backtest CSV", type=["csv"], key="cmp_backtest_csv")

        def _norm_equity_frame(df: pd.DataFrame, label: str) -> tuple[pd.DataFrame, dict[str, Any]]:
            if df.empty:
                return pd.DataFrame(), {}
            ts_candidates = [c for c in df.columns if str(c).lower() in {"ts", "ts_ny", "timestamp", "date", "datetime", "day"}]
            eq_candidates = [c for c in df.columns if str(c).lower() in {"equity", "end_equity", "final_equity", "final_value", "window_end_equity"}]
            if not eq_candidates:
                eq_candidates = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
            if not eq_candidates:
                return pd.DataFrame(), {}
            eq_col = eq_candidates[0]
            out = pd.DataFrame()
            if ts_candidates:
                out["ts"] = pd.to_datetime(df[ts_candidates[0]], errors="coerce")
            else:
                out["ts"] = pd.RangeIndex(start=0, stop=len(df), step=1)
            out["equity"] = pd.to_numeric(df[eq_col], errors="coerce")
            out = out.dropna(subset=["equity"]).copy()
            if out.empty:
                return pd.DataFrame(), {}
            if "ts" in out.columns:
                out = out.sort_values("ts")
            out["source"] = label
            start_eq = float(out["equity"].iloc[0])
            end_eq = float(out["equity"].iloc[-1])
            pnl = end_eq - start_eq
            ret_pct = ((end_eq / start_eq) - 1.0) * 100.0 if start_eq != 0 else None
            stats = {
                "source": label,
                "rows": int(len(out)),
                "start_equity": start_eq,
                "end_equity": end_eq,
                "pnl": pnl,
                "return_pct": ret_pct,
                "equity_col": eq_col,
            }
            return out, stats

        if runtime_file is not None and backtest_file is not None:
            try:
                rt_df = pd.read_csv(runtime_file)
                bt_df = pd.read_csv(backtest_file)
                rt_curve, rt_stats = _norm_equity_frame(rt_df, "runtime_upload")
                bt_curve, bt_stats = _norm_equity_frame(bt_df, "backtest_upload")
                if rt_curve.empty or bt_curve.empty:
                    st.warning("Could not detect usable equity columns in one or both files.")
                else:
                    cmp_rows = pd.DataFrame([rt_stats, bt_stats])
                    st.dataframe(cmp_rows, use_container_width=True, hide_index=True)
                    delta_row = pd.DataFrame(
                        [
                            {
                                "metric": "end_equity_delta",
                                "runtime_minus_backtest": float(rt_stats["end_equity"]) - float(bt_stats["end_equity"]),
                            },
                            {
                                "metric": "pnl_delta",
                                "runtime_minus_backtest": float(rt_stats["pnl"]) - float(bt_stats["pnl"]),
                            },
                            {
                                "metric": "return_pct_delta",
                                "runtime_minus_backtest": float(rt_stats["return_pct"]) - float(bt_stats["return_pct"]),
                            },
                        ]
                    )
                    st.dataframe(delta_row, use_container_width=True, hide_index=True)
                    both = pd.concat([rt_curve, bt_curve], ignore_index=True)
                    if pd.api.types.is_datetime64_any_dtype(both["ts"]):
                        ch = (
                            alt.Chart(both)
                            .mark_line(strokeWidth=2.1)
                            .encode(
                                x=alt.X("ts:T", title="Time"),
                                y=alt.Y("equity:Q", title="Equity"),
                                color=alt.Color("source:N", title="Series"),
                                tooltip=["source:N", "ts:T", alt.Tooltip("equity:Q", format=",.2f")],
                            )
                            .properties(height=260)
                            .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
                            .configure_view(strokeOpacity=0)
                        )
                    else:
                        both = both.reset_index(drop=True)
                        both["idx"] = both.index + 1
                        ch = (
                            alt.Chart(both)
                            .mark_line(strokeWidth=2.1)
                            .encode(
                                x=alt.X("idx:Q", title="Row"),
                                y=alt.Y("equity:Q", title="Equity"),
                                color=alt.Color("source:N", title="Series"),
                                tooltip=["source:N", "idx:Q", alt.Tooltip("equity:Q", format=",.2f")],
                            )
                            .properties(height=260)
                            .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
                            .configure_view(strokeOpacity=0)
                        )
                    st.altair_chart(ch, use_container_width=True)
            except Exception as exc:
                st.error(f"Compare load failed: {exc}")
    if not uploaded:
        st.info("Upload one or more CSV/JSON backtest outputs to compare.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    summary_rows: list[dict[str, Any]] = []
    for up in uploaded:
        name = str(up.name)
        suffix = Path(name).suffix.lower()
        try:
            if suffix == ".csv":
                df = pd.read_csv(up)
            elif suffix == ".json":
                obj = json.loads(up.read().decode("utf-8", errors="replace"))
                if isinstance(obj, list):
                    df = pd.DataFrame(obj)
                elif isinstance(obj, dict):
                    df = pd.json_normalize(obj, sep=".")
                else:
                    df = pd.DataFrame()
            else:
                df = pd.DataFrame()
        except Exception as exc:
            st.error(f"{name}: failed to parse ({exc})")
            continue

        if df.empty:
            st.warning(f"{name}: no rows parsed.")
            continue

        num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        ret_col = next((c for c in df.columns if str(c).lower() in {"return_pct", "cpu_return_pct", "gpu_return_pct", "return %"}), "")
        pnl_col = next((c for c in df.columns if "pnl" in str(c).lower()), "")
        dd_col = next((c for c in df.columns if "drawdown" in str(c).lower()), "")
        summary_rows.append(
            {
                "file": name,
                "rows": int(len(df)),
                "columns": int(len(df.columns)),
                "numeric_cols": int(len(num_cols)),
                "return_col": ret_col or "-",
                "pnl_col": pnl_col or "-",
                "drawdown_col": dd_col or "-",
            }
        )

        with st.expander(f"Preview: {name}", expanded=False):
            st.dataframe(_paged_view(df, key=f"bt_preview_{name}", default_page_size=50), use_container_width=True, hide_index=True)
            if num_cols:
                pick = st.selectbox(
                    "Numeric Series",
                    options=num_cols,
                    index=0,
                    key=f"bt_num_pick_{name}",
                )
                plot_df = df[[pick]].copy().reset_index(drop=True)
                plot_df["row_idx"] = plot_df.index + 1
                ch = (
                    alt.Chart(plot_df)
                    .mark_line(point=False, color="#66d7ff")
                    .encode(
                        x=alt.X("row_idx:Q", title="Row"),
                        y=alt.Y(f"{pick}:Q", title=pick),
                        tooltip=["row_idx:Q", alt.Tooltip(f"{pick}:Q", format=",.4f")],
                    )
                    .properties(height=220)
                    .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
                    .configure_view(strokeOpacity=0)
                )
                st.altair_chart(ch, use_container_width=True)

    if summary_rows:
        st.subheader("Imported Files Summary")
        st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_runbook_tab(db_path: str, current_user: str, user_default_db: str, strict_isolation: bool) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown("<div class='section-caption'>Operator guide for deployment, startup checks, and paper/live execution commands.</div>", unsafe_allow_html=True)
    st.subheader("Operator Guide")
    st.markdown(
        """
- **Runtime script**: `/home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py`
- **State DB**: configurable in runtime with `--state-db` (dashboard currently reading selected DB path)
- **Primary profile**: `aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m`
- **Variant engine**: `baseline`, `inverse_ma20`, `inverse_ma60`
        """
    )
    st.caption(f"Signed-in user: `{current_user}` | strict isolation: `{'ON' if strict_isolation else 'OFF'}`")
    st.caption(f"User-isolated runtime DB: `{user_default_db}`")

    st.code("\n".join(_runtime_command_lines(mode="paper", state_db=user_default_db, execute_orders=True)), language="bash")
    st.code("\n".join(_runtime_command_lines(mode="live", state_db=user_default_db, execute_orders=True)), language="bash")
    if str(db_path).strip() != str(user_default_db).strip():
        st.caption("Current dashboard view DB differs from user-isolated DB.")
        st.code("\n".join(_runtime_command_lines(mode="paper", state_db=db_path, execute_orders=True)), language="bash")

    st.caption(f"Current dashboard DB path: `{db_path}`")
    st.markdown("</div>", unsafe_allow_html=True)


def _main_app() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide", page_icon="chart_with_upwards_trend")
    _inject_theme_css()
    _init_session_state()
    _apply_global_pending_ui_state()
    _flush_cookie_ops()

    cfg = _build_auth_config()
    _restore_auth_from_cookie(cfg)
    if _enforce_session_timeout(cfg):
        _render_login(cfg)
        return
    if not st.session_state.auth_ok:
        _render_login(cfg)
        return
    _touch_session_activity()
    current_user = str(st.session_state.get("auth_user", "") or "")
    user_role = _get_user_role(cfg, current_user)
    st.session_state["auth_role_cached"] = user_role
    user_default_db = str(_user_runtime_db_path(current_user))

    # User-scoped preferences.
    all_prefs = _load_prefs()
    user_prefs = all_prefs.get(current_user, {}) if isinstance(all_prefs, dict) else {}
    _load_user_ui_prefs_once(current_user=current_user, user_prefs=user_prefs if isinstance(user_prefs, dict) else {})
    _inject_compact_mode_css(enabled=bool(st.session_state.get("ui_mobile_compact_mode", False)))

    with st.sidebar:
        st.markdown("## Runtime Ops")
        st.caption("Automated Trading Ops")
        st.markdown("---")
        st.markdown("### Session")
        st.caption(f"Signed in as: `{st.session_state.auth_user}`")
        st.caption(f"Role: `{user_role}`")
        with st.expander("RBAC Capability Matrix", expanded=False):
            cap_rows = pd.DataFrame(
                [
                    {"section": "Tradeboard", "min_role": "viewer"},
                    {"section": "Portfolio Pulse", "min_role": "viewer"},
                    {"section": "Execution Journal", "min_role": "operator"},
                    {"section": "Strategy Lab", "min_role": "viewer"},
                    {"section": "P&L Monitor", "min_role": "viewer"},
                    {"section": "Notifications", "min_role": "viewer"},
                    {"section": "Ops Health", "min_role": "operator"},
                    {"section": "Audit Trail", "min_role": "admin"},
                    {"section": "Operator Guide", "min_role": "viewer"},
                    {"section": "Backtest Hub", "min_role": "viewer"},
                ]
            )
            cap_rows["available"] = cap_rows["min_role"].map(lambda r: "YES" if _role_at_least(user_role, str(r)) else "NO")
            st.dataframe(cap_rows, use_container_width=True, hide_index=True)
        st.caption(f"Auto-logout: `{cfg.session_timeout_min}m`")
        if st.session_state.get("auth_method"):
            st.caption(f"Auth method: `{st.session_state.auth_method}`")
        if st.button("Logout", use_container_width=True):
            _append_ui_audit("logout", {})
            _clear_auth_session(cfg)
            st.rerun()

        _render_admin_sidebar(cfg, st.session_state.auth_user)

        st.markdown("---")
        st.markdown("### Data Source")
        is_admin_user = _role_at_least(user_role, ROLE_ADMIN)
        strict_isolation = True
        if is_admin_user:
            strict_isolation = st.checkbox(
                "Strict User DB Isolation",
                value=bool(st.session_state.get("ui_strict_user_db", True)),
                key="ui_strict_user_db",
                help="When ON, runtime/data view is locked to signed-in user's isolated DB path.",
            )
        else:
            st.session_state["ui_strict_user_db"] = True
            st.caption("Strict isolation enforced for non-admin users.")
        pref_default_db = str(user_prefs.get("default_db_path", "") or "").strip() if isinstance(user_prefs, dict) else ""
        default_db = pref_default_db or user_default_db
        _ensure_runtime_db_min_schema(user_default_db)
        _ensure_runtime_db_min_schema(default_db)
        candidates = _discover_db_candidates(current_user=current_user)
        options: list[str] = []
        for item in [default_db, *candidates]:
            path_item = str(item or "").strip()
            if path_item and path_item not in options:
                options.append(path_item)
        if not options:
            options = [default_db]
        default_index = options.index(default_db) if default_db in options else 0
        if st.session_state.get("sidebar_loaded_for_user", "") != current_user:
            st.session_state.sidebar_custom_db = default_db
            st.session_state.sidebar_event_limit = int(user_prefs.get("event_limit", 3000)) if isinstance(user_prefs, dict) else 3000
            st.session_state.sidebar_loaded_for_user = current_user
        st.caption(f"User-isolated DB: `{user_default_db}`")
        if strict_isolation:
            st.text_input("Active Runtime DB (locked)", value=user_default_db, disabled=True, key="sidebar_user_locked_db")
            selected = user_default_db
            custom = ""
            db_path = user_default_db
        else:
            selected = st.selectbox("Detected DB Files", options=options, index=default_index, key="sidebar_detected_db")
            custom = st.text_input("Custom DB Path (optional)", value=st.session_state.sidebar_custom_db, key="sidebar_custom_db")
            db_path = custom.strip() or selected
            if (not is_admin_user) and (str(db_path).strip() != user_default_db):
                st.warning("Non-admin users cannot view other users' DBs. Reverting to user-isolated DB.")
                db_path = user_default_db
        selected_owner = _db_owner_from_path(db_path)
        if selected_owner and selected_owner != _user_slug(current_user):
            if is_admin_user and (not strict_isolation):
                st.warning(f"Cross-user view enabled: currently viewing owner `{selected_owner}`.")
            else:
                db_path = user_default_db
                st.warning("DB owner mismatch detected; switched back to your isolated DB.")
        schema_ok, _schema_msg = _ensure_runtime_db_min_schema(db_path)
        if not schema_ok:
            st.warning("Selected DB path is not writable/readable for schema bootstrap.")

        event_limit = st.slider("Event rows to load", min_value=200, max_value=10000, value=int(st.session_state.sidebar_event_limit), step=100, key="sidebar_event_limit")
        refresh = st.button("Refresh Now", use_container_width=True, disabled=not _role_at_least(user_role, ROLE_OPERATOR))
        if refresh:
            _load_runtime_db.clear()
            _append_ui_audit("refresh_runtime_db", {"db_path": db_path})
            st.rerun()

        with st.expander("Runtime Launcher (User-Isolated)", expanded=False):
            st.caption("Start runtime loop with a guaranteed user-isolated `--state-db` path.")
            launch_mode = st.selectbox("Mode", options=["paper", "live"], index=0, key="ui_runtime_launch_mode")
            launch_execute = st.checkbox("Include --execute-orders", value=True, key="ui_runtime_launch_execute")
            st.code(
                "\n".join(
                    _runtime_command_lines(
                        mode=launch_mode,
                        state_db=user_default_db,
                        execute_orders=bool(launch_execute),
                    )
                ),
                language="bash",
            )
            if (not strict_isolation) and is_admin_user and str(db_path).strip() != user_default_db:
                st.caption("Current view DB differs from isolated path. Separate command for current view:")
                st.code(
                    "\n".join(
                        _runtime_command_lines(
                            mode=launch_mode,
                            state_db=db_path,
                            execute_orders=bool(launch_execute),
                        )
                    ),
                    language="bash",
                )
        if is_admin_user:
            with st.expander("Multi-Account Switchboard (Admin)", expanded=False):
                profiles = _account_profiles_from_env()
                if not profiles:
                    st.caption("Set `SWITCH_UI_ACCOUNTS_JSON` env var with a JSON list of account profiles.")
                    st.code(
                        '[{"name":"paper-main","mode":"paper","data_feed":"sip","strategy_profile":"aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m","state_db":"/abs/path/runtime.db"}]',
                        language="json",
                    )
                else:
                    names = [p["name"] for p in profiles]
                    pick = st.selectbox("Account Profile", options=names, index=0, key="admin_account_profile_pick")
                    selected_prof = next((p for p in profiles if p["name"] == pick), profiles[0])
                    state_db_prof = selected_prof.get("state_db") or user_default_db
                    cmd_lines = _runtime_command_lines(
                        mode=str(selected_prof.get("mode", "paper")),
                        state_db=str(state_db_prof),
                        execute_orders=True,
                        strategy_profile=str(selected_prof.get("strategy_profile", "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m")),
                    )
                    # keep data-feed explicit from profile
                    cmd_lines = [ln if "--data-feed" not in ln else f"  --data-feed {str(selected_prof.get('data_feed', 'sip'))} \\" for ln in cmd_lines]
                    st.code("\n".join(cmd_lines), language="bash")
                    st.caption(
                        f"profile={selected_prof.get('name')} | mode={selected_prof.get('mode')} | feed={selected_prof.get('data_feed')} | state_db={state_db_prof}"
                    )

        with st.expander("Auto Refresh", expanded=False):
            st.caption("Continuously refresh dashboard views while monitoring runtime activity.")
            auto_enabled = st.checkbox("Enable Auto Refresh", value=bool(st.session_state.ui_auto_refresh_enabled), key="ui_auto_refresh_enabled")
            interval = st.selectbox(
                "Interval",
                options=[5, 15, 30, 60, 120],
                index=[5, 15, 30, 60, 120].index(int(st.session_state.ui_auto_refresh_interval_sec))
                if int(st.session_state.ui_auto_refresh_interval_sec) in [5, 15, 30, 60, 120]
                else 2,
                key="ui_auto_refresh_interval_sec",
                disabled=not auto_enabled,
            )
            st.caption(
                f"Status: {'ON' if auto_enabled else 'OFF'}"
                + (f" every {int(interval)}s" if auto_enabled else "")
            )
        with st.expander("Live Stream Mode", expanded=False):
            st.caption("Realtime UI mode with SSE support and polling fallback.")
            stream_enabled = st.checkbox(
                "Enable Live Stream Mode",
                value=bool(st.session_state.get("ui_live_stream_enabled", False)),
                key="ui_live_stream_enabled",
            )
            transport = st.selectbox(
                "Transport",
                options=list(STREAM_TRANSPORT_OPTIONS),
                index=list(STREAM_TRANSPORT_OPTIONS).index(str(st.session_state.get("ui_live_stream_transport", "Polling")))
                if str(st.session_state.get("ui_live_stream_transport", "Polling")) in STREAM_TRANSPORT_OPTIONS
                else 0,
                key="ui_live_stream_transport",
                disabled=not stream_enabled,
            )
            st.selectbox(
                "Live Tick Interval (sec)",
                options=[1, 2, 3, 5, 10, 15, 30],
                index=[1, 2, 3, 5, 10, 15, 30].index(int(st.session_state.get("ui_live_stream_interval_sec", 5)))
                if int(st.session_state.get("ui_live_stream_interval_sec", 5)) in [1, 2, 3, 5, 10, 15, 30]
                else 3,
                key="ui_live_stream_interval_sec",
                disabled=not stream_enabled,
            )
            st.text_input(
                "SSE URL (optional)",
                value=str(st.session_state.get("ui_live_stream_sse_url", "") or ""),
                key="ui_live_stream_sse_url",
                disabled=not (stream_enabled and str(transport).startswith("SSE")),
                placeholder="http://host:port/events",
            )
            st.caption(
                f"Live mode: {'ON' if stream_enabled else 'OFF'} | "
                f"transport={transport if stream_enabled else '-'}"
            )

        with st.expander("Preferences", expanded=False):
            st.caption("Saved per signed-in user.")
            st.selectbox(
                "Preferred Workspace",
                options=["Tradeboard", "Portfolio Pulse", "Execution Journal", "Strategy Lab", "P&L Monitor", "Notifications", "Ops Health", "Audit Trail", "Operator Guide"],
                index=["Tradeboard", "Portfolio Pulse", "Execution Journal", "Strategy Lab", "P&L Monitor", "Notifications", "Ops Health", "Audit Trail", "Operator Guide"].index(str(st.session_state.get("ui_saved_workspace", "Tradeboard")) if str(st.session_state.get("ui_saved_workspace", "Tradeboard")) in ["Tradeboard", "Portfolio Pulse", "Execution Journal", "Strategy Lab", "P&L Monitor", "Notifications", "Ops Health", "Audit Trail", "Operator Guide"] else "Tradeboard"),
                key="ui_saved_workspace",
            )
            compact_mode = st.checkbox(
                "Compact table density",
                value=bool(user_prefs.get("compact_mode", True)),
                key="pref_compact_mode",
            )
            st.checkbox(
                "Mobile Compact Mode",
                value=bool(st.session_state.get("ui_mobile_compact_mode", False)),
                key="ui_mobile_compact_mode",
                help="Uses smaller table pages and denser controls for smaller screens.",
            )
            save_prefs = st.button("Save Preferences", use_container_width=True, key="save_user_prefs")
            if save_prefs:
                next_all = all_prefs if isinstance(all_prefs, dict) else {}
                ui_state = {k: st.session_state.get(k) for k in USER_UI_PREF_KEYS}
                next_all[current_user] = {
                    "default_db_path": db_path,
                    "event_limit": int(event_limit),
                    "compact_mode": bool(compact_mode),
                    "ui_state": ui_state,
                }
                ok, msg = _save_prefs(next_all)
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()
            st.markdown("---")
            st.caption("Saved Layouts")
            layout_name = st.text_input("Layout Name", value="", key="ui_layout_name")
            if st.button("Save Current Layout", use_container_width=True, key="ui_save_layout_btn"):
                lname = str(layout_name or "").strip()
                if not lname:
                    st.warning("Enter a layout name.")
                else:
                    next_all = all_prefs if isinstance(all_prefs, dict) else {}
                    user_block = next_all.get(current_user, {}) if isinstance(next_all.get(current_user, {}), dict) else {}
                    layouts = user_block.get("layouts", {}) if isinstance(user_block.get("layouts", {}), dict) else {}
                    layouts[lname] = {
                        "saved_at_ny": pd.Timestamp.now(tz=NY_TZ).isoformat(),
                        "ui_state": {k: st.session_state.get(k) for k in USER_UI_PREF_KEYS},
                        "default_db_path": db_path,
                        "event_limit": int(event_limit),
                    }
                    user_block["layouts"] = layouts
                    user_block["default_db_path"] = db_path
                    user_block["event_limit"] = int(event_limit)
                    user_block["compact_mode"] = bool(compact_mode)
                    user_block["ui_state"] = {k: st.session_state.get(k) for k in USER_UI_PREF_KEYS}
                    next_all[current_user] = user_block
                    ok, msg = _save_prefs(next_all)
                    _append_ui_audit("layout_save", {"name": lname, "ok": ok})
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.rerun()

            stored_layouts = user_prefs.get("layouts", {}) if isinstance(user_prefs, dict) and isinstance(user_prefs.get("layouts", {}), dict) else {}
            layout_opts = sorted(stored_layouts.keys())
            if layout_opts:
                selected_layout = st.selectbox("Load Layout", options=layout_opts, index=0, key="ui_load_layout_name")
                l1, l2 = st.columns(2)
                with l1:
                    if st.button("Apply Layout", use_container_width=True, key="ui_apply_layout_btn"):
                        payload = stored_layouts.get(selected_layout, {})
                        ui_state = payload.get("ui_state", {}) if isinstance(payload, dict) else {}
                        if not isinstance(ui_state, dict):
                            ui_state = {}
                        st.session_state["ui_layout_pending_load"] = ui_state
                        if isinstance(payload, dict):
                            if payload.get("default_db_path"):
                                st.session_state["ui_pending_sidebar_custom_db"] = str(payload.get("default_db_path"))
                            if payload.get("event_limit") is not None:
                                try:
                                    st.session_state["ui_pending_sidebar_event_limit"] = int(payload.get("event_limit"))
                                except Exception:
                                    pass
                        st.success(f"Applied layout: {selected_layout}")
                        _append_ui_audit("layout_apply", {"name": selected_layout})
                        st.rerun()
                with l2:
                    if st.button("Delete Layout", use_container_width=True, key="ui_delete_layout_btn"):
                        next_all = all_prefs if isinstance(all_prefs, dict) else {}
                        user_block = next_all.get(current_user, {}) if isinstance(next_all.get(current_user, {}), dict) else {}
                        layouts = user_block.get("layouts", {}) if isinstance(user_block.get("layouts", {}), dict) else {}
                        layouts.pop(selected_layout, None)
                        user_block["layouts"] = layouts
                        next_all[current_user] = user_block
                        ok, msg = _save_prefs(next_all)
                        _append_ui_audit("layout_delete", {"name": selected_layout, "ok": ok})
                        (st.success if ok else st.error)(msg)
                        if ok:
                            st.rerun()

            st.markdown("---")
            st.caption("Workspace Profiles")
            ws_profile_name = st.text_input("Workspace Profile Name", value="", key="ui_workspace_profile_name")
            sp1, sp2, sp3 = st.columns(3)
            with sp1:
                if st.button("Save Profile", use_container_width=True, key="ui_workspace_profile_save"):
                    pname = str(ws_profile_name or "").strip()
                    if not pname:
                        st.warning("Enter a workspace profile name.")
                    else:
                        next_all = all_prefs if isinstance(all_prefs, dict) else {}
                        user_block = next_all.get(current_user, {}) if isinstance(next_all.get(current_user, {}), dict) else {}
                        profiles = user_block.get("workspace_profiles", {}) if isinstance(user_block.get("workspace_profiles", {}), dict) else {}
                        profiles[pname] = {
                            "saved_at_ny": pd.Timestamp.now(tz=NY_TZ).isoformat(),
                            "workspace": str(st.session_state.get("ui_saved_workspace", "Tradeboard")),
                            "default_db_path": db_path,
                            "event_limit": int(event_limit),
                            "ui_state": {k: st.session_state.get(k) for k in USER_UI_PREF_KEYS},
                        }
                        user_block["workspace_profiles"] = profiles
                        next_all[current_user] = user_block
                        ok, msg = _save_prefs(next_all)
                        _append_ui_audit("workspace_profile_save", {"name": pname, "ok": ok})
                        (st.success if ok else st.error)(msg)
                        if ok:
                            st.rerun()
            profiles_now = user_prefs.get("workspace_profiles", {}) if isinstance(user_prefs, dict) and isinstance(user_prefs.get("workspace_profiles", {}), dict) else {}
            profile_opts = sorted(list(profiles_now.keys()))
            if profile_opts:
                with sp2:
                    selected_profile = st.selectbox("Profile", options=profile_opts, index=0, key="ui_workspace_profile_pick")
                with sp3:
                    if st.button("Apply Profile", use_container_width=True, key="ui_workspace_profile_apply"):
                        payload = profiles_now.get(selected_profile, {})
                        ui_state = payload.get("ui_state", {}) if isinstance(payload, dict) else {}
                        if not isinstance(ui_state, dict):
                            ui_state = {}
                        st.session_state["ui_layout_pending_load"] = ui_state
                        if isinstance(payload, dict):
                            if payload.get("default_db_path"):
                                st.session_state["ui_pending_sidebar_custom_db"] = str(payload.get("default_db_path"))
                            if payload.get("event_limit") is not None:
                                try:
                                    st.session_state["ui_pending_sidebar_event_limit"] = int(payload.get("event_limit"))
                                except Exception:
                                    pass
                            if payload.get("workspace"):
                                st.session_state["ui_saved_workspace"] = str(payload.get("workspace"))
                        _append_ui_audit("workspace_profile_apply", {"name": selected_profile})
                        st.success(f"Applied workspace profile: {selected_profile}")
                        st.rerun()
                if st.button("Delete Profile", use_container_width=True, key="ui_workspace_profile_delete"):
                    next_all = all_prefs if isinstance(all_prefs, dict) else {}
                    user_block = next_all.get(current_user, {}) if isinstance(next_all.get(current_user, {}), dict) else {}
                    profiles = user_block.get("workspace_profiles", {}) if isinstance(user_block.get("workspace_profiles", {}), dict) else {}
                    selected_profile = str(st.session_state.get("ui_workspace_profile_pick", profile_opts[0]) or profile_opts[0])
                    profiles.pop(selected_profile, None)
                    user_block["workspace_profiles"] = profiles
                    next_all[current_user] = user_block
                    ok, msg = _save_prefs(next_all)
                    _append_ui_audit("workspace_profile_delete", {"name": selected_profile, "ok": ok})
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.rerun()

    state_df, events_df = _load_runtime_db(db_path=db_path, event_limit=event_limit)
    notices_df = _notification_center_table(events_df, state_df)

    with st.sidebar:
        st.markdown("---")
        st.markdown("### Notifications")
        if notices_df.empty:
            st.caption("No alerts")
        else:
            st.caption(
                f"High: {int((notices_df['severity'] == 'high').sum())} | "
                f"Medium: {int((notices_df['severity'] == 'medium').sum())} | "
                f"Info: {int((notices_df['severity'] == 'info').sum())}"
            )
            for _, row in notices_df.head(3).iterrows():
                st.caption(f"[{str(row.get('severity')).upper()}] {str(row.get('title'))}")

    events_global = _global_filter_toolbar(events_df)

    live_mode_enabled = bool(st.session_state.get("ui_live_stream_enabled", False))
    if live_mode_enabled:
        _live_stream_pulse(
            enabled=True,
            transport=str(st.session_state.get("ui_live_stream_transport", "Polling") or "Polling"),
            interval_seconds=int(st.session_state.get("ui_live_stream_interval_sec", 5) or 5),
            sse_url=str(st.session_state.get("ui_live_stream_sse_url", "") or ""),
        )
    else:
        _auto_refresh_pulse(
            enabled=bool(st.session_state.get("ui_auto_refresh_enabled", False)),
            interval_seconds=int(st.session_state.get("ui_auto_refresh_interval_sec", 30)),
        )

    _render_runtime_health_banner(cfg, db_path=db_path, events_df=events_global, state_df=state_df)
    _render_banner(db_path=db_path, state_df=state_df, events_df=events_global)
    _render_status_bar(events_df=events_global, state_df=state_df)
    _render_operator_brief(events_df=events_global, state_df=state_df)
    _render_kpis(events_df=events_global, state_df=state_df)
    _render_persistent_pnl_tiles(events_df=events_global, state_df=state_df)
    st.caption(f"Preferred Workspace: `{st.session_state.get('ui_saved_workspace', 'Tradeboard')}` | Tip: pin browser tab with this dashboard open on your preferred tab.")

    tab_specs: list[tuple[str, str]] = [
        ("Tradeboard", ROLE_VIEWER),
        ("Portfolio Pulse", ROLE_VIEWER),
        ("Execution Journal", ROLE_OPERATOR),
        ("Strategy Lab", ROLE_VIEWER),
        ("P&L Monitor", ROLE_VIEWER),
        ("Notifications", ROLE_VIEWER),
        ("Ops Health", ROLE_OPERATOR),
        ("Audit Trail", ROLE_ADMIN),
        ("Operator Guide", ROLE_VIEWER),
        ("Backtest Hub", ROLE_VIEWER),
    ]
    allowed_tabs = [name for name, min_role in tab_specs if _role_at_least(user_role, min_role)]
    tab_objs = st.tabs(allowed_tabs)

    for tab_obj, tab_name in zip(tab_objs, allowed_tabs, strict=False):
        with tab_obj:
            if tab_name == "Tradeboard":
                use_global = st.checkbox("Use Global Filters (Tradeboard)", value=True, key="ui_ws_use_global_tradeboard")
                _render_terminal_workspace(events_df=(events_global if use_global else events_df), state_df=state_df)
            elif tab_name == "Portfolio Pulse":
                use_global = st.checkbox("Use Global Filters (Portfolio Pulse)", value=True, key="ui_ws_use_global_pulse")
                _render_live_overview_workspace(events_df=(events_global if use_global else events_df), state_df=state_df)
            elif tab_name == "Execution Journal":
                use_global = st.checkbox("Use Global Filters (Execution Journal)", value=True, key="ui_ws_use_global_exec")
                _render_execution_orders_workspace(events_df=(events_global if use_global else events_df), state_df=state_df)
            elif tab_name == "Strategy Lab":
                use_global = st.checkbox("Use Global Filters (Strategy Lab)", value=True, key="ui_ws_use_global_strategy")
                _render_strategy_analytics_workspace(events_df=(events_global if use_global else events_df), state_df=state_df)
            elif tab_name == "P&L Monitor":
                use_global = st.checkbox("Use Global Filters (P&L Monitor)", value=True, key="ui_ws_use_global_pnl")
                _render_pnl_monitor_workspace(events_df=(events_global if use_global else events_df), state_df=state_df)
            elif tab_name == "Notifications":
                use_global = st.checkbox("Use Global Filters (Notifications)", value=True, key="ui_ws_use_global_notice")
                _render_notifications_workspace(events_df=(events_global if use_global else events_df), state_df=state_df)
            elif tab_name == "Ops Health":
                use_global = st.checkbox("Use Global Filters (Ops Health)", value=True, key="ui_ws_use_global_health")
                _render_system_health_workspace(events_df=(events_global if use_global else events_df), state_df=state_df, db_path=db_path)
            elif tab_name == "Audit Trail":
                use_global = st.checkbox("Use Global Filters (Audit Trail)", value=True, key="ui_ws_use_global_audit")
                _render_audit_workspace(events_df=(events_global if use_global else events_df), state_df=state_df)
            elif tab_name == "Operator Guide":
                _render_runbook_tab(
                    db_path=db_path,
                    current_user=current_user,
                    user_default_db=user_default_db,
                    strict_isolation=bool(st.session_state.get("ui_strict_user_db", True)),
                )
            elif tab_name == "Backtest Hub":
                use_global = st.checkbox("Use Global Filters (Backtest Hub)", value=True, key="ui_ws_use_global_backtest")
                _render_backtest_import_hub(events_df=(events_global if use_global else events_df), state_df=state_df)


if __name__ == "__main__":
    _main_app()
