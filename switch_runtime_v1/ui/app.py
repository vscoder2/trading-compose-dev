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
import re
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


def _arrow_safe_scalar(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, (dict, list, tuple, set)):
        try:
            return json.dumps(value, sort_keys=True, default=str)
        except Exception:
            return str(value)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return str(value)
    if isinstance(value, (datetime, pd.Timestamp)):
        try:
            return pd.Timestamp(value).isoformat()
        except Exception:
            return str(value)
    return value


def _sanitize_dataframe_for_streamlit(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df
    out = df.copy()
    obj_cols = out.select_dtypes(include=["object"]).columns.tolist()
    for col in obj_cols:
        series = out[col].map(_arrow_safe_scalar)
        non_null = [v for v in series.head(500).tolist() if v is not None]
        if non_null:
            types = {type(v) for v in non_null}
            # Arrow conversion is brittle for mixed object columns; stringify to keep UI stable.
            if len(types) > 1:
                series = series.map(lambda v: None if v is None else str(v))
        out[col] = series
    return out


_ORIGINAL_ST_DATAFRAME = st.dataframe


def _safe_st_dataframe(data: Any = None, *args: Any, **kwargs: Any) -> Any:
    if isinstance(data, pd.DataFrame):
        data = _sanitize_dataframe_for_streamlit(data)
    elif isinstance(data, pd.Series):
        data = _sanitize_dataframe_for_streamlit(data.to_frame(name=data.name or "value"))
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        data = _sanitize_dataframe_for_streamlit(pd.DataFrame(data))
    try:
        return _ORIGINAL_ST_DATAFRAME(data, *args, **kwargs)
    except Exception:
        if isinstance(data, pd.DataFrame):
            fallback = data.copy()
            for col in fallback.columns:
                fallback[col] = fallback[col].map(_arrow_safe_scalar).map(lambda v: None if v is None else str(v))
            return _ORIGINAL_ST_DATAFRAME(fallback, *args, **kwargs)
        raise


st.dataframe = _safe_st_dataframe


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
WORKSPACE_ORDER = [
    "Command Center",
    "Tradeboard",
    "Multi-Chart",
    "Portfolio Pulse",
    "Execution Journal",
    "Strategy Lab",
    "P&L Monitor",
    "Notifications",
    "Ops Health",
    "UI Diagnostics",
    "Audit Trail",
    "Operator Guide",
    "Backtest Hub",
    "UI Changelog",
]
THEME_PRESETS: dict[str, dict[str, str]] = {
    "Neo Green": {
        "accent": "#00c805",
        "accent2": "#3cd95b",
        "accent3": "#99ec5b",
        "link": "#5eead4",
    },
    "Ocean Blue": {
        "accent": "#3b82f6",
        "accent2": "#60a5fa",
        "accent3": "#93c5fd",
        "link": "#67e8f9",
    },
    "Amber Pro": {
        "accent": "#f59e0b",
        "accent2": "#fbbf24",
        "accent3": "#fcd34d",
        "link": "#fde68a",
    },
    "Mono Slate": {
        "accent": "#94a3b8",
        "accent2": "#cbd5e1",
        "accent3": "#e2e8f0",
        "link": "#c7d2fe",
    },
    "Pro Dark": {
        "accent": "#22c55e",
        "accent2": "#4ade80",
        "accent3": "#86efac",
        "link": "#67e8f9",
    },
    "Institutional Slate": {
        "accent": "#38bdf8",
        "accent2": "#7dd3fc",
        "accent3": "#bae6fd",
        "link": "#c4b5fd",
    },
    "Neon Grid": {
        "accent": "#00e5ff",
        "accent2": "#00ffa8",
        "accent3": "#ccff00",
        "link": "#7dd3fc",
    },
    "Robinhood Terminal": {
        "accent": "#00c805",
        "accent2": "#42d66a",
        "accent3": "#8be88f",
        "link": "#93f5b0",
    },
    "Webull Slate": {
        "accent": "#2ea7ff",
        "accent2": "#6dc5ff",
        "accent3": "#a8dcff",
        "link": "#8fd6ff",
    },
    "Kraken Pro": {
        "accent": "#6f54ff",
        "accent2": "#9a86ff",
        "accent3": "#c8bfff",
        "link": "#8eb3ff",
    },
}
DENSITY_MODES = ("Comfortable", "Compact", "Ultra Compact")
USER_UI_PREF_KEYS = (
    "ui_watchlist_pins",
    "ui_watchlist_pinned_only",
    "ui_watchlist_min_events",
    "ui_watchlist_sort_mode",
    "ui_watchlist_card_density",
    "ui_watchlist_quick_actions",
    "ui_time_window",
    "ui_eval_time",
    "ui_sync_charts",
    "ui_symbol_view_mode",
    "ui_symbol_chart_type",
    "ui_linked_cursor_enabled",
    "ui_linked_cursor_ts",
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
    "ui_global_filter_presets",
    "ui_saved_workspace",
    "ui_workspace_view_mode",
    "ui_workspace_nav_selection",
    "ui_live_stream_enabled",
    "ui_live_stream_transport",
    "ui_live_stream_interval_sec",
    "ui_live_stream_sse_url",
    "ui_mobile_compact_mode",
    "ui_alert_rules",
    "ui_theme_preset",
    "ui_density_mode",
    "ui_font_scale_pct",
    "ui_high_contrast",
    "ui_focus_mode",
    "ui_keyboard_shortcuts_enabled",
    "ui_tradeboard_split",
    "ui_execution_split",
    "ui_tradeboard_custom_weights",
    "ui_execution_custom_weights",
    "ui_incident_mode",
    "ui_mobile_bottom_nav",
    "ui_workspace_preset",
    "ui_workspace_visible_tabs",
    "ui_command_history",
    "ui_command_favorites",
    "ui_layout_editor_enabled",
    "ui_panel_layout_locked",
    "ui_popout_panel",
    "ui_accessibility_preset",
    "ui_reduced_motion",
    "ui_perf_mode_enabled",
    "ui_perf_max_rows",
    "ui_persona_mode",
    "ui_home_tiles",
    "ui_show_onboarding",
    "ui_saved_views",
    "ui_fast_widget_cadence_sec",
    "ui_heavy_widget_cadence_sec",
    "ui_minimal_mode",
)


def _normalize_eval_time(value: Any, default: str = "15:55") -> str:
    raw = str(value or "").strip()
    if not re.fullmatch(r"\d{2}:\d{2}", raw):
        return default
    hh_txt, mm_txt = raw.split(":", 1)
    try:
        hh = int(hh_txt)
        mm = int(mm_txt)
    except Exception:
        return default
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return f"{hh:02d}:{mm:02d}"
    return default


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
    eval_time: str | None = None,
) -> list[str]:
    eval_time_txt = _normalize_eval_time(eval_time if eval_time is not None else st.session_state.get("ui_eval_time", "15:55"))
    lines = [
        "/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \\",
        "  /home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py \\",
        "  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \\",
        "  --env-override \\",
        f"  --mode {str(mode).strip()} \\",
        f"  --strategy-profile {strategy_profile} \\",
        "  --data-feed sip \\",
        f"  --eval-time {eval_time_txt} \\",
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
    try:
        min_ev = int(pd.to_numeric(ui.get("ui_watchlist_min_events", 0), errors="coerce") or 0)
    except Exception:
        min_ev = 0
    out["ui_watchlist_min_events"] = max(0, min(5000, min_ev))
    sort_mode = str(ui.get("ui_watchlist_sort_mode", "Activity") or "Activity")
    out["ui_watchlist_sort_mode"] = sort_mode if sort_mode in {"Activity", "Target Weight", "Alphabetical"} else "Activity"
    card_density = str(ui.get("ui_watchlist_card_density", "Standard") or "Standard")
    out["ui_watchlist_card_density"] = card_density if card_density in {"Compact", "Standard", "Expanded"} else "Standard"
    out["ui_watchlist_quick_actions"] = bool(ui.get("ui_watchlist_quick_actions", True))
    tw = str(ui.get("ui_time_window", "1D") or "1D")
    out["ui_time_window"] = tw if tw in TIME_WINDOW_OPTIONS else "1D"
    out["ui_eval_time"] = _normalize_eval_time(ui.get("ui_eval_time", "15:55"), default="15:55")
    out["ui_sync_charts"] = bool(ui.get("ui_sync_charts", True))
    view = str(ui.get("ui_symbol_view_mode", "Intraday Activity") or "Intraday Activity")
    out["ui_symbol_view_mode"] = view if view in {"Intraday Activity", "Lifecycle", "Event Mix"} else "Intraday Activity"
    chart_type = str(ui.get("ui_symbol_chart_type", "Line") or "Line")
    out["ui_symbol_chart_type"] = chart_type if chart_type in {"Line", "Bar", "Candles"} else "Line"
    out["ui_linked_cursor_enabled"] = bool(ui.get("ui_linked_cursor_enabled", True))
    cursor_ts = str(ui.get("ui_linked_cursor_ts", "") or "")
    out["ui_linked_cursor_ts"] = cursor_ts
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
    raw_presets = ui.get("ui_global_filter_presets", {})
    out["ui_global_filter_presets"] = raw_presets if isinstance(raw_presets, dict) else {}
    out["ui_saved_workspace"] = str(ui.get("ui_saved_workspace", "Tradeboard") or "Tradeboard")
    vm = str(ui.get("ui_workspace_view_mode", "Single Workspace") or "Single Workspace")
    out["ui_workspace_view_mode"] = vm if vm in {"Single Workspace", "Tabbed Workspace"} else "Single Workspace"
    nav = str(ui.get("ui_workspace_nav_selection", out["ui_saved_workspace"]) or out["ui_saved_workspace"])
    out["ui_workspace_nav_selection"] = nav if nav in WORKSPACE_ORDER else out["ui_saved_workspace"]
    out["ui_live_stream_enabled"] = bool(ui.get("ui_live_stream_enabled", False))
    tr = str(ui.get("ui_live_stream_transport", "Polling") or "Polling")
    out["ui_live_stream_transport"] = tr if tr in STREAM_TRANSPORT_OPTIONS else "Polling"
    live_interval = int(pd.to_numeric(ui.get("ui_live_stream_interval_sec", 5), errors="coerce") or 5)
    out["ui_live_stream_interval_sec"] = max(1, min(60, live_interval))
    out["ui_live_stream_sse_url"] = str(ui.get("ui_live_stream_sse_url", "") or "")
    out["ui_mobile_compact_mode"] = bool(ui.get("ui_mobile_compact_mode", False))
    raw_rules = ui.get("ui_alert_rules", [])
    out["ui_alert_rules"] = raw_rules if isinstance(raw_rules, list) else []
    theme = str(ui.get("ui_theme_preset", "Neo Green") or "Neo Green")
    out["ui_theme_preset"] = theme if theme in THEME_PRESETS else "Neo Green"
    density = str(ui.get("ui_density_mode", "Comfortable") or "Comfortable")
    out["ui_density_mode"] = density if density in DENSITY_MODES else "Comfortable"
    try:
        fscale = int(pd.to_numeric(ui.get("ui_font_scale_pct", 100), errors="coerce") or 100)
    except Exception:
        fscale = 100
    out["ui_font_scale_pct"] = max(85, min(130, fscale))
    out["ui_high_contrast"] = bool(ui.get("ui_high_contrast", False))
    out["ui_focus_mode"] = bool(ui.get("ui_focus_mode", False))
    out["ui_keyboard_shortcuts_enabled"] = bool(ui.get("ui_keyboard_shortcuts_enabled", True))
    tb_split = str(ui.get("ui_tradeboard_split", "Balanced") or "Balanced")
    out["ui_tradeboard_split"] = tb_split if tb_split in {"Balanced", "Chart Focus", "Watchlist Focus", "Stats Focus", "Custom"} else "Balanced"
    ex_split = str(ui.get("ui_execution_split", "Balanced") or "Balanced")
    out["ui_execution_split"] = ex_split if ex_split in {"Balanced", "Blotter Focus", "Analytics Focus", "Custom"} else "Balanced"
    raw_tbw = ui.get("ui_tradeboard_custom_weights", [0.8, 1.95, 0.75])
    if isinstance(raw_tbw, list) and len(raw_tbw) == 3:
        out["ui_tradeboard_custom_weights"] = [float(pd.to_numeric(v, errors="coerce") or 1.0) for v in raw_tbw]
    else:
        out["ui_tradeboard_custom_weights"] = [0.8, 1.95, 0.75]
    raw_exw = ui.get("ui_execution_custom_weights", [1.15, 1.0])
    if isinstance(raw_exw, list) and len(raw_exw) == 2:
        out["ui_execution_custom_weights"] = [float(pd.to_numeric(v, errors="coerce") or 1.0) for v in raw_exw]
    else:
        out["ui_execution_custom_weights"] = [1.15, 1.0]
    out["ui_incident_mode"] = bool(ui.get("ui_incident_mode", False))
    out["ui_mobile_bottom_nav"] = bool(ui.get("ui_mobile_bottom_nav", False))
    preset = str(ui.get("ui_workspace_preset", "Custom") or "Custom")
    out["ui_workspace_preset"] = preset if preset in {"Custom", "Open", "Close", "Forensics", "Risk", "Execution", "Monitor"} else "Custom"
    raw_tabs = ui.get("ui_workspace_visible_tabs", [])
    if isinstance(raw_tabs, list):
        out["ui_workspace_visible_tabs"] = [str(t) for t in raw_tabs if str(t) in WORKSPACE_ORDER]
    else:
        out["ui_workspace_visible_tabs"] = []
    raw_hist = ui.get("ui_command_history", [])
    out["ui_command_history"] = raw_hist if isinstance(raw_hist, list) else []
    raw_fav = ui.get("ui_command_favorites", [])
    out["ui_command_favorites"] = raw_fav if isinstance(raw_fav, list) else []
    out["ui_layout_editor_enabled"] = bool(ui.get("ui_layout_editor_enabled", False))
    out["ui_panel_layout_locked"] = bool(ui.get("ui_panel_layout_locked", False))
    out["ui_popout_panel"] = str(ui.get("ui_popout_panel", "") or "")
    ap = str(ui.get("ui_accessibility_preset", "Default") or "Default")
    out["ui_accessibility_preset"] = ap if ap in {"Default", "High Contrast", "Large Text", "Reduced Motion"} else "Default"
    out["ui_reduced_motion"] = bool(ui.get("ui_reduced_motion", False))
    out["ui_perf_mode_enabled"] = bool(ui.get("ui_perf_mode_enabled", False))
    try:
        out["ui_perf_max_rows"] = int(pd.to_numeric(ui.get("ui_perf_max_rows", 3000), errors="coerce") or 3000)
    except Exception:
        out["ui_perf_max_rows"] = 3000
    pm = str(ui.get("ui_persona_mode", "Operator") or "Operator")
    out["ui_persona_mode"] = pm if pm in {"Viewer", "Operator", "Admin"} else "Operator"
    raw_tiles = ui.get("ui_home_tiles", [])
    out["ui_home_tiles"] = raw_tiles if isinstance(raw_tiles, list) else []
    out["ui_show_onboarding"] = bool(ui.get("ui_show_onboarding", True))
    out["ui_minimal_mode"] = bool(ui.get("ui_minimal_mode", False))
    try:
        fast_sec = int(pd.to_numeric(ui.get("ui_fast_widget_cadence_sec", 5), errors="coerce") or 5)
    except Exception:
        fast_sec = 5
    try:
        heavy_sec = int(pd.to_numeric(ui.get("ui_heavy_widget_cadence_sec", 30), errors="coerce") or 30)
    except Exception:
        heavy_sec = 30
    out["ui_fast_widget_cadence_sec"] = max(1, min(30, fast_sec))
    out["ui_heavy_widget_cadence_sec"] = max(5, min(300, heavy_sec))
    raw_views = ui.get("ui_saved_views", {})
    out["ui_saved_views"] = raw_views if isinstance(raw_views, dict) else {}
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
          .sticky-global {
            position: sticky;
            top: 0.35rem;
            z-index: 15;
            backdrop-filter: blur(10px);
          }
          .top-command-bar {
            border: 1px solid rgba(121, 149, 193, 0.3);
            border-radius: 12px;
            background: linear-gradient(180deg, rgba(10, 18, 28, 0.84), rgba(9, 14, 24, 0.88));
            padding: 0.52rem 0.62rem;
            margin-bottom: 0.66rem;
            box-shadow: 0 10px 20px rgba(0, 0, 0, 0.28);
          }
          .command-palette-modal {
            border: 1px solid rgba(107, 134, 180, 0.34);
            box-shadow: 0 18px 26px rgba(0, 0, 0, 0.34);
            background: linear-gradient(180deg, rgba(8, 14, 24, 0.95), rgba(9, 13, 20, 0.96));
          }
          .quick-nav-block {
            padding-top: 0.5rem;
            padding-bottom: 0.5rem;
          }
          .realtime-ribbon {
            display: flex;
            flex-wrap: wrap;
            gap: 0.35rem;
            border: 1px solid rgba(130, 146, 170, 0.28);
            border-radius: 10px;
            background: rgba(8, 12, 20, 0.78);
            padding: 0.42rem 0.52rem;
            margin-bottom: 0.58rem;
            font-size: 0.76rem;
          }
          .realtime-ribbon span {
            background: rgba(19, 26, 40, 0.72);
            border: 1px solid rgba(119, 132, 153, 0.35);
            border-radius: 999px;
            padding: 0.2rem 0.48rem;
          }
          .toast-center {
            border: 1px solid rgba(139, 154, 176, 0.26);
            border-radius: 11px;
            background: rgba(10, 16, 24, 0.76);
            padding: 0.45rem 0.56rem;
            margin-bottom: 0.7rem;
          }
          .toast-center-title {
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #b7cae8;
            font-size: 0.72rem;
            font-weight: 700;
            margin-bottom: 0.24rem;
          }
          .mobile-bottom-nav {
            position: sticky;
            bottom: 0.35rem;
            z-index: 14;
            border: 1px solid rgba(119, 137, 167, 0.34);
            border-radius: 12px;
            background: rgba(8, 12, 19, 0.86);
            padding: 0.35rem 0.45rem;
            margin-top: 0.5rem;
            backdrop-filter: blur(12px);
          }
          .left-nav-card {
            border: 1px solid rgba(122, 132, 142, 0.2);
            border-radius: 12px;
            background: rgba(10, 15, 22, 0.72);
            padding: 0.58rem 0.62rem;
            margin-bottom: 0.55rem;
          }
          .risk-strip {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.45rem;
            margin-bottom: 0.8rem;
          }
          .risk-pill {
            border: 1px solid rgba(122, 132, 142, 0.24);
            border-radius: 11px;
            background: rgba(10, 15, 22, 0.78);
            padding: 0.46rem 0.55rem;
          }
          .risk-pill-k {
            color: #a9bad7;
            font-size: 0.72rem;
            margin-bottom: 0.11rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
          }
          .risk-pill-v {
            font-family: "IBM Plex Mono", monospace;
            font-size: 0.83rem;
            color: #edf6ff;
            font-weight: 600;
          }
          .risk-dot {
            display: inline-block;
            width: 0.56rem;
            height: 0.56rem;
            border-radius: 999px;
            margin-right: 0.36rem;
          }
          .risk-dot.ok { background: #00c805; box-shadow: 0 0 0 3px rgba(0, 200, 5, 0.15); }
          .risk-dot.warn { background: #f3b13c; box-shadow: 0 0 0 3px rgba(243, 177, 60, 0.15); }
          .risk-dot.bad { background: #ff5d7a; box-shadow: 0 0 0 3px rgba(255, 93, 122, 0.16); }
          .alert-action-center {
            border: 1px solid rgba(107, 134, 180, 0.3);
            border-radius: 12px;
            background: rgba(12, 18, 28, 0.8);
            padding: 0.6rem 0.66rem 0.52rem 0.66rem;
            margin-bottom: 0.85rem;
          }
          .alert-center-title {
            font-size: 0.78rem;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            color: #b5c8e5;
            margin-bottom: 0.2rem;
            font-weight: 700;
          }
          .empty-state-card {
            border: 1px dashed rgba(122, 132, 142, 0.4);
            border-radius: 12px;
            background: rgba(12, 17, 25, 0.62);
            padding: 0.95rem 0.86rem;
            margin: 0.35rem 0 0.4rem 0;
          }
          .empty-title {
            color: #e8f2ff;
            font-weight: 700;
            margin-bottom: 0.18rem;
          }
          .empty-text {
            color: #a9bedf;
            font-size: 0.84rem;
            line-height: 1.32;
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
            align-items: center;
            justify-content: flex-start;
            flex-wrap: wrap;
            gap: 0.24rem;
            font-family: "IBM Plex Mono", monospace;
            font-size: 0.77rem;
            color: #e6f0ff;
            font-weight: 600;
          }
          .watch-item-symbol {
            margin-right: auto;
          }
          .watch-item-sub {
            margin-top: 0.14rem;
            color: #9fb3d5;
            font-size: 0.68rem;
            letter-spacing: 0.01em;
            font-family: "IBM Plex Mono", monospace;
          }
          .watch-item-badge {
            border: 1px solid rgba(134, 156, 189, 0.38);
            border-radius: 999px;
            padding: 0.04rem 0.35rem;
            font-size: 0.7rem;
            font-weight: 600;
            margin-left: 0.18rem;
            background: rgba(11, 17, 27, 0.85);
            color: #cde1ff;
          }
          .watch-item.compact {
            padding: 0.24rem 0.42rem;
          }
          .watch-item.expanded {
            padding: 0.42rem 0.54rem;
          }
          .watch-item-spark-wrap {
            margin-top: 0.12rem;
            border-top: 1px solid rgba(122, 132, 142, 0.14);
            padding-top: 0.12rem;
          }
          .watch-item-spark-caption {
            color: #8ea6cd;
            font-size: 0.65rem;
            font-family: "IBM Plex Mono", monospace;
            margin-top: 0.05rem;
          }
          .quick-action-row {
            display: grid;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            gap: 0.38rem;
            margin-bottom: 0.44rem;
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
          .skeleton-line {
            height: 0.78rem;
            border-radius: 999px;
            background: linear-gradient(90deg, rgba(132, 148, 171, 0.16), rgba(188, 204, 228, 0.3), rgba(132, 148, 171, 0.16));
            background-size: 300% 100%;
            animation: skeletonPulse 1.6s ease-in-out infinite;
            margin-bottom: 0.33rem;
          }
          @keyframes skeletonPulse {
            0% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
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
            transition: transform 120ms ease, border-color 120ms ease, box-shadow 120ms ease;
          }
          .desk-block:hover {
            transform: translateY(-1px);
            border-color: rgba(122, 132, 142, 0.3);
            box-shadow: 0 14px 24px rgba(0, 0, 0, 0.3);
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
            transition: transform 110ms ease, box-shadow 110ms ease, filter 110ms ease;
          }
          .stButton button:hover {
            transform: translateY(-1px);
            box-shadow: 0 10px 18px rgba(0, 0, 0, 0.26);
            filter: saturate(1.08);
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
            .risk-strip {
              grid-template-columns: repeat(2, minmax(0, 1fr));
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
            h1, h2, h3 {
              line-height: 1.18 !important;
              letter-spacing: -0.01em !important;
            }
            .desk-block {
              padding: 0.55rem 0.55rem 0.4rem 0.55rem;
              border-radius: 12px;
            }
            .top-command-bar {
              padding: 0.45rem 0.45rem;
            }
            .rh-hero-symbol {
              font-size: 1.2rem !important;
            }
            .rh-stats-grid {
              grid-template-columns: 1fr;
            }
            .risk-strip {
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


def _inject_dynamic_theme_css() -> None:
    preset = str(st.session_state.get("ui_theme_preset", "Neo Green") or "Neo Green")
    colors = THEME_PRESETS.get(preset, THEME_PRESETS["Neo Green"])
    accessibility_preset = str(st.session_state.get("ui_accessibility_preset", "Default") or "Default")
    reduced_motion_pref = bool(st.session_state.get("ui_reduced_motion", False))
    density = str(st.session_state.get("ui_density_mode", "Comfortable") or "Comfortable")
    font_scale_pct = int(pd.to_numeric(st.session_state.get("ui_font_scale_pct", 100), errors="coerce") or 100)
    font_scale_pct = max(85, min(130, font_scale_pct))
    high_contrast = bool(st.session_state.get("ui_high_contrast", False))
    focus_mode = bool(st.session_state.get("ui_focus_mode", False))
    minimal_mode = bool(st.session_state.get("ui_minimal_mode", False))
    incident_mode = bool(st.session_state.get("ui_incident_mode", False))
    if accessibility_preset == "High Contrast":
        high_contrast = True
    if accessibility_preset == "Large Text":
        font_scale_pct = max(font_scale_pct, 115)
    reduced_motion = reduced_motion_pref or accessibility_preset == "Reduced Motion"

    density_css = {
        "Comfortable": """
          .stDataFrame [role="grid"] [role="row"] {
            min-height: 2rem;
          }
        """,
        "Compact": """
          .stDataFrame [role="grid"] [role="row"] {
            min-height: 1.6rem;
          }
          .desk-block {
            padding-top: 0.52rem !important;
            padding-bottom: 0.42rem !important;
          }
        """,
        "Ultra Compact": """
          .stDataFrame [role="grid"] [role="row"] {
            min-height: 1.35rem;
          }
          .desk-block {
            padding-top: 0.42rem !important;
            padding-bottom: 0.34rem !important;
          }
          .section-caption {
            margin-bottom: 0.25rem !important;
          }
        """,
    }.get(density, "")
    contrast_css = """
      .desk-block, .terminal-shell, .terminal-shell-hero, .left-nav-card {
        border-color: rgba(204, 214, 226, 0.68) !important;
      }
      .status-badge, .risk-pill {
        box-shadow: inset 0 0 0 1px rgba(220, 231, 245, 0.34);
      }
    """ if high_contrast else ""
    focus_css = """
      .section-caption, .stCaption {
        opacity: 0.55 !important;
      }
    """ if focus_mode else ""
    incident_css = """
      .top-banner, .desk-block {
        border-color: rgba(255, 93, 122, 0.48) !important;
      }
      .section-caption::before {
        content: "INCIDENT MODE ";
        color: #ff8ba1;
        font-weight: 800;
        margin-right: 0.22rem;
      }
      .status-bar, .realtime-ribbon {
        box-shadow: inset 0 0 0 1px rgba(255, 93, 122, 0.22);
      }
    """ if incident_mode else ""
    reduced_motion_css = """
      *, *::before, *::after {
        animation: none !important;
        transition: none !important;
        scroll-behavior: auto !important;
      }
    """ if reduced_motion else ""
    minimal_css = """
      .top-command-bar,
      .quick-nav-block,
      .toast-center,
      .event-tape,
      .section-caption {
        display: none !important;
      }
      .desk-block {
        margin-bottom: 0.5rem !important;
        padding-top: 0.46rem !important;
        padding-bottom: 0.4rem !important;
      }
      .stCaption {
        opacity: 0.75 !important;
      }
    """ if minimal_mode else ""
    st.markdown(
        f"""
        <style>
          :root {{
            --accent: {colors["accent"]};
            --accent-2: {colors["accent2"]};
            --accent-3: {colors["accent3"]};
          }}
          html, body, .stApp {{
            font-size: {font_scale_pct}%;
          }}
          a {{
            color: {colors["link"]} !important;
          }}
          .stButton button, [data-testid="stSidebar"] .stButton button {{
            background: linear-gradient(90deg, {colors["accent"]}, {colors["accent2"]}) !important;
          }}
          .status-good {{
            border-color: {colors["accent2"]} !important;
          }}
          .status-bar .mode-chip {{
            border-color: {colors["accent3"]} !important;
          }}
          {density_css}
          {contrast_css}
          {focus_css}
          {incident_css}
          {reduced_motion_css}
          {minimal_css}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _query_param_first(name: str) -> str:
    try:
        value = st.query_params.get(name, "")
        if isinstance(value, list):
            return str(value[0] if value else "").strip()
        return str(value or "").strip()
    except Exception:
        return ""


def _clear_query_params_keys(*names: str) -> None:
    try:
        for name in names:
            try:
                st.query_params.pop(name)  # type: ignore[arg-type]
            except Exception:
                try:
                    del st.query_params[name]
                except Exception:
                    pass
    except Exception:
        pass


def _inject_keyboard_shortcuts_js(enabled: bool) -> None:
    if not enabled:
        return
    components.html(
        """
        <script>
          (function () {
            if (window.__switchUiShortcutsBound) return;
            window.__switchUiShortcutsBound = true;
            function emit(code) {
              try {
                const u = new URL(window.parent.location.href);
                u.searchParams.set("ui_kb", code);
                window.parent.location.href = u.toString();
              } catch (e) {}
            }
            window.addEventListener("keydown", function (ev) {
              const key = String(ev.key || "").toLowerCase();
              if ((ev.ctrlKey || ev.metaKey) && key === "k") {
                ev.preventDefault();
                emit("palette");
                return;
              }
              if (ev.altKey && key === "r") {
                ev.preventDefault();
                emit("refresh");
                return;
              }
              if (ev.altKey && ["1","2","3","4","5","6"].includes(key)) {
                ev.preventDefault();
                emit("ws:" + key);
                return;
              }
              if (ev.altKey && key === "t") {
                ev.preventDefault();
                emit("jump:tradeboard");
                return;
              }
              if (ev.altKey && key === "e") {
                ev.preventDefault();
                emit("jump:execution");
                return;
              }
              if (ev.altKey && key === "n") {
                ev.preventDefault();
                emit("jump:notifications");
                return;
              }
              if (ev.altKey && key === "a") {
                ev.preventDefault();
                emit("jump:audit");
                return;
              }
              if (ev.altKey && key === "h") {
                ev.preventDefault();
                emit("jump:home");
              }
            }, true);
          })();
        </script>
        """,
        height=0,
        width=0,
    )


def _handle_keyboard_shortcut_event(user_role: str) -> None:
    code = _query_param_first("ui_kb")
    if not code:
        return
    _clear_query_params_keys("ui_kb")
    if code == "palette":
        st.session_state["ui_global_palette_open"] = True
        st.session_state["ui_palette_status"] = "Shortcut: opened global command palette."
        st.rerun()
    if code == "refresh":
        _load_runtime_db.clear()
        st.session_state["ui_palette_status"] = "Shortcut: runtime data refreshed."
        st.rerun()
    if code.startswith("ws:"):
        idx_raw = code.split(":", 1)[1].strip()
        try:
            idx = int(idx_raw)
        except Exception:
            return
        tabs = _workspace_nav_options(user_role)
        if 1 <= idx <= len(tabs):
            target = str(tabs[idx - 1])
            _queue_workspace_switch(target)
            st.session_state["ui_palette_status"] = f"Shortcut: switched workspace to {target}."
            st.rerun()
    if code.startswith("jump:"):
        token = code.split(":", 1)[1].strip().lower()
        target_map = {
            "home": "Command Center",
            "tradeboard": "Tradeboard",
            "execution": "Execution Journal",
            "notifications": "Notifications",
            "audit": "Audit Trail",
        }
        target = target_map.get(token, "")
        if target and target in _workspace_nav_options(user_role):
            _queue_workspace_switch(target)
            st.session_state["ui_palette_status"] = f"Shortcut: switched workspace to {target}."
            st.rerun()


def _sparkline(values: list[float]) -> str:
    if not values:
        return "-"
    ticks = "▁▂▃▄▅▆▇█"
    lo = min(values)
    hi = max(values)
    if hi <= lo:
        return ticks[0] * min(24, len(values))
    out: list[str] = []
    for v in values[-24:]:
        idx = int((float(v) - lo) / (hi - lo) * (len(ticks) - 1))
        idx = max(0, min(len(ticks) - 1, idx))
        out.append(ticks[idx])
    return "".join(out)


def _symbol_position_sparkline(events_df: pd.DataFrame, symbol: str, points: int = 20) -> str:
    try:
        frame = _symbol_activity_df(events_df, symbol)  # type: ignore[name-defined]
    except Exception:
        return "-"
    if frame.empty:
        return "-"
    vals = pd.to_numeric(frame.get("cum_position"), errors="coerce").dropna().astype(float).tolist()
    if not vals:
        return "-"
    return _sparkline(vals[-max(8, int(points)):])


def _apply_workspace_preset(preset: str) -> None:
    p = str(preset or "Custom").strip()
    st.session_state["ui_workspace_preset"] = p
    if p == "Open":
        st.session_state["ui_pending_saved_workspace"] = "Tradeboard"
        st.session_state["ui_pending_time_window"] = "1D"
        st.session_state["ui_palette_status"] = "Preset applied: Open."
    elif p == "Close":
        st.session_state["ui_pending_saved_workspace"] = "Execution Journal"
        st.session_state["ui_pending_time_window"] = "1D"
        st.session_state["ui_palette_status"] = "Preset applied: Close."
    elif p == "Forensics":
        st.session_state["ui_pending_saved_workspace"] = "Audit Trail"
        st.session_state["ui_pending_time_window"] = "1W"
        st.session_state["ui_palette_status"] = "Preset applied: Forensics."
    elif p == "Risk":
        st.session_state["ui_pending_saved_workspace"] = "Ops Health"
        st.session_state["ui_pending_time_window"] = "1W"
        st.session_state["ui_incident_mode"] = True
        st.session_state["ui_palette_status"] = "Preset applied: Risk."
    elif p == "Execution":
        st.session_state["ui_pending_saved_workspace"] = "Execution Journal"
        st.session_state["ui_pending_time_window"] = "1D"
        st.session_state["ui_symbol_view_mode"] = "Lifecycle"
        st.session_state["ui_palette_status"] = "Preset applied: Execution."
    elif p == "Monitor":
        st.session_state["ui_pending_saved_workspace"] = "Portfolio Pulse"
        st.session_state["ui_pending_time_window"] = "1W"
        st.session_state["ui_palette_status"] = "Preset applied: Monitor."
    else:
        st.session_state["ui_palette_status"] = "Preset switched to Custom."


def _queue_workspace_switch(target: str) -> None:
    selected = str(target or "").strip()
    if not selected:
        return
    st.session_state["ui_pending_workspace_nav_selection"] = selected
    st.session_state["ui_pending_saved_workspace"] = selected


def _render_workspace_preset_chips() -> None:
    st.markdown("<div class='sticky-global'><div class='desk-block'>", unsafe_allow_html=True)
    st.markdown("<div class='section-caption'>Workspace Presets</div>", unsafe_allow_html=True)
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    with c1:
        if st.button("Open", use_container_width=True, key="ws_preset_open"):
            _apply_workspace_preset("Open")
            st.rerun()
    with c2:
        if st.button("Close", use_container_width=True, key="ws_preset_close"):
            _apply_workspace_preset("Close")
            st.rerun()
    with c3:
        if st.button("Forensics", use_container_width=True, key="ws_preset_forensics"):
            _apply_workspace_preset("Forensics")
            st.rerun()
    with c4:
        if st.button("Risk", use_container_width=True, key="ws_preset_risk"):
            _apply_workspace_preset("Risk")
            st.rerun()
    with c5:
        if st.button("Execution", use_container_width=True, key="ws_preset_execution"):
            _apply_workspace_preset("Execution")
            st.rerun()
    with c6:
        if st.button("Monitor", use_container_width=True, key="ws_preset_monitor"):
            _apply_workspace_preset("Monitor")
            st.rerun()
    with c7:
        if st.button("Custom", use_container_width=True, key="ws_preset_custom"):
            _apply_workspace_preset("Custom")
            st.rerun()
    st.markdown("</div></div>", unsafe_allow_html=True)


def _sync_toast_history(notices_df: pd.DataFrame) -> None:
    if notices_df is None or notices_df.empty:
        return
    seen_raw = st.session_state.get("ui_toast_seen_keys", [])
    seen = [str(x) for x in (seen_raw if isinstance(seen_raw, list) else [])]
    seen_set = set(seen)
    hist_raw = st.session_state.get("ui_toast_history", [])
    hist = list(hist_raw) if isinstance(hist_raw, list) else []
    last_by_title_raw = st.session_state.get("ui_toast_last_by_title", {})
    last_by_title = dict(last_by_title_raw) if isinstance(last_by_title_raw, dict) else {}
    muted_raw = st.session_state.get("notice_muted_until", {})
    muted_map = muted_raw if isinstance(muted_raw, dict) else {}
    new_count = 0
    for _, row in notices_df.head(20).iterrows():
        key_source = str(row.get("alert_key", "") or "").strip()
        if key_source:
            key = key_source
        else:
            key = hashlib.sha1(
                f"{row.get('ts')}|{row.get('severity')}|{row.get('title')}|{row.get('detail')}|{row.get('source')}".encode("utf-8")
            ).hexdigest()[:16]
        mute_until = muted_map.get(key)
        if mute_until:
            mts = pd.to_datetime(mute_until, errors="coerce")
            if not pd.isna(mts):
                if mts.tzinfo is None:
                    mts = mts.tz_localize(NY_TZ)
                if pd.Timestamp.now(tz=NY_TZ) < mts:
                    continue
        if key in seen_set:
            continue
        seen.append(key)
        seen_set.add(key)
        sev = str(row.get("severity", "info")).lower()
        title = str(row.get("title", "Alert"))
        detail = str(row.get("detail", "") or "")
        item = {
            "ts_ny": pd.Timestamp.now(tz=NY_TZ).isoformat(),
            "key": key,
            "severity": sev,
            "title": title,
            "detail": detail[:300],
        }
        hist.insert(0, item)
        new_count += 1
        if hasattr(st, "toast") and sev in {"high", "medium"}:
            is_profit_lock = title.lower().startswith("profit-lock execution")
            if is_profit_lock:
                # Deduplicate noisy profit-lock warnings across symbol/title variations.
                dedupe_key = "profit-lock execution"
                last_ts_raw = last_by_title.get(dedupe_key)
                last_ts = pd.to_datetime(last_ts_raw, errors="coerce") if last_ts_raw else pd.NaT
                now_ts = pd.Timestamp.now(tz=NY_TZ)
                if pd.isna(last_ts) or ((now_ts - last_ts).total_seconds() >= 1800):
                    icon = "⚠️"
                    st.toast(f"{title}", icon=icon)
                    last_by_title[dedupe_key] = now_ts.isoformat()
            else:
                icon = "🚨" if sev == "high" else "⚠️"
                st.toast(f"{title}", icon=icon)
    st.session_state["ui_toast_history"] = hist[:400]
    st.session_state["ui_toast_seen_keys"] = seen[-1200:]
    st.session_state["ui_toast_last_by_title"] = last_by_title
    if new_count > 0:
        st.session_state["ui_palette_status"] = f"{new_count} new alert toast(s)."


def _render_toast_center() -> None:
    hist_raw = st.session_state.get("ui_toast_history", [])
    hist = list(hist_raw) if isinstance(hist_raw, list) else []
    st.markdown("<div class='toast-center'>", unsafe_allow_html=True)
    st.markdown("<div class='toast-center-title'>Realtime Alerts</div>", unsafe_allow_html=True)
    if not hist:
        st.caption("No recent alert toasts.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    sev_filter = st.selectbox(
        "Toast Severity",
        options=["all", "high", "medium", "info"],
        index=0,
        key="ui_toast_filter",
    )
    frame = pd.DataFrame(hist)
    if not frame.empty and sev_filter != "all":
        frame = frame[frame["severity"].astype(str).str.lower() == sev_filter].copy()
    preview = frame.head(6).copy() if not frame.empty else pd.DataFrame()
    if preview.empty:
        st.caption("No alerts for selected severity.")
    else:
        cols = ["ts_ny", "severity", "title", "detail"]
        for col in cols:
            if col not in preview.columns:
                preview[col] = None
        st.dataframe(preview[cols], use_container_width=True, hide_index=True, height=190)
    with st.expander("Toast History", expanded=False):
        if frame.empty:
            st.caption("No historical toasts available.")
        else:
            st.dataframe(
                _paged_view(frame, key="toast_hist", default_page_size=50),  # type: ignore[name-defined]
                use_container_width=True,
                hide_index=True,
            )
    st.markdown("</div>", unsafe_allow_html=True)


def _render_realtime_status_ribbon(events_df: pd.DataFrame, state_df: pd.DataFrame, current_user: str, user_role: str) -> None:
    hs = _health_snapshot(events_df, state_df)  # type: ignore[name-defined]
    fresh = hs.get("freshness_min")
    fresh_txt = "-" if fresh is None else f"{float(fresh):.1f}m"
    live_mode = "ON" if bool(st.session_state.get("ui_live_stream_enabled", False)) else "OFF"
    auto_mode = "ON" if bool(st.session_state.get("ui_auto_refresh_enabled", False)) else "OFF"
    profile = str(_state_value(state_df, "switch_last_profile", "-"))  # type: ignore[name-defined]
    now_ny = pd.Timestamp.now(tz=NY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    st.markdown(
        (
            "<div class='realtime-ribbon'>"
            f"<span>User: <b>{current_user}</b> ({user_role})</span>"
            f"<span>Freshness: <b>{fresh_txt}</b></span>"
            f"<span>Events 1h: <b>{int(hs.get('events_1h', 0))}</b></span>"
            f"<span>Cycles 24h: <b>{int(hs.get('cycles_24h', 0))}</b></span>"
            f"<span>Live: <b>{live_mode}</b></span>"
            f"<span>Auto: <b>{auto_mode}</b></span>"
            f"<span>Profile: <b>{profile}</b></span>"
            f"<span>Now: <b>{now_ny}</b></span>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_active_context_bar(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    hs = _health_snapshot(events_df, state_df)
    selected_symbol = str(st.session_state.get("ui_selected_symbol", "-") or "-")
    tw = str(st.session_state.get("ui_time_window", "1D") or "1D")
    view = str(st.session_state.get("ui_symbol_view_mode", "Intraday Activity") or "Intraday Activity")
    chart = str(st.session_state.get("ui_symbol_chart_type", "Line") or "Line")
    filters_on = bool(st.session_state.get("ui_global_filters_enabled", False))
    gq = str(st.session_state.get("ui_global_filter_query", "") or "").strip()
    gq_txt = gq if gq else "-"
    fresh = hs.get("freshness_min")
    if fresh is None:
        conf = "LOW"
        conf_color = "#ff8ba1"
    elif float(fresh) <= 10 and int(hs.get("error_events_24h", 0)) == 0:
        conf = "HIGH"
        conf_color = "#7fffb0"
    elif float(fresh) <= 30:
        conf = "MEDIUM"
        conf_color = "#f3df90"
    else:
        conf = "LOW"
        conf_color = "#ff8ba1"
    st.markdown(
        (
            "<div class='status-bar'>"
            f"<span class='mode-chip'>Symbol <b>{selected_symbol}</b></span>"
            f"<span class='mode-chip'>Window <b>{tw}</b></span>"
            f"<span class='mode-chip'>View <b>{view}</b></span>"
            f"<span class='mode-chip'>Chart <b>{chart}</b></span>"
            f"<span class='mode-chip'>Global Filters <b>{'ON' if filters_on else 'OFF'}</b></span>"
            f"<span class='mode-chip'>Query <b>{gq_txt}</b></span>"
            f"<span class='mode-chip'>Freshness Confidence <b style='color:{conf_color}'>{conf}</b></span>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_activity_timeline_strip(events_df: pd.DataFrame) -> None:
    if events_df.empty:
        return
    frame = events_df.copy()
    col = _events_time_col(frame)
    if not col or col not in frame.columns:
        return
    frame[col] = pd.to_datetime(frame[col], errors="coerce")
    frame = frame.dropna(subset=[col]).sort_values(col, ascending=False).head(20)
    if frame.empty:
        return
    parts: list[str] = []
    for _, row in frame.iterrows():
        ts = pd.to_datetime(row.get(col), errors="coerce")
        ts_txt = "-" if pd.isna(ts) else ts.strftime("%H:%M")
        evt = str(row.get("event_type", "") or "-")
        sym = str(row.get("symbol", "") or "-")
        parts.append(f"{ts_txt} <b>{sym}</b> {evt}")
    tape = "  |  ".join(parts)
    st.markdown(f"<div class='event-tape'>{tape}</div>", unsafe_allow_html=True)


def _record_ui_command_event(action_name: str, query_text: str = "") -> None:
    hist_raw = st.session_state.get("ui_command_history", [])
    hist = list(hist_raw) if isinstance(hist_raw, list) else []
    hist.insert(
        0,
        {
            "ts_ny": pd.Timestamp.now(tz=NY_TZ).isoformat(),
            "action": str(action_name or "").strip(),
            "query": str(query_text or "").strip(),
        },
    )
    st.session_state["ui_command_history"] = hist[:300]


def _render_top_command_bar(events_df: pd.DataFrame, state_df: pd.DataFrame, user_role: str) -> None:
    st.markdown("<div class='sticky-global'><div class='top-command-bar'>", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns([1.65, 1.25, 1.1, 1.0])
    with c1:
        q = st.text_input(
            "Command Search",
            value=str(st.session_state.get("ui_top_cmd_search", "") or ""),
            key="ui_top_cmd_search",
            placeholder="search events/symbols and apply globally...",
            label_visibility="collapsed",
        )
    with c2:
        action = st.selectbox(
            "Quick Action",
            options=[
                "Refresh Runtime Data",
                "Open Command Center",
                "Open Tradeboard",
                "Open Multi-Chart",
                "Open Execution Journal",
                "Open Audit Trail",
                "Open Notifications",
                "Open UI Diagnostics",
                "Open Pop-out: Tradeboard Chart",
                "Open Pop-out: Execution Blotter",
                "Open Pop-out: Alerts",
                "Close Pop-out",
                "Open Global Command Palette",
                "Toggle Incident Mode",
                "Toggle Auto Refresh",
                "Set Global Window: 1D",
                "Set Global Window: 1W",
                "Set Global Window: 1M",
            ],
            key="ui_top_cmd_action",
            label_visibility="collapsed",
        )
    with c3:
        if st.button("Run Action", use_container_width=True, key="ui_top_cmd_run"):
            if q.strip():
                st.session_state["ui_global_filters_enabled"] = True
                st.session_state["ui_global_filter_query"] = q.strip()
            if action == "Refresh Runtime Data":
                _load_runtime_db.clear()  # type: ignore[name-defined]
            elif action == "Open Command Center":
                _queue_workspace_switch("Command Center")
            elif action == "Open Tradeboard":
                _queue_workspace_switch("Tradeboard")
            elif action == "Open Multi-Chart":
                _queue_workspace_switch("Multi-Chart")
            elif action == "Open Execution Journal":
                _queue_workspace_switch("Execution Journal")
            elif action == "Open Audit Trail":
                _queue_workspace_switch("Audit Trail")
            elif action == "Open Notifications":
                _queue_workspace_switch("Notifications")
            elif action == "Open UI Diagnostics":
                _queue_workspace_switch("UI Diagnostics")
            elif action == "Open Pop-out: Tradeboard Chart":
                st.session_state["ui_popout_panel"] = "tradeboard_chart"
            elif action == "Open Pop-out: Execution Blotter":
                st.session_state["ui_popout_panel"] = "execution_blotter"
            elif action == "Open Pop-out: Alerts":
                st.session_state["ui_popout_panel"] = "alerts"
            elif action == "Close Pop-out":
                st.session_state["ui_popout_panel"] = ""
            elif action == "Open Global Command Palette":
                st.session_state["ui_global_palette_open"] = True
            elif action == "Toggle Incident Mode":
                st.session_state["ui_incident_mode"] = not bool(st.session_state.get("ui_incident_mode", False))
            elif action == "Toggle Auto Refresh":
                st.session_state["ui_pending_auto_refresh_enabled"] = not bool(st.session_state.get("ui_auto_refresh_enabled", False))
            elif action.endswith("1D"):
                st.session_state["ui_global_filters_enabled"] = True
                st.session_state["ui_global_time_window"] = "1D"
            elif action.endswith("1W"):
                st.session_state["ui_global_filters_enabled"] = True
                st.session_state["ui_global_time_window"] = "1W"
            elif action.endswith("1M"):
                st.session_state["ui_global_filters_enabled"] = True
                st.session_state["ui_global_time_window"] = "1M"
            st.session_state["ui_palette_status"] = f"Top action executed: {action}"
            _record_ui_command_event(action, q.strip())
            _append_ui_audit("top_command_action", {"action": action, "query": q.strip()})
            st.rerun()
    with c4:
        prof = str(_state_value(state_df, "switch_last_profile", "-"))  # type: ignore[name-defined]
        st.caption("Profile")
        st.code(prof, language="text")

    q1, q2, q3, q4, q5, q6 = st.columns(6)
    with q1:
        if st.button("Tradeboard", use_container_width=True, key="ui_top_quick_tradeboard"):
            _queue_workspace_switch("Tradeboard")
            _record_ui_command_event("Quick: Tradeboard")
            st.session_state["ui_palette_status"] = "Quick action: Tradeboard."
            st.rerun()
    with q2:
        if st.button("Execution", use_container_width=True, key="ui_top_quick_execution"):
            _queue_workspace_switch("Execution Journal")
            _record_ui_command_event("Quick: Execution")
            st.session_state["ui_palette_status"] = "Quick action: Execution Journal."
            st.rerun()
    with q3:
        if st.button("Notifications", use_container_width=True, key="ui_top_quick_notifications"):
            _queue_workspace_switch("Notifications")
            _record_ui_command_event("Quick: Notifications")
            st.session_state["ui_palette_status"] = "Quick action: Notifications."
            st.rerun()
    with q4:
        if st.button("Audit", use_container_width=True, key="ui_top_quick_audit"):
            _queue_workspace_switch("Audit Trail")
            _record_ui_command_event("Quick: Audit")
            st.session_state["ui_palette_status"] = "Quick action: Audit Trail."
            st.rerun()
    with q5:
        if st.button("Refresh", use_container_width=True, key="ui_top_quick_refresh"):
            _load_runtime_db.clear()
            _record_ui_command_event("Quick: Refresh")
            st.session_state["ui_palette_status"] = "Quick action: runtime data refreshed."
            st.rerun()
    with q6:
        if st.button("Auto Refresh", use_container_width=True, key="ui_top_quick_autoref"):
            next_auto = not bool(st.session_state.get("ui_auto_refresh_enabled", False))
            st.session_state["ui_pending_auto_refresh_enabled"] = next_auto
            _record_ui_command_event("Quick: Toggle Auto Refresh")
            st.session_state["ui_palette_status"] = f"Quick action: auto refresh {'enabled' if next_auto else 'disabled'}."
            st.rerun()

    lr1, lr2, lr3 = st.columns([1.35, 1.25, 1.4])
    with lr1:
        fav_raw = st.session_state.get("ui_command_favorites", [])
        favs = [str(x) for x in fav_raw] if isinstance(fav_raw, list) else []
        if action not in favs and st.button("Add Action To Favorites", use_container_width=True, key="ui_top_cmd_fav_add"):
            favs.insert(0, str(action))
            st.session_state["ui_command_favorites"] = sorted(set(favs))
            st.session_state["ui_palette_status"] = f"Favorite saved: {action}"
            st.rerun()
        if favs:
            fav_pick = st.selectbox("Favorites", options=favs, index=0, key="ui_top_cmd_fav_pick")
            if st.button("Run Favorite", use_container_width=True, key="ui_top_cmd_fav_run"):
                st.session_state["ui_top_cmd_action"] = fav_pick
                _record_ui_command_event(fav_pick, q.strip())
                st.session_state["ui_palette_status"] = f"Favorite queued: {fav_pick}"
                st.rerun()
    with lr2:
        recent_raw = st.session_state.get("ui_command_history", [])
        recent = list(recent_raw) if isinstance(recent_raw, list) else []
        if recent:
            recent_opts = [f"{str(r.get('action',''))} | {str(r.get('ts_ny',''))[:19]}" for r in recent[:20] if isinstance(r, dict)]
            if recent_opts:
                st.selectbox("Recent Commands", options=recent_opts, index=0, key="ui_top_cmd_recent")
        if st.button("Clear History", use_container_width=True, key="ui_top_cmd_clear_history"):
            st.session_state["ui_command_history"] = []
            st.session_state["ui_palette_status"] = "Command history cleared."
            st.rerun()
    with lr3:
        match_rows: list[str] = []
        needle = str(q or "").strip().lower()
        if needle and (not events_df.empty):
            for _, row in events_df.head(1200).iterrows():
                txt = " | ".join(
                    [
                        str(row.get("symbol", "")),
                        str(row.get("event_type", "")),
                        str(row.get("variant", "")),
                        str(row.get("order_type", "")),
                    ]
                ).strip()
                if needle in txt.lower():
                    match_rows.append(txt)
                if len(match_rows) >= 40:
                    break
        if match_rows:
            pick = st.selectbox("Search Jump", options=match_rows, index=0, key="ui_top_cmd_search_jump")
            if st.button("Open Match In Tradeboard", use_container_width=True, key="ui_top_cmd_search_open"):
                parts = [p.strip() for p in str(pick).split("|")]
                symbol = str(parts[0] if parts else "").upper()
                if symbol:
                    st.session_state["ui_pending_selected_symbol"] = symbol
                    _queue_workspace_switch("Tradeboard")
                st.session_state["ui_global_filters_enabled"] = True
                st.session_state["ui_global_filter_query"] = needle
                _record_ui_command_event("Search Jump", needle)
                st.session_state["ui_palette_status"] = f"Opened match in Tradeboard: {symbol or '-'}"
                st.rerun()
        else:
            st.caption("Search Jump: no results in current scope.")

    with st.expander("Keyboard Shortcuts", expanded=False):
        st.markdown("`Ctrl/Cmd+K` command palette context")
        st.markdown("`Alt+R` runtime data refresh")
        st.markdown("`Alt+1..6` workspace quick jump")
        st.markdown("`Alt+H/T/E/N/A` quick workspace hops (home/tradeboard/execution/notifications/audit)")
    st.markdown("</div></div>", unsafe_allow_html=True)


def _render_workspace_quick_nav(allowed_tabs: list[str]) -> None:
    if not allowed_tabs:
        return
    st.markdown("<div class='desk-block quick-nav-block'>", unsafe_allow_html=True)
    st.markdown("<div class='section-caption'>Quick Jump</div>", unsafe_allow_html=True)
    top = allowed_tabs[:10]
    cols = st.columns(len(top))
    for i, name in enumerate(top):
        with cols[i]:
            if st.button(name, use_container_width=True, key=f"ui_quick_nav_{name.replace(' ', '_').lower()}"):
                _queue_workspace_switch(name)
                st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def _render_keyboard_cheatsheet() -> None:
    with st.expander("Keyboard & Command Cheatsheet", expanded=False):
        st.markdown("`Ctrl/Cmd + K` open command palette context")
        st.markdown("`Alt + R` refresh runtime data")
        st.markdown("`Alt + 1..6` workspace quick jump")
        st.markdown("`Alt + H/T/E/N/A` workspace hop (home/tradeboard/execution/notifications/audit)")
        st.markdown("Use top command bar `Search Jump` for symbol/event drill-in.")
        st.caption("If shortcuts are not working, enable `Keyboard Shortcuts` in Preferences.")


def _run_global_palette_action(action: str, query: str, user_role: str) -> None:
    if str(query or "").strip():
        st.session_state["ui_global_filters_enabled"] = True
        st.session_state["ui_global_filter_query"] = str(query).strip()
    if action == "Refresh Runtime Data":
        _load_runtime_db.clear()
    elif action == "Open Command Center":
        _queue_workspace_switch("Command Center")
    elif action == "Open Tradeboard":
        _queue_workspace_switch("Tradeboard")
    elif action == "Open Multi-Chart":
        _queue_workspace_switch("Multi-Chart")
    elif action == "Open Execution Journal":
        _queue_workspace_switch("Execution Journal")
    elif action == "Open Notifications":
        _queue_workspace_switch("Notifications")
    elif action == "Open Audit Trail":
        _queue_workspace_switch("Audit Trail")
    elif action == "Open UI Diagnostics":
        _queue_workspace_switch("UI Diagnostics")
    elif action == "Toggle Auto Refresh":
        st.session_state["ui_pending_auto_refresh_enabled"] = not bool(st.session_state.get("ui_auto_refresh_enabled", False))
    elif action == "Toggle Incident Mode":
        st.session_state["ui_incident_mode"] = not bool(st.session_state.get("ui_incident_mode", False))
    elif action == "Apply Preset: Open":
        _apply_workspace_preset("Open")
    elif action == "Apply Preset: Close":
        _apply_workspace_preset("Close")
    elif action == "Apply Preset: Forensics":
        _apply_workspace_preset("Forensics")
    elif action == "Apply Preset: Risk":
        _apply_workspace_preset("Risk")
    elif action == "Apply Preset: Execution":
        _apply_workspace_preset("Execution")
    elif action == "Apply Preset: Monitor":
        _apply_workspace_preset("Monitor")
    elif action == "Pop-out: Tradeboard Chart":
        st.session_state["ui_popout_panel"] = "tradeboard_chart"
    elif action == "Pop-out: Execution Blotter":
        st.session_state["ui_popout_panel"] = "execution_blotter"
    elif action == "Pop-out: Alerts":
        st.session_state["ui_popout_panel"] = "alerts"
    elif action == "Close Pop-out":
        st.session_state["ui_popout_panel"] = ""
    elif action == "Jump Highest Activity Symbol":
        watch = _watchlist_df(events_df=st.session_state.get("ui_cached_events_for_palette", pd.DataFrame()), state_df=st.session_state.get("ui_cached_state_for_palette", pd.DataFrame()))
        if not watch.empty:
            top = str(watch.iloc[0]["symbol"]).strip().upper()
            if top:
                st.session_state["ui_pending_selected_symbol"] = top
                _queue_workspace_switch("Tradeboard")
    if action.startswith("Open ") and (action.replace("Open ", "") not in _workspace_nav_options(user_role)):
        st.session_state["ui_palette_status"] = f"Action blocked by role: {action}"
    else:
        st.session_state["ui_palette_status"] = f"Palette action executed: {action}"
        _record_ui_command_event(action, str(query or ""))
        _append_ui_audit("global_palette_run", {"action": action, "query": str(query or "")[:120]})
    st.rerun()


def _render_global_command_palette_modal(events_df: pd.DataFrame, state_df: pd.DataFrame, user_role: str) -> None:
    open_now = bool(st.session_state.get("ui_global_palette_open", False))
    toggled = st.button("Command Palette", use_container_width=False, key="ui_global_palette_toggle")
    if toggled:
        st.session_state["ui_global_palette_open"] = not open_now
        open_now = bool(st.session_state["ui_global_palette_open"])
        st.rerun()
    if not open_now:
        return
    st.session_state["ui_cached_events_for_palette"] = events_df
    st.session_state["ui_cached_state_for_palette"] = state_df
    st.markdown("<div class='desk-block command-palette-modal'>", unsafe_allow_html=True)
    st.markdown("<div class='section-caption'>Global Command Palette</div>", unsafe_allow_html=True)
    actions = [
        "Refresh Runtime Data",
        "Open Command Center",
        "Open Tradeboard",
        "Open Multi-Chart",
        "Open Execution Journal",
        "Open Notifications",
        "Open Audit Trail",
        "Open UI Diagnostics",
        "Toggle Auto Refresh",
        "Toggle Incident Mode",
        "Apply Preset: Open",
        "Apply Preset: Close",
        "Apply Preset: Forensics",
        "Apply Preset: Risk",
        "Apply Preset: Execution",
        "Apply Preset: Monitor",
        "Pop-out: Tradeboard Chart",
        "Pop-out: Execution Blotter",
        "Pop-out: Alerts",
        "Close Pop-out",
        "Jump Highest Activity Symbol",
    ]
    query = st.text_input(
        "Find Action",
        value=str(st.session_state.get("ui_global_palette_query", "") or ""),
        key="ui_global_palette_query",
        placeholder="Type action, symbol, or command...",
    )
    needle = str(query or "").strip().lower()
    filtered = [a for a in actions if needle in a.lower()] if needle else actions
    if needle and not filtered:
        filtered = difflib.get_close_matches(needle, actions, n=12, cutoff=0.2) or actions
    choice = st.selectbox("Action", options=filtered, index=0, key="ui_global_palette_action")
    c1, c2 = st.columns([1.0, 1.0])
    with c1:
        if st.button("Run Action", use_container_width=True, key="ui_global_palette_run"):
            _run_global_palette_action(choice, query, user_role=user_role)
    with c2:
        if st.button("Close Palette", use_container_width=True, key="ui_global_palette_close"):
            st.session_state["ui_global_palette_open"] = False
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def _market_session_snapshot(eval_time_text: str) -> dict[str, Any]:
    now = pd.Timestamp.now(tz=NY_TZ)
    today = now.normalize()
    open_ts = today + pd.Timedelta(hours=9, minutes=30)
    close_ts = today + pd.Timedelta(hours=16)
    ext_close_ts = today + pd.Timedelta(hours=20)
    eval_txt = _normalize_eval_time(eval_time_text, default="15:55")
    hh, mm = [int(x) for x in eval_txt.split(":")]
    eval_today = today + pd.Timedelta(hours=hh, minutes=mm)

    weekday = int(now.weekday())
    if weekday >= 5:
        session = "Weekend Closed"
    elif now < open_ts:
        session = "Pre-Market"
    elif now <= close_ts:
        session = "Regular"
    elif now <= ext_close_ts:
        session = "After-Hours"
    else:
        session = "Post-Market"

    next_eval = eval_today
    if (weekday >= 5) or (now > eval_today):
        next_eval = eval_today + pd.Timedelta(days=1)
        while int(next_eval.weekday()) >= 5:
            next_eval += pd.Timedelta(days=1)
    mins_to_eval = max(0.0, float((next_eval - now).total_seconds() / 60.0))
    return {
        "session": session,
        "now_ny": now,
        "next_eval": next_eval,
        "eval_time": eval_txt,
        "mins_to_eval": mins_to_eval,
    }


def _render_change_since_refresh_strip(events_df: pd.DataFrame, notices_df: pd.DataFrame) -> None:
    now = pd.Timestamp.now(tz=NY_TZ)
    fresh_ts = None
    if (not events_df.empty) and ("ts_ny" in events_df.columns):
        ts = pd.to_datetime(events_df["ts_ny"], errors="coerce").dropna()
        if not ts.empty:
            fresh_ts = ts.max()
    evt_counts: dict[str, int] = {}
    if (not events_df.empty) and ("event_type" in events_df.columns):
        vc = events_df["event_type"].astype(str).value_counts().head(12)
        evt_counts = {str(k): int(v) for k, v in vc.items()}
    snap = {
        "events": int(len(events_df)),
        "alerts": int(len(notices_df)),
        "cycles": int((events_df["event_type"] == "switch_cycle_complete").sum()) if ("event_type" in events_df.columns and not events_df.empty) else 0,
        "latest_ts": str(fresh_ts) if fresh_ts is not None else "-",
        "captured_at": str(now),
        "event_type_counts": evt_counts,
    }
    prev = st.session_state.get("ui_last_refresh_snapshot", {})
    if not isinstance(prev, dict) or not prev:
        prev = {
            "events": snap["events"],
            "alerts": snap["alerts"],
            "cycles": snap["cycles"],
            "latest_ts": snap["latest_ts"],
        }
    d_events = int(snap["events"]) - int(pd.to_numeric(prev.get("events", 0), errors="coerce") or 0)
    d_alerts = int(snap["alerts"]) - int(pd.to_numeric(prev.get("alerts", 0), errors="coerce") or 0)
    d_cycles = int(snap["cycles"]) - int(pd.to_numeric(prev.get("cycles", 0), errors="coerce") or 0)
    market = _market_session_snapshot(str(st.session_state.get("ui_eval_time", "15:55")))
    st.markdown(
        (
            "<div class='desk-block'>"
            "<div class='section-caption'>Changed Since Last Refresh</div>"
            f"<span class='stat-chip'>Events Δ {d_events:+d}</span> "
            f"<span class='stat-chip'>Alerts Δ {d_alerts:+d}</span> "
            f"<span class='stat-chip'>Cycles Δ {d_cycles:+d}</span> "
            f"<span class='stat-chip'>Session {market['session']}</span> "
            f"<span class='stat-chip'>Eval {market['eval_time']} ET in {market['mins_to_eval']:.1f}m</span> "
            f"<span class='stat-chip'>Latest {snap['latest_ts']}</span>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    with st.expander("Detailed Delta", expanded=False):
        prev_counts_raw = prev.get("event_type_counts", {}) if isinstance(prev, dict) else {}
        prev_counts = prev_counts_raw if isinstance(prev_counts_raw, dict) else {}
        keys = sorted(set(prev_counts.keys()) | set(evt_counts.keys()))
        rows: list[dict[str, Any]] = []
        for k in keys:
            old = int(pd.to_numeric(prev_counts.get(k, 0), errors="coerce") or 0)
            new = int(pd.to_numeric(evt_counts.get(k, 0), errors="coerce") or 0)
            delta = new - old
            if delta != 0:
                rows.append({"event_type": k, "prev": old, "now": new, "delta": delta})
        if rows:
            dd = pd.DataFrame(rows).sort_values("delta", ascending=False)
            st.dataframe(dd, use_container_width=True, hide_index=True)
        else:
            st.caption("No event-type count changes detected since previous refresh.")
    st.session_state["ui_last_refresh_snapshot"] = snap


def _apply_widget_cadence(df: pd.DataFrame, *, key: str, cadence_sec: int) -> pd.DataFrame:
    sec = max(1, int(cadence_sec))
    now = pd.Timestamp.now(tz=NY_TZ)
    cache_key = f"ui_widget_cadence_cache_{key}"
    rec = st.session_state.get(cache_key, {})
    if not isinstance(rec, dict):
        rec = {}
    last_ts = pd.to_datetime(rec.get("ts"), errors="coerce")
    cached_df = rec.get("df")
    if isinstance(cached_df, pd.DataFrame) and (last_ts is not None) and (not pd.isna(last_ts)):
        age = (now - last_ts).total_seconds()
        st.session_state[f"ui_{key}_widget_last_age_min"] = max(0.0, age / 60.0)
        if age < sec:
            return cached_df
    st.session_state[f"ui_{key}_widget_last_age_min"] = 0.0
    st.session_state[cache_key] = {"ts": now.isoformat(), "df": df}
    return df


def _render_onboarding_guide() -> None:
    if not bool(st.session_state.get("ui_show_onboarding", True)):
        return
    with st.expander("Onboarding Tour", expanded=False):
        st.markdown("1. **Check Runtime Health**: Ensure banner shows healthy freshness and DB status.")
        st.markdown("2. **Use Workspace Presets**: Open/Close/Forensics/Risk for quick context switches.")
        st.markdown("3. **Inspect Tradeboard**: Validate symbol focus, event markers, and position lens.")
        st.markdown("4. **Execution Journal**: Verify lifecycle, slippage, and drift guardrails.")
        st.markdown("5. **Audit Trail**: Replay day timeline, bookmark key events, and export CSV.")
        st.markdown("6. **Notifications**: Acknowledge/snooze/escalate and monitor incident timeline.")
        st.checkbox("Hide onboarding next time", value=False, key="ui_hide_onboarding_once")
        if st.button("Apply Onboarding Preference", use_container_width=True, key="ui_apply_onboarding_pref"):
            st.session_state["ui_show_onboarding"] = not bool(st.session_state.get("ui_hide_onboarding_once", False))
            st.rerun()


def _render_mobile_bottom_nav(allowed_tabs: list[str]) -> None:
    if not bool(st.session_state.get("ui_mobile_bottom_nav", False)):
        return
    if not allowed_tabs:
        return
    st.markdown("<div class='mobile-bottom-nav'>", unsafe_allow_html=True)
    nav_default = str(st.session_state.get("ui_workspace_nav_selection", allowed_tabs[0]) or allowed_tabs[0])
    if nav_default not in allowed_tabs:
        nav_default = allowed_tabs[0]
    picked = st.radio(
        "Bottom Nav",
        options=allowed_tabs,
        index=allowed_tabs.index(nav_default),
        horizontal=True,
        key="ui_mobile_bottom_nav_pick",
        label_visibility="collapsed",
    )
    if str(picked) != str(st.session_state.get("ui_workspace_nav_selection", "")):
        _queue_workspace_switch(str(picked))
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


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


def _list_user_sessions(cfg: AuthConfig, username: str) -> pd.DataFrame:
    user = str(username or "").strip()
    if not user:
        return pd.DataFrame()
    _init_users_db(cfg)
    now = pd.Timestamp.now(tz="UTC")
    conn = sqlite3.connect(str(Path(cfg.users_db_path)))
    try:
        df = pd.read_sql_query(
            """
            SELECT token_hash, username, auth_method, created_at, last_seen_at, expires_at, is_active
            FROM ui_sessions
            WHERE username = ?
            ORDER BY last_seen_at DESC
            """,
            conn,
            params=(user,),
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    if df.empty:
        return df
    for col in ["created_at", "last_seen_at", "expires_at"]:
        df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
        df[f"{col}_ny"] = df[col].dt.tz_convert(NY_TZ)
    df["is_active"] = pd.to_numeric(df["is_active"], errors="coerce").fillna(0).astype(int)
    df["expired"] = df["expires_at"] < now
    df["status"] = df.apply(
        lambda r: "expired"
        if bool(r.get("expired"))
        else ("active" if int(r.get("is_active", 0)) == 1 else "revoked"),
        axis=1,
    )
    df["token_tail"] = df["token_hash"].astype(str).map(lambda s: s[-8:] if len(s) >= 8 else s)
    return df.reset_index(drop=True)


def _revoke_user_session(cfg: AuthConfig, username: str, token_hash: str) -> tuple[bool, str]:
    user = str(username or "").strip()
    token = str(token_hash or "").strip()
    if not user or not token:
        return False, "Missing username or token."
    _init_users_db(cfg)
    conn = sqlite3.connect(str(Path(cfg.users_db_path)))
    try:
        cur = conn.execute(
            "UPDATE ui_sessions SET is_active = 0 WHERE username = ? AND token_hash = ?",
            (user, token),
        )
        conn.commit()
        if int(cur.rowcount or 0) <= 0:
            return False, "No matching active session found."
        return True, "Session revoked."
    except Exception as exc:
        return False, f"Failed to revoke session: {exc}"
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


def _render_session_sidebar_tools(cfg: AuthConfig, current_user: str) -> None:
    stats = _session_idle_stats(cfg)
    idle_min = float(stats["idle_sec"]) / 60.0
    rem_min = float(stats["remaining_sec"]) / 60.0
    ttl_min = max(1.0, float(stats["ttl_sec"]) / 60.0)
    ratio = max(0.0, min(1.0, rem_min / ttl_min))
    st.progress(ratio, text=f"Session remaining: {rem_min:.1f}m / {ttl_min:.0f}m")
    st.caption(f"Idle: {idle_min:.1f}m")
    if rem_min <= 5:
        st.warning("Session will expire soon. Any action refreshes activity timer.")
    with st.expander("Active Sessions", expanded=False):
        sessions = _list_user_sessions(cfg, current_user)
        if sessions.empty:
            st.caption("No active sessions for this account.")
            return
        current_token = str(st.session_state.get("auth_session_token", "") or "").strip()
        current_hash = _hash_session_token(current_token) if current_token else ""
        sessions["current"] = sessions["token_hash"].astype(str).map(lambda h: "yes" if h == current_hash else "no")
        preview_cols = ["current", "status", "auth_method", "created_at_ny", "last_seen_at_ny", "expires_at_ny", "token_tail"]
        for col in preview_cols:
            if col not in sessions.columns:
                sessions[col] = None
        st.dataframe(sessions[preview_cols], use_container_width=True, hide_index=True, height=220)
        revokable = sessions[(sessions["current"] == "no") & (sessions["status"] == "active")].copy()
        if revokable.empty:
            st.caption("No revokable active sessions besides this one.")
            return
        opts = [
            f"{str(r.get('token_tail'))} | last_seen={pd.to_datetime(r.get('last_seen_at_ny'), errors='coerce')} | {str(r.get('auth_method'))}"
            for _, r in revokable.iterrows()
        ]
        pick = st.selectbox("Session To Revoke", options=opts, index=0, key="session_revoke_pick")
        b1, b2 = st.columns(2)
        with b1:
            if st.button("Revoke Selected", use_container_width=True, key="session_revoke_selected"):
                idx = max(0, opts.index(pick))
                token_hash = str(revokable.iloc[idx]["token_hash"])
                ok, msg = _revoke_user_session(cfg, current_user, token_hash=token_hash)
                _append_ui_audit("session_revoke_selected", {"ok": ok, "token_tail": str(token_hash)[-8:]})
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()
        with b2:
            if st.button("Revoke All Others", use_container_width=True, key="session_revoke_all_others"):
                ok_count = 0
                for _, row in revokable.iterrows():
                    ok, _ = _revoke_user_session(cfg, current_user, token_hash=str(row.get("token_hash")))
                    if ok:
                        ok_count += 1
                _append_ui_audit("session_revoke_all_others", {"count": ok_count})
                st.success(f"Revoked {ok_count} session(s).")
                if ok_count > 0:
                    st.rerun()


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
    st.session_state.setdefault("ui_watchlist_min_events", 0)
    st.session_state.setdefault("ui_watchlist_sort_mode", "Activity")
    st.session_state.setdefault("ui_watchlist_card_density", "Standard")
    st.session_state.setdefault("ui_watchlist_quick_actions", True)
    st.session_state.setdefault("ui_time_window", "1D")
    st.session_state.setdefault("ui_eval_time", "15:55")
    st.session_state.setdefault("ui_sync_charts", True)
    st.session_state.setdefault("ui_symbol_view_mode", "Intraday Activity")
    st.session_state.setdefault("ui_symbol_chart_type", "Line")
    st.session_state.setdefault("ui_linked_cursor_enabled", True)
    st.session_state.setdefault("ui_linked_cursor_ts", "")
    st.session_state.setdefault("ui_command_palette_action", "Refresh Data Now")
    st.session_state.setdefault("ui_command_palette_search", "")
    st.session_state.setdefault("ui_global_palette_open", False)
    st.session_state.setdefault("ui_global_palette_query", "")
    st.session_state.setdefault("ui_global_palette_action", "Refresh Runtime Data")
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
    st.session_state.setdefault("ui_global_filter_presets", {})
    st.session_state.setdefault("ui_global_preset_pending_apply", {})
    st.session_state.setdefault("ui_pending_saved_workspace", "")
    st.session_state.setdefault("ui_pending_workspace_nav_selection", "")
    st.session_state.setdefault("ui_saved_workspace", "Tradeboard")
    st.session_state.setdefault("ui_workspace_view_mode", "Single Workspace")
    st.session_state.setdefault("ui_workspace_nav_selection", "Tradeboard")
    st.session_state.setdefault("ui_custom_alert_stale_enabled", False)
    st.session_state.setdefault("ui_custom_alert_stale_min", 10)
    st.session_state.setdefault("ui_custom_alert_min_cycles_enabled", False)
    st.session_state.setdefault("ui_custom_alert_min_cycles_24h", 1)
    st.session_state.setdefault("ui_custom_alert_error_enabled", False)
    st.session_state.setdefault("ui_custom_alert_error_max_24h", 0)
    st.session_state.setdefault("audit_replay_playing", False)
    st.session_state.setdefault("audit_replay_speed_sec", 2)
    st.session_state.setdefault("audit_replay_pending_speed_sec", None)
    st.session_state.setdefault("audit_replay_pending_idx", None)
    st.session_state.setdefault("audit_replay_pending_minute_idx", None)
    st.session_state.setdefault("ui_theme_preset", "Neo Green")
    st.session_state.setdefault("ui_density_mode", "Comfortable")
    st.session_state.setdefault("ui_font_scale_pct", 100)
    st.session_state.setdefault("ui_high_contrast", False)
    st.session_state.setdefault("ui_focus_mode", False)
    st.session_state.setdefault("ui_keyboard_shortcuts_enabled", True)
    st.session_state.setdefault("ui_tradeboard_split", "Balanced")
    st.session_state.setdefault("ui_execution_split", "Balanced")
    st.session_state.setdefault("ui_tradeboard_custom_weights", [0.8, 1.95, 0.75])
    st.session_state.setdefault("ui_execution_custom_weights", [1.15, 1.0])
    st.session_state.setdefault("auth_forgot_requested", "")
    st.session_state.setdefault("ui_incident_mode", False)
    st.session_state.setdefault("ui_mobile_bottom_nav", False)
    st.session_state.setdefault("ui_workspace_preset", "Custom")
    st.session_state.setdefault("ui_workspace_visible_tabs", [])
    st.session_state.setdefault("ui_command_history", [])
    st.session_state.setdefault("ui_command_favorites", [])
    st.session_state.setdefault("ui_layout_editor_enabled", False)
    st.session_state.setdefault("ui_panel_layout_locked", False)
    st.session_state.setdefault("ui_popout_panel", "")
    st.session_state.setdefault("ui_accessibility_preset", "Default")
    st.session_state.setdefault("ui_reduced_motion", False)
    st.session_state.setdefault("ui_perf_mode_enabled", False)
    st.session_state.setdefault("ui_perf_max_rows", 3000)
    st.session_state.setdefault("ui_persona_mode", "Operator")
    st.session_state.setdefault("ui_home_tiles", [])
    st.session_state.setdefault("ui_show_onboarding", True)
    st.session_state.setdefault("ui_minimal_mode", False)
    st.session_state.setdefault("ui_fast_widget_cadence_sec", 5)
    st.session_state.setdefault("ui_heavy_widget_cadence_sec", 30)
    st.session_state.setdefault("ui_saved_views", {})
    st.session_state.setdefault("ui_last_refresh_snapshot", {})
    st.session_state.setdefault("ui_render_timings", [])
    st.session_state.setdefault("ui_toast_history", [])
    st.session_state.setdefault("ui_toast_seen_keys", [])
    st.session_state.setdefault("ui_toast_last_by_title", {})
    st.session_state.setdefault("audit_replay_bookmarks", {})
    st.session_state.setdefault("notice_muted_until", {})


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

    pending_preset = st.session_state.get("ui_global_preset_pending_apply", {})
    if isinstance(pending_preset, dict) and pending_preset:
        key_map = {
            "window": "ui_global_time_window",
            "query": "ui_global_filter_query",
            "symbols": "ui_global_symbols",
            "event_types": "ui_global_event_types",
            "variants": "ui_global_variants",
            "sides": "ui_global_sides",
            "order_types": "ui_global_order_types",
            "enabled": "ui_global_filters_enabled",
        }
        for src, dst in key_map.items():
            if src in pending_preset:
                st.session_state[dst] = pending_preset.get(src)
        st.session_state["ui_global_preset_pending_apply"] = {}

    pending_workspace = str(st.session_state.get("ui_pending_saved_workspace", "") or "").strip()
    if pending_workspace:
        st.session_state["ui_saved_workspace"] = pending_workspace
        st.session_state["ui_pending_saved_workspace"] = ""

    pending_nav_workspace = str(st.session_state.get("ui_pending_workspace_nav_selection", "") or "").strip()
    if pending_nav_workspace:
        st.session_state["ui_workspace_nav_selection"] = pending_nav_workspace
        st.session_state["ui_pending_workspace_nav_selection"] = ""


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


def _session_idle_stats(cfg: AuthConfig) -> dict[str, float]:
    now_ts = float(datetime.now(tz=timezone.utc).timestamp())
    last_ts = float(st.session_state.get("session_last_activity_ts", 0.0) or 0.0)
    if last_ts <= 0:
        last_ts = now_ts
        st.session_state["session_last_activity_ts"] = now_ts
    idle_sec = max(0.0, now_ts - last_ts)
    ttl_sec = float(max(60, int(cfg.session_timeout_min) * 60))
    remaining_sec = max(0.0, ttl_sec - idle_sec)
    return {"idle_sec": idle_sec, "remaining_sec": remaining_sec, "ttl_sec": ttl_sec}


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
                with st.expander("Forgot Password", expanded=False):
                    st.caption("Password reset requests are handled by admin/operator.")
                    reset_user = st.text_input(
                        "Username for Reset",
                        value=str(st.session_state.get("auth_forgot_requested", "") or ""),
                        key="auth_forgot_username",
                    )
                    if st.button("Request Password Reset", use_container_width=True, key="auth_forgot_submit"):
                        request_user = str(reset_user or "").strip()
                        if not request_user:
                            _set_auth_notice("error", "Enter your username to request password reset.")
                        else:
                            st.session_state["auth_forgot_requested"] = request_user
                            _append_ui_audit("password_reset_requested", {"username": request_user})
                            _set_auth_notice(
                                "info",
                                "Password reset request logged. Contact your admin to complete reset.",
                            )
                        st.rerun()
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
    snap_row = {
        "ts_ny": pd.Timestamp.now(tz=NY_TZ).isoformat(),
        "total_events": int(total_events),
        "events_24h": int(events_24h),
        "orders_submitted": int(orders_submitted),
        "threshold_pct": float(pd.to_numeric(threshold_pct, errors="coerce") or 0.0) if threshold_pct is not None else None,
    }
    hist_raw = st.session_state.get("ui_kpi_history", [])
    hist = list(hist_raw) if isinstance(hist_raw, list) else []
    hist.append(snap_row)
    hist = hist[-360:]
    st.session_state["ui_kpi_history"] = hist
    hist_df = pd.DataFrame(hist)
    ev_sp = _sparkline(pd.to_numeric(hist_df.get("total_events", pd.Series(dtype=float)), errors="coerce").dropna().astype(float).tolist()) if not hist_df.empty else "-"
    e24_sp = _sparkline(pd.to_numeric(hist_df.get("events_24h", pd.Series(dtype=float)), errors="coerce").dropna().astype(float).tolist()) if not hist_df.empty else "-"
    ord_sp = _sparkline(pd.to_numeric(hist_df.get("orders_submitted", pd.Series(dtype=float)), errors="coerce").dropna().astype(float).tolist()) if not hist_df.empty else "-"
    thr_sp = _sparkline(pd.to_numeric(hist_df.get("threshold_pct", pd.Series(dtype=float)), errors="coerce").dropna().astype(float).tolist()) if not hist_df.empty else "-"

    kpi_data = [
        ("Events Loaded", f"{total_events:,}", f"Database rows in memory  {ev_sp}"),
        ("Events (24h)", f"{events_24h:,}", f"Recent runtime activity  {e24_sp}"),
        ("Current Variant", str(current_variant), "Active switch state"),
        ("Last Cycle Day", str(last_day), "Cycle completion marker"),
        ("Orders (Last Cycle)", f"{orders_submitted}", f"Submitted intents  {ord_sp}"),
        ("Threshold %", "-" if threshold_pct is None else f"{float(threshold_pct):.2f}", f"Adaptive profit-lock  {thr_sp}"),
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

    def _push(ts: Any, severity: str, title: str, detail: str, source: str, symbol: str = "") -> None:
        notices.append(
            {
                "ts": ts,
                "severity": severity,
                "title": title,
                "detail": detail,
                "source": source,
                "symbol": str(symbol or "").strip().upper(),
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
                symbol=str(row.get("symbol", "") or ""),
            )

        # Profit-lock events (collapsed to reduce duplicate-warning spam).
        pl = events_df[events_df["event_type"].isin(["switch_profit_lock_close", "switch_profit_lock_intraday_close"])].copy()
        if not pl.empty:
            pl["ts_ny"] = pd.to_datetime(pl["ts_ny"], errors="coerce")
            pl = pl.dropna(subset=["ts_ny"]).copy()
            pl["symbol_norm"] = pl["symbol"].astype(str).str.upper().fillna("-")
            pl["side_norm"] = pl["side"].astype(str).str.lower().fillna("-")
            pl["qty_num"] = pd.to_numeric(pl["qty"], errors="coerce").fillna(0.0)
            pl["day_ny"] = pl["ts_ny"].dt.strftime("%Y-%m-%d")
            grp = (
                pl.groupby(["day_ny", "symbol_norm", "side_norm", "event_type"], as_index=False)
                .agg(
                    ts_latest=("ts_ny", "max"),
                    qty_total=("qty_num", "sum"),
                    hits=("id", "count"),
                )
                .sort_values("ts_latest", ascending=False)
                .head(40)
            )
            for _, row in grp.iterrows():
                sym = str(row.get("symbol_norm", "-") or "-")
                side = str(row.get("side_norm", "-") or "-")
                qty_total = float(pd.to_numeric(row.get("qty_total"), errors="coerce") or 0.0)
                hits = int(pd.to_numeric(row.get("hits"), errors="coerce") or 0)
                day_ny = str(row.get("day_ny", "") or "")
                title = "Profit-lock execution" if hits <= 1 else f"Profit-lock execution x{hits}"
                detail = f"{sym} {side} qty_total={qty_total:.4f} day={day_ny}"
                _push(row.get("ts_latest"), "medium", title, detail, str(row.get("event_type")), symbol=sym)

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
                symbol=str(row.get("symbol", "") or ""),
            )

    out = pd.DataFrame(notices)
    if out.empty:
        return out
    out["ts"] = pd.to_datetime(out["ts"], errors="coerce")
    out["symbol"] = out.get("symbol", "").astype(str).str.upper()
    out = out.drop_duplicates(subset=["ts", "severity", "title", "detail", "source"], keep="first")
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


def _render_empty_state(title: str, detail: str, hint: str = "") -> None:
    extra = f"<div class='empty-text' style='margin-top:0.3rem'>{hint}</div>" if str(hint or "").strip() else ""
    box_key = hashlib.sha1(f"{title}|{detail}|{hint}".encode("utf-8")).hexdigest()[:10]
    st.markdown(
        (
            "<div class='empty-state-card'>"
            f"<div class='empty-title'>{title}</div>"
            f"<div class='empty-text'>{detail}</div>"
            f"{extra}"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    a1, a2 = st.columns(2)
    with a1:
        if st.button("Refresh Runtime Data", use_container_width=True, key=f"empty_refresh_{box_key}"):
            _load_runtime_db.clear()
            st.session_state["ui_palette_status"] = f"Refresh requested from empty state: {title}"
            st.rerun()
    with a2:
        if st.button("Open Tradeboard", use_container_width=True, key=f"empty_tradeboard_{box_key}"):
            _queue_workspace_switch("Tradeboard")
            st.session_state["ui_palette_status"] = "Opened Tradeboard from empty state."
            st.rerun()


def _render_skeleton(lines: int = 5) -> None:
    parts = ["<div class='desk-block'>"]
    for _ in range(max(1, int(lines))):
        parts.append("<div class='skeleton-line'></div>")
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def _workspace_nav_options(user_role: str) -> list[str]:
    tab_specs: list[tuple[str, str]] = [
        ("Command Center", ROLE_VIEWER),
        ("Tradeboard", ROLE_VIEWER),
        ("Multi-Chart", ROLE_VIEWER),
        ("Portfolio Pulse", ROLE_VIEWER),
        ("Execution Journal", ROLE_OPERATOR),
        ("Strategy Lab", ROLE_VIEWER),
        ("P&L Monitor", ROLE_VIEWER),
        ("Notifications", ROLE_VIEWER),
        ("Ops Health", ROLE_OPERATOR),
        ("UI Diagnostics", ROLE_OPERATOR),
        ("Audit Trail", ROLE_ADMIN),
        ("Operator Guide", ROLE_VIEWER),
        ("Backtest Hub", ROLE_VIEWER),
        ("UI Changelog", ROLE_VIEWER),
    ]
    allowed = [name for name, min_role in tab_specs if _role_at_least(user_role, min_role)]
    persona = str(st.session_state.get("ui_persona_mode", "Operator") or "Operator")
    if persona == "Viewer":
        viewer_tabs = {
            "Command Center",
            "Tradeboard",
            "Multi-Chart",
            "Portfolio Pulse",
            "P&L Monitor",
            "Notifications",
            "UI Diagnostics",
            "Operator Guide",
            "UI Changelog",
            "Backtest Hub",
        }
        allowed = [name for name in allowed if name in viewer_tabs]
    ranked = {name: idx for idx, name in enumerate(WORKSPACE_ORDER)}
    return sorted(allowed, key=lambda x: ranked.get(x, 999))


def _extract_symbols_from_notice_row(row: pd.Series) -> list[str]:
    parts = [
        str(row.get("title", "") or ""),
        str(row.get("detail", "") or ""),
        str(row.get("source", "") or ""),
    ]
    symbols: list[str] = []
    for part in parts:
        for token in re.findall(r"\b[A-Z]{3,5}\b", part.upper()):
            if token not in {"ERROR", "ALERT", "RUNTIME", "HIGH", "INFO", "WARN", "OPEN"}:
                symbols.append(token)
    out: list[str] = []
    seen: set[str] = set()
    for sym in symbols:
        if sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def _render_actionable_alert_center(notices_df: pd.DataFrame) -> None:
    if notices_df.empty:
        return
    open_notices = notices_df.copy()
    if "status" in open_notices.columns:
        open_notices = open_notices[open_notices["status"] != "acknowledged"].copy()
    if open_notices.empty:
        return
    top = open_notices.head(8).copy().reset_index(drop=True)
    st.markdown("<div class='alert-action-center'>", unsafe_allow_html=True)
    st.markdown("<div class='alert-center-title'>Action Center</div>", unsafe_allow_html=True)
    st.caption("Quick handling for active alerts from the current runtime scope.")
    options = [f"{idx+1}. [{str(r.get('severity', '')).upper()}] {str(r.get('title', 'Alert'))}" for idx, (_, r) in enumerate(top.iterrows())]
    selected = st.selectbox("Alert", options=options, index=0, key="ui_action_center_select")
    idx = max(0, min(len(top) - 1, int(str(selected).split(".", 1)[0]) - 1))
    row = top.iloc[idx]
    st.caption(str(row.get("detail", "") or ""))
    candidate_symbols = _extract_symbols_from_notice_row(row)
    c1, c2, c3 = st.columns([1.0, 1.0, 1.0])
    with c1:
        if st.button("Acknowledge", use_container_width=True, key="ui_action_center_ack"):
            actions = st.session_state.get("notice_actions", {})
            if not isinstance(actions, dict):
                actions = {}
            key = hashlib.sha1(
                f"{row.get('ts')}|{row.get('severity')}|{row.get('title')}|{row.get('source')}".encode("utf-8")
            ).hexdigest()[:16]
            actions[key] = {"status": "acknowledged", "updated_at": pd.Timestamp.now(tz=NY_TZ).isoformat()}
            st.session_state["notice_actions"] = actions
            st.session_state["ui_palette_status"] = "Alert acknowledged from Action Center."
            st.rerun()
    with c2:
        if st.button("Mute 30m", use_container_width=True, key="ui_action_center_mute"):
            actions = st.session_state.get("notice_actions", {})
            if not isinstance(actions, dict):
                actions = {}
            key = hashlib.sha1(
                f"{row.get('ts')}|{row.get('severity')}|{row.get('title')}|{row.get('source')}".encode("utf-8")
            ).hexdigest()[:16]
            actions[key] = {
                "status": "snoozed",
                "snooze_until": (pd.Timestamp.now(tz=NY_TZ) + pd.Timedelta(minutes=30)).isoformat(),
                "updated_at": pd.Timestamp.now(tz=NY_TZ).isoformat(),
            }
            st.session_state["notice_actions"] = actions
            st.session_state["ui_palette_status"] = "Alert snoozed for 30 minutes."
            st.rerun()
    with c3:
        if st.button("Open Symbol", use_container_width=True, key="ui_action_center_open_symbol"):
            if candidate_symbols:
                st.session_state["ui_pending_selected_symbol"] = candidate_symbols[0]
                st.session_state["ui_pending_saved_workspace"] = "Tradeboard"
                st.session_state["ui_palette_status"] = f"Focused symbol {candidate_symbols[0]} from alert."
                st.rerun()
            st.session_state["ui_palette_status"] = "No symbol found in selected alert."
    st.markdown("</div>", unsafe_allow_html=True)


def _render_risk_strip(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    hs = _health_snapshot(events_df, state_df)
    curve = _equity_curve_frame(events_df, state_df)
    snap = _pnl_snapshot(curve, state_df)
    fresh = hs.get("freshness_min")
    if fresh is None:
        fresh_state, fresh_txt = "warn", "-"
    elif float(fresh) <= 10:
        fresh_state, fresh_txt = "ok", f"{float(fresh):.1f}m"
    elif float(fresh) <= 60:
        fresh_state, fresh_txt = "warn", f"{float(fresh):.1f}m"
    else:
        fresh_state, fresh_txt = "bad", f"{float(fresh):.1f}m"

    err24 = int(hs.get("error_events_24h", 0))
    err_state = "ok" if err24 == 0 else ("warn" if err24 <= 3 else "bad")
    dd = float(pd.to_numeric(snap.get("max_drawdown_pct"), errors="coerce") or 0.0)
    dd_state = "ok" if dd <= 15 else ("warn" if dd <= 30 else "bad")
    cyc24 = int(hs.get("cycles_24h", 0))
    cyc_state = "ok" if cyc24 >= 1 else "warn"
    pos = _estimated_positions_table(events_df, state_df)
    net_abs = float(pd.to_numeric(pos.get("net_qty_est"), errors="coerce").fillna(0.0).abs().sum()) if not pos.empty else 0.0
    active_syms = int((pd.to_numeric(pos.get("net_qty_est"), errors="coerce").fillna(0.0).abs() > 0).sum()) if not pos.empty else 0
    exposure_state = "ok" if net_abs <= 200 else ("warn" if net_abs <= 600 else "bad")
    last_cycle_pnl = float(pd.to_numeric(snap.get("last_day_pnl"), errors="coerce") or 0.0)
    pnl_state = "ok" if last_cycle_pnl >= 0 else ("warn" if last_cycle_pnl >= -250 else "bad")
    ord_1h = int(hs.get("events_1h", 0))
    ord_state = "ok" if ord_1h >= 10 else ("warn" if ord_1h >= 1 else "bad")
    st.markdown(
        (
            "<div class='risk-strip'>"
            "<div class='risk-pill'>"
            "<div class='risk-pill-k'>Feed Freshness</div>"
            f"<div class='risk-pill-v'><span class='risk-dot {fresh_state}'></span>{fresh_txt}</div>"
            "</div>"
            "<div class='risk-pill'>"
            "<div class='risk-pill-k'>Error Events (24h)</div>"
            f"<div class='risk-pill-v'><span class='risk-dot {err_state}'></span>{err24}</div>"
            "</div>"
            "<div class='risk-pill'>"
            "<div class='risk-pill-k'>Max Drawdown</div>"
            f"<div class='risk-pill-v'><span class='risk-dot {dd_state}'></span>{dd:.2f}%</div>"
            "</div>"
            "<div class='risk-pill'>"
            "<div class='risk-pill-k'>Cycles (24h)</div>"
            f"<div class='risk-pill-v'><span class='risk-dot {cyc_state}'></span>{cyc24}</div>"
            "</div>"
            "<div class='risk-pill'>"
            "<div class='risk-pill-k'>Net Qty Exposure</div>"
            f"<div class='risk-pill-v'><span class='risk-dot {exposure_state}'></span>{net_abs:,.2f} | syms {active_syms}</div>"
            "</div>"
            "<div class='risk-pill'>"
            "<div class='risk-pill-k'>Last Cycle PnL</div>"
            f"<div class='risk-pill-v'><span class='risk-dot {pnl_state}'></span>${last_cycle_pnl:,.2f}</div>"
            "</div>"
            "<div class='risk-pill'>"
            "<div class='risk-pill-k'>Events (1h)</div>"
            f"<div class='risk-pill-v'><span class='risk-dot {ord_state}'></span>{ord_1h}</div>"
            "</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
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
    st.markdown("<div class='sticky-global'><div class='desk-block'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-caption'>Global filters and workspace sync controls (applied to all tabs unless overridden).</div>",
        unsafe_allow_html=True,
    )
    presets = st.session_state.get("ui_global_filter_presets", {})
    if not isinstance(presets, dict):
        presets = {}
    default_presets: dict[str, dict[str, Any]] = {
        "Today: High Priority": {
            "enabled": True,
            "window": "1D",
            "query": "error|reject|fail|profit_lock",
            "symbols": [],
            "event_types": [],
            "variants": [],
            "sides": [],
            "order_types": [],
        },
        "Week: Execution Focus": {
            "enabled": True,
            "window": "1W",
            "query": "order|profit_lock|rebalance",
            "symbols": [],
            "event_types": [],
            "variants": [],
            "sides": [],
            "order_types": [],
        },
        "Month: Full Scope": {
            "enabled": True,
            "window": "1M",
            "query": "",
            "symbols": [],
            "event_types": [],
            "variants": [],
            "sides": [],
            "order_types": [],
        },
    }
    preset_all = {**default_presets, **presets}
    top1, top2, top3, top4 = st.columns([1.45, 1.05, 0.9, 0.9])
    with top1:
        preset_name = st.selectbox(
            "Filter Preset",
            options=["(none)"] + sorted(preset_all.keys()),
            index=0,
            key="ui_global_preset_name",
        )
    with top2:
        save_name = st.text_input("Save As", value=str(st.session_state.get("ui_global_preset_save_name", "") or ""), key="ui_global_preset_save_name")
    with top3:
        if st.button("Apply Preset", use_container_width=True, key="ui_global_preset_apply_btn"):
            if preset_name != "(none)" and preset_name in preset_all:
                st.session_state["ui_global_preset_pending_apply"] = preset_all[preset_name]
                st.session_state["ui_palette_status"] = f"Applied global preset: {preset_name}"
                st.rerun()
    with top4:
        if st.button("Save Current", use_container_width=True, key="ui_global_preset_save_btn"):
            name = str(save_name or "").strip()
            if name:
                state_payload = {
                    "enabled": bool(st.session_state.get("ui_global_filters_enabled", False)),
                    "window": str(st.session_state.get("ui_global_time_window", "1M") or "1M"),
                    "query": str(st.session_state.get("ui_global_filter_query", "") or ""),
                    "symbols": list(st.session_state.get("ui_global_symbols", []) or []),
                    "event_types": list(st.session_state.get("ui_global_event_types", []) or []),
                    "variants": list(st.session_state.get("ui_global_variants", []) or []),
                    "sides": list(st.session_state.get("ui_global_sides", []) or []),
                    "order_types": list(st.session_state.get("ui_global_order_types", []) or []),
                }
                presets[name] = state_payload
                st.session_state["ui_global_filter_presets"] = presets
                st.session_state["ui_palette_status"] = f"Saved global preset: {name}"
                st.rerun()
            st.warning("Enter a preset name first.")

    p1, p2, _ = st.columns([0.9, 0.9, 2.2])
    with p1:
        if st.button("Delete Preset", use_container_width=True, key="ui_global_preset_delete_btn"):
            if preset_name in presets:
                presets.pop(preset_name, None)
                st.session_state["ui_global_filter_presets"] = presets
                st.session_state["ui_palette_status"] = f"Deleted preset: {preset_name}"
                st.rerun()
    with p2:
        if st.button("Reset Filters", use_container_width=True, key="ui_global_preset_reset_btn"):
            st.session_state["ui_global_preset_pending_apply"] = {
                "enabled": False,
                "window": "1M",
                "query": "",
                "symbols": [],
                "event_types": [],
                "variants": [],
                "sides": [],
                "order_types": [],
            }
            st.session_state["ui_palette_status"] = "Global filters reset."
            st.rerun()

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
    st.markdown("</div></div>", unsafe_allow_html=True)
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


def _table_customize(df: pd.DataFrame, *, key: str, default_sort_col: str = "", default_desc: bool = True) -> pd.DataFrame:
    if df.empty:
        return df
    cols = [str(c) for c in df.columns.tolist()]
    sort_opts = ["(none)"] + cols
    preferred_sort = default_sort_col if default_sort_col in cols else "(none)"

    pending_view = st.session_state.get(f"{key}_table_pending_view", None)
    if isinstance(pending_view, dict):
        st.session_state[f"{key}_visible_cols"] = [c for c in list(pending_view.get("visible_cols", cols)) if c in cols] or cols
        sort_pending = str(pending_view.get("sort_col", preferred_sort) or preferred_sort)
        st.session_state[f"{key}_sort_col"] = sort_pending if sort_pending in sort_opts else preferred_sort
        st.session_state[f"{key}_sort_desc"] = bool(pending_view.get("sort_desc", bool(default_desc)))
        st.session_state[f"{key}_contains"] = str(pending_view.get("contains", "") or "")
        st.session_state[f"{key}_pinned_cols"] = [c for c in list(pending_view.get("pinned_cols", [])) if c in cols]
        st.session_state[f"{key}_table_pending_view"] = None

    all_views_raw = st.session_state.get("ui_saved_views", {})
    all_views = all_views_raw if isinstance(all_views_raw, dict) else {}
    table_views_raw = all_views.get(key, {})
    table_views = table_views_raw if isinstance(table_views_raw, dict) else {}

    with st.expander("Table Controls", expanded=False):
        c1, c2, c3 = st.columns([1.25, 1.0, 1.0])
        with c1:
            selected_cols = st.multiselect(
                "Visible Columns",
                options=cols,
                default=cols,
                key=f"{key}_visible_cols",
            )
        with c2:
            sort_col = st.selectbox(
                "Sort Column",
                options=sort_opts,
                index=sort_opts.index(preferred_sort),
                key=f"{key}_sort_col",
            )
        with c3:
            desc = st.checkbox(
                "Sort Desc",
                value=bool(default_desc),
                key=f"{key}_sort_desc",
            )
        q = st.text_input(
            "Contains Filter",
            value="",
            key=f"{key}_contains",
            placeholder="text search across visible columns",
        )
        pinned_cols = st.multiselect(
            "Pin Columns (left-first order)",
            options=selected_cols if selected_cols else cols,
            default=[c for c in list(st.session_state.get(f"{key}_pinned_cols", [])) if c in (selected_cols if selected_cols else cols)],
            key=f"{key}_pinned_cols",
        )

        st.markdown("---")
        v1, v2, v3 = st.columns([1.2, 1.1, 1.1])
        with v1:
            view_name = st.text_input(
                "View Name",
                value="",
                key=f"{key}_table_view_name",
                placeholder="save this table setup",
            )
        with v2:
            available = sorted(table_views.keys())
            chosen_view = st.selectbox(
                "Saved Views",
                options=["(none)"] + available,
                index=0,
                key=f"{key}_table_saved_pick",
            )
        with v3:
            st.caption("Column scope for this table only.")
            apply_clicked = st.button("Apply View", use_container_width=True, key=f"{key}_table_apply_view")
            save_clicked = st.button("Save View", use_container_width=True, key=f"{key}_table_save_view")
            delete_clicked = st.button("Delete View", use_container_width=True, key=f"{key}_table_delete_view")

        if apply_clicked:
            if chosen_view != "(none)" and chosen_view in table_views:
                st.session_state[f"{key}_table_pending_view"] = table_views[chosen_view]
                st.session_state["ui_palette_status"] = f"Applied table view: {chosen_view}"
                st.rerun()
            st.info("Select a saved view first.")
        if save_clicked:
            vname = str(view_name or "").strip()
            if not vname:
                st.warning("Enter view name.")
            else:
                payload = {
                    "visible_cols": list(selected_cols) if selected_cols else cols,
                    "sort_col": str(sort_col),
                    "sort_desc": bool(desc),
                    "contains": str(q or ""),
                    "pinned_cols": list(pinned_cols) if pinned_cols else [],
                }
                table_views[vname] = payload
                all_views[key] = table_views
                st.session_state["ui_saved_views"] = all_views
                st.session_state["ui_palette_status"] = f"Saved table view: {vname}"
                st.rerun()
        if delete_clicked:
            if chosen_view != "(none)" and chosen_view in table_views:
                table_views.pop(chosen_view, None)
                all_views[key] = table_views
                st.session_state["ui_saved_views"] = all_views
                st.session_state["ui_palette_status"] = f"Deleted table view: {chosen_view}"
                st.rerun()
            st.info("Select a saved view first.")
    out = df.copy()
    chosen = [c for c in selected_cols if c in out.columns] if selected_cols else cols
    pin_chosen = [c for c in (pinned_cols if pinned_cols else []) if c in chosen]
    tail_cols = [c for c in chosen if c not in pin_chosen]
    chosen = pin_chosen + tail_cols
    out = out[chosen].copy()
    needle = str(q or "").strip().lower()
    if needle:
        mask = pd.Series(False, index=out.index)
        for col in out.columns:
            mask = mask | out[col].astype(str).str.lower().str.contains(needle, na=False)
        out = out[mask]
    if sort_col != "(none)" and sort_col in out.columns:
        out = out.sort_values(sort_col, ascending=(not bool(desc)))
    rendered = out.reset_index(drop=True)
    st.caption(
        f"Table Rows: {len(rendered):,} / {len(df):,} | Columns: {len(rendered.columns):,}"
        + (f" | Sorted: {sort_col} ({'desc' if bool(desc) else 'asc'})" if sort_col != "(none)" else "")
    )
    return rendered


def _tradeboard_split_columns(split: str) -> list[float]:
    mode = str(split or "Balanced").strip()
    mapping: dict[str, list[float]] = {
        "Balanced": [0.8, 1.95, 0.75],
        "Chart Focus": [0.65, 2.35, 0.6],
        "Watchlist Focus": [1.1, 1.75, 0.75],
        "Stats Focus": [0.75, 1.7, 1.05],
    }
    if mode == "Custom":
        raw = st.session_state.get("ui_tradeboard_custom_weights", [0.8, 1.95, 0.75])
        if isinstance(raw, list) and len(raw) == 3:
            vals = [max(0.2, float(pd.to_numeric(v, errors="coerce") or 1.0)) for v in raw]
            return vals
    return mapping.get(mode, mapping["Balanced"])


def _chart_png_bytes(chart: alt.Chart) -> bytes | None:
    try:
        buf = io.BytesIO()
        chart.save(buf, format="png")
        return buf.getvalue()
    except Exception:
        return None


def _execution_split_columns(split: str) -> list[float]:
    mode = str(split or "Balanced").strip()
    mapping: dict[str, list[float]] = {
        "Balanced": [1.15, 1.0],
        "Blotter Focus": [1.35, 0.8],
        "Analytics Focus": [0.95, 1.2],
    }
    if mode == "Custom":
        raw = st.session_state.get("ui_execution_custom_weights", [1.15, 1.0])
        if isinstance(raw, list) and len(raw) == 2:
            vals = [max(0.2, float(pd.to_numeric(v, errors="coerce") or 1.0)) for v in raw]
            return vals
    return mapping.get(mode, mapping["Balanced"])


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


def _reference_rule_layer(reference_ts: Any, y_title: str = "Price") -> alt.Chart | None:
    ref_ts = pd.to_datetime(reference_ts, errors="coerce")
    if pd.isna(ref_ts):
        return None
    if ref_ts.tzinfo is None:
        try:
            ref_ts = ref_ts.tz_localize(NY_TZ)
        except Exception:
            return None
    else:
        ref_ts = ref_ts.tz_convert(NY_TZ)
    ref_df = pd.DataFrame({"ts_ny": [ref_ts]})
    return (
        alt.Chart(ref_df)
        .mark_rule(color="#d6def0", strokeDash=[4, 4], strokeWidth=1.2, opacity=0.85)
        .encode(x=alt.X("ts_ny:T", title="Time (NY)"), tooltip=["ts_ny:T"])
    )


def _market_candlestick_chart(
    symbol: str,
    window: str,
    feed: str = "sip",
    events_df: pd.DataFrame | None = None,
    reference_ts: Any = None,
) -> tuple[alt.Chart | None, str]:
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
    layers: list[alt.Chart] = [wick, body, vwap_line]
    if events_df is not None and isinstance(events_df, pd.DataFrame) and (not events_df.empty):
        fills = _symbol_price_event_df(events_df, symbol)
        if not fills.empty:
            markers = fills.copy()
            markers["ts_ny"] = pd.to_datetime(markers["ts"], errors="coerce")
            markers = markers.dropna(subset=["ts_ny", "execution_price"]).copy()
            if not markers.empty:
                markers["side_norm"] = markers["side"].astype(str).str.lower().map(
                    lambda s: "buy" if s == "buy" else ("sell" if s == "sell" else "other")
                )
                marker_layer = (
                    alt.Chart(markers)
                    .mark_point(size=68, filled=True, opacity=0.95)
                    .encode(
                        x=alt.X("ts_ny:T"),
                        y=alt.Y("execution_price:Q", title="Price"),
                        color=alt.Color(
                            "side_norm:N",
                            scale=alt.Scale(domain=["buy", "sell", "other"], range=["#00c805", "#ff5d7a", "#9fb3d5"]),
                            legend=None,
                        ),
                        shape=alt.Shape(
                            "side_norm:N",
                            scale=alt.Scale(domain=["buy", "sell", "other"], range=["triangle-up", "triangle-down", "circle"]),
                            legend=None,
                        ),
                        tooltip=[
                            "ts_ny:T",
                            "side:N",
                            alt.Tooltip("qty:Q", format=",.4f"),
                            alt.Tooltip("execution_price:Q", format=",.4f"),
                            "event_type:N",
                        ],
                    )
                )
                layers.append(marker_layer)
    ref_rule = _reference_rule_layer(reference_ts)
    if ref_rule is not None:
        layers.append(ref_rule)
    price_panel = alt.layer(*layers).properties(height=300)
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
        momentum_score = 0.0
        risk_score = 0.0
        sym_flow = _symbol_activity_df(events_df, sym)
        if not sym_flow.empty:
            cum = pd.to_numeric(sym_flow.get("cum_position"), errors="coerce").dropna().astype(float)
            if len(cum) >= 2:
                momentum_score = float(cum.iloc[-1] - cum.iloc[max(0, len(cum) - 6)])
            signed = pd.to_numeric(sym_flow.get("signed_qty"), errors="coerce").dropna().astype(float)
            if not signed.empty:
                risk_score = float(signed.abs().rolling(window=min(6, len(signed))).mean().iloc[-1]) if len(signed) >= 2 else float(signed.abs().iloc[-1])
        rows.append(
            {
                "symbol": sym,
                "target_wt_%": target_map.get(sym),
                "net_qty_est": net,
                "order_events": 0 if sym_orders.empty else int(len(sym_orders)),
                "last_side": last_side,
                "last_event_ts": last_ts,
                "momentum_score": momentum_score,
                "risk_score": risk_score,
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
    max_ev = int(pd.to_numeric(watch.get("order_events"), errors="coerce").fillna(0.0).max()) if "order_events" in watch.columns else 0
    ctl1, ctl2, ctl3, ctl4 = st.columns([1.0, 1.05, 1.0, 1.0])
    with ctl1:
        st.slider(
            "Min Events",
            min_value=0,
            max_value=max(1, int(max_ev)),
            value=min(int(pd.to_numeric(st.session_state.get("ui_watchlist_min_events", 0), errors="coerce") or 0), max(1, int(max_ev))),
            step=1,
            key="ui_watchlist_min_events",
            help="Hide symbols with fewer execution events in current scope.",
        )
    with ctl2:
        st.selectbox(
            "Sort",
            options=["Activity", "Target Weight", "Momentum", "Risk", "Alphabetical"],
            index=["Activity", "Target Weight", "Momentum", "Risk", "Alphabetical"].index(str(st.session_state.get("ui_watchlist_sort_mode", "Activity")))
            if str(st.session_state.get("ui_watchlist_sort_mode", "Activity")) in {"Activity", "Target Weight", "Momentum", "Risk", "Alphabetical"}
            else 0,
            key="ui_watchlist_sort_mode",
        )
    with ctl3:
        st.selectbox(
            "Card Density",
            options=["Compact", "Standard", "Expanded"],
            index=["Compact", "Standard", "Expanded"].index(str(st.session_state.get("ui_watchlist_card_density", "Standard")))
            if str(st.session_state.get("ui_watchlist_card_density", "Standard")) in {"Compact", "Standard", "Expanded"}
            else 1,
            key="ui_watchlist_card_density",
        )
    with ctl4:
        st.checkbox(
            "Quick Actions",
            value=bool(st.session_state.get("ui_watchlist_quick_actions", True)),
            key="ui_watchlist_quick_actions",
            help="Show/hide watchlist quick action buttons to reduce clutter.",
        )
    min_events = int(pd.to_numeric(st.session_state.get("ui_watchlist_min_events", 0), errors="coerce") or 0)
    if min_events > 0:
        filtered = watch[pd.to_numeric(watch["order_events"], errors="coerce").fillna(0.0) >= float(min_events)].copy()
        if not filtered.empty:
            watch = filtered

    sort_mode = str(st.session_state.get("ui_watchlist_sort_mode", "Activity") or "Activity")
    if sort_mode == "Target Weight":
        watch["_tw"] = pd.to_numeric(watch["target_wt_%"], errors="coerce").fillna(-1.0)
        watch = watch.sort_values(["pinned", "_tw", "order_events", "symbol"], ascending=[False, False, False, True]).drop(columns=["_tw"])
    elif sort_mode == "Momentum":
        watch["_mom"] = pd.to_numeric(watch.get("momentum_score"), errors="coerce").fillna(0.0)
        watch = watch.sort_values(["pinned", "_mom", "order_events", "symbol"], ascending=[False, False, False, True]).drop(columns=["_mom"])
    elif sort_mode == "Risk":
        watch["_risk"] = pd.to_numeric(watch.get("risk_score"), errors="coerce").fillna(0.0)
        watch = watch.sort_values(["pinned", "_risk", "order_events", "symbol"], ascending=[False, True, False, True]).drop(columns=["_risk"])
    elif sort_mode == "Alphabetical":
        watch = watch.sort_values(["pinned", "symbol"], ascending=[False, True])
    else:
        watch = watch.sort_values(["pinned", "order_events", "symbol"], ascending=[False, False, True])
    watch = watch.reset_index(drop=True)

    pinned_only = st.checkbox("Pinned Only", key="ui_watchlist_pinned_only")
    if pinned_only:
        pinned_view = watch[watch["pinned"]].copy()
        if not pinned_view.empty:
            watch = pinned_view.reset_index(drop=True)

    symbols = watch["symbol"].astype(str).tolist()
    current = st.session_state.get("ui_selected_symbol", symbols[0])
    if current not in symbols:
        current = symbols[0]
    st.session_state["ui_selected_symbol"] = current

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
    st.session_state["ui_selected_symbol"] = selected

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
    if bool(st.session_state.get("ui_watchlist_quick_actions", True)):
        qa1, qa2, qa3 = st.columns(3)
        with qa1:
            if st.button("Focus Selected", use_container_width=True, key=f"ui_watchlist_focus_{selected}"):
                st.session_state["ui_pending_selected_symbol"] = selected
                st.session_state["ui_palette_status"] = f"Focused {selected}."
                st.rerun()
        with qa2:
            if st.button("Filter To Selected", use_container_width=True, key=f"ui_watchlist_filter_selected_{selected}"):
                st.session_state["ui_global_filters_enabled"] = True
                st.session_state["ui_global_symbols"] = [selected]
                st.session_state["ui_palette_status"] = f"Global filter pinned to {selected}."
                st.rerun()
        with qa3:
            if st.button("Snapshot", use_container_width=True, key=f"ui_watchlist_snapshot_{selected}"):
                snaps_raw = st.session_state.get("ui_watchlist_snapshots", [])
                snaps = list(snaps_raw) if isinstance(snaps_raw, list) else []
                snaps.insert(
                    0,
                    {
                        "ts_ny": pd.Timestamp.now(tz=NY_TZ).isoformat(),
                        "symbol": selected,
                        "sort_mode": sort_mode,
                        "min_events": int(min_events),
                    },
                )
                st.session_state["ui_watchlist_snapshots"] = snaps[:100]
                st.session_state["ui_palette_status"] = f"Snapshot saved for {selected}."
                st.rerun()

    card_density = str(st.session_state.get("ui_watchlist_card_density", "Standard") or "Standard")
    st.caption(f"Pinned: {len(pin_set)} | Card Density: {card_density}")
    st.markdown("<div class='section-caption'>Watchlist Cards</div>", unsafe_allow_html=True)
    for _, row in shown.iterrows():
        symbol = str(row["symbol"])
        badge_w = "-" if pd.isna(row.get("target_wt_%")) else f"{float(pd.to_numeric(row.get('target_wt_%'), errors='coerce') or 0.0):.1f}%"
        badge_e = int(pd.to_numeric(row.get("order_events"), errors="coerce") or 0)
        last_side = str(row.get("last_side") or "-").upper()
        net_qty = float(pd.to_numeric(row.get("net_qty_est"), errors="coerce") or 0.0)
        last_evt_raw = str(row.get("last_event_ts") or "").strip()
        last_evt_txt = "-"
        if last_evt_raw and last_evt_raw != "-":
            try:
                last_evt = pd.to_datetime(last_evt_raw, errors="coerce", utc=True)
                if not pd.isna(last_evt):
                    last_evt_txt = last_evt.tz_convert(NY_TZ).strftime("%m-%d %H:%M")
            except Exception:
                last_evt_txt = "-"
        net_txt = f"{net_qty:+.2f}"
        pinned_cls = "★ " if bool(row.get("pinned", False)) else ""
        active_cls = " (active)" if symbol == str(st.session_state.get("ui_selected_symbol", "")) else ""
        density_cls = "compact" if card_density == "Compact" else ("expanded" if card_density == "Expanded" else "standard")
        st.markdown(
            (
                f"<div class='watch-item {density_cls}'>"
                f"<div class='watch-item-top'><span class='watch-item-symbol'>{pinned_cls}{symbol}{active_cls}</span>"
                f"<span class='watch-item-badge'>wt {badge_w}</span> "
                f"<span class='watch-item-badge'>ev {badge_e}</span> "
                f"<span class='watch-item-badge'>{last_side}</span></div>"
                + (f"<div class='watch-item-sub'>net {net_txt} | last {last_evt_txt} ET</div>" if card_density != "Compact" else "")
                + "</div>"
            ),
            unsafe_allow_html=True,
        )
        if card_density != "Compact":
            mini = _watchlist_card_spark_chart(events_df, symbol)
            if mini is not None:
                st.markdown("<div class='watch-item-spark-wrap'>", unsafe_allow_html=True)
                st.altair_chart(mini, use_container_width=True)
                if card_density == "Expanded":
                    st.markdown(
                        f"<div class='watch-item-spark-caption'>side={last_side} | net_qty={net_txt} | last={last_evt_txt} ET</div>",
                        unsafe_allow_html=True,
                    )
                st.markdown("</div>", unsafe_allow_html=True)
    compact = shown[["pinned", "symbol", "target_wt_%", "net_qty_est", "order_events", "last_side"]].copy()
    compact["pinned"] = compact["pinned"].map(lambda v: "★" if bool(v) else "")
    st.dataframe(compact, use_container_width=True, hide_index=True, height=280)
    return str(st.session_state.get("ui_selected_symbol", ""))


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
    st.caption(
        "Command Palette 2.0: search/filter commands, then run. "
        "Shortcuts: `Ctrl/Cmd+K` palette context, `Alt+R` refresh, `Alt+1..6` workspace jump."
    )
    fav_raw = st.session_state.get("ui_command_favorites", [])
    favorites = [str(x) for x in fav_raw if str(x).strip()] if isinstance(fav_raw, list) else []
    if favorites:
        f1, f2 = st.columns([2.4, 1.0])
        with f1:
            fav_pick = st.selectbox("Favorite Commands", options=favorites, index=0, key="ui_palette_favorite_pick")
        with f2:
            if st.button("Run Favorite", use_container_width=True, key="ui_palette_favorite_run"):
                st.session_state["ui_command_palette_action"] = fav_pick
                st.session_state["ui_palette_status"] = f"Favorite command selected: {fav_pick}"
                st.rerun()
    if st.button("Add Current To Favorites", use_container_width=True, key="ui_palette_add_favorite"):
        action_txt = str(st.session_state.get("ui_command_palette_action", "") or "").strip()
        if action_txt:
            if action_txt not in favorites:
                favorites.append(action_txt)
            st.session_state["ui_command_favorites"] = sorted(set(favorites))
            st.session_state["ui_palette_status"] = f"Added favorite: {action_txt}"
            st.rerun()

    if run:
        selected_symbol = str(st.session_state.get("ui_selected_symbol", "") or "").strip().upper()
        _record_ui_command_event(action, str(search or ""))
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


def _symbol_candlestick_chart(events_df: pd.DataFrame, symbol: str, reference_ts: Any = None) -> alt.Chart | None:
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
    chart = (wick + body)
    ref_rule = _reference_rule_layer(reference_ts)
    if ref_rule is not None:
        chart = chart + ref_rule
    return (
        chart.properties(height=360)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_view(strokeOpacity=0)
    )


def _symbol_activity_chart(
    events_df: pd.DataFrame,
    symbol: str,
    chart_type: str = "Line",
    with_markers: bool = False,
    reference_ts: Any = None,
) -> alt.Chart | None:
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

        core = (
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
        )
        ref_rule = _reference_rule_layer(reference_ts, y_title="Filled Qty (Signed)")
        chart = core
        if ref_rule is not None:
            chart = alt.layer(core, ref_rule).resolve_scale(y="independent")
        return chart.configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)").configure_legend(labelColor="#d8e6ff", titleColor="#d8e6ff").configure_view(strokeOpacity=0)
    base = alt.Chart(frame).encode(
        x=alt.X("ts:T", title="Time (NY)"),
        y=alt.Y("cum_position:Q", title="Estimated Net Position"),
        tooltip=["ts:T", "event_type:N", "side:N", "qty:Q", "cum_position:Q", "order_type:N"],
    )
    line = base.mark_line(
        point=alt.OverlayMarkDef(filled=True, size=78) if with_markers else True,
        strokeWidth=2.4,
        color="#00c805",
    )
    core = line.properties(height=360)
    chart = core
    ref_rule = _reference_rule_layer(reference_ts, y_title="Estimated Net Position")
    if ref_rule is not None:
        chart = alt.layer(core, ref_rule).resolve_scale(y="independent")
    return chart.configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)").configure_view(strokeOpacity=0)


def _symbol_activity_marker_chart(events_df: pd.DataFrame, symbol: str) -> alt.Chart | None:
    frame = _symbol_activity_df(events_df, symbol)
    if frame.empty:
        return None
    frame = frame.copy()
    frame["side_norm"] = frame["side"].astype(str).str.lower().map(lambda s: "buy" if s == "buy" else ("sell" if s == "sell" else "other"))
    return (
        alt.Chart(frame)
        .mark_point(size=78, filled=True, opacity=0.92)
        .encode(
            x=alt.X("ts:T", title="Time (NY)"),
            y=alt.Y("cum_position:Q", title="Estimated Net Position"),
            color=alt.Color(
                "side_norm:N",
                scale=alt.Scale(domain=["buy", "sell", "other"], range=["#00c805", "#ff5d7a", "#aab4be"]),
                title="Execution Side",
            ),
            shape=alt.Shape(
                "side_norm:N",
                scale=alt.Scale(domain=["buy", "sell", "other"], range=["triangle-up", "triangle-down", "circle"]),
                legend=None,
            ),
            tooltip=["ts:T", "event_type:N", "side:N", "qty:Q", "cum_position:Q", "order_type:N"],
        )
        .properties(height=360)
    )


def _multi_symbol_compare_chart(events_df: pd.DataFrame, symbols: list[str], limit: int = 4, reference_ts: Any = None) -> alt.Chart | None:
    picks = [str(s).strip().upper() for s in symbols if str(s).strip()]
    if not picks:
        return None
    picks = picks[: max(1, int(limit))]
    frames: list[pd.DataFrame] = []
    for sym in picks:
        sym_df = _symbol_activity_df(events_df, sym)
        if sym_df.empty:
            continue
        tmp = sym_df[["ts", "cum_position"]].copy()
        tmp["symbol"] = sym
        tmp["cum_position"] = pd.to_numeric(tmp["cum_position"], errors="coerce")
        tmp = tmp.dropna(subset=["ts", "cum_position"])
        if tmp.empty:
            continue
        base = float(tmp["cum_position"].iloc[0])
        tmp["position_delta"] = tmp["cum_position"] - base
        frames.append(tmp)
    if not frames:
        return None
    plot = pd.concat(frames, ignore_index=True).sort_values("ts")
    core = (
        alt.Chart(plot)
        .mark_line(strokeWidth=2.1)
        .encode(
            x=alt.X("ts:T", title="Time (NY)"),
            y=alt.Y("position_delta:Q", title="Position Delta From First Event"),
            color=alt.Color("symbol:N", title="Symbol"),
            tooltip=["ts:T", "symbol:N", alt.Tooltip("position_delta:Q", format=",.4f")],
        )
        .properties(height=220)
    )
    chart = core
    ref_rule = _reference_rule_layer(reference_ts, y_title="Position Delta")
    if ref_rule is not None:
        chart = alt.layer(core, ref_rule).resolve_scale(y="independent")
    return (
        chart.configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_legend(labelColor="#d8e6ff", titleColor="#d8e6ff")
        .configure_view(strokeOpacity=0)
    )


def _mini_symbol_activity_chart(events_df: pd.DataFrame, symbol: str) -> alt.Chart | None:
    frame = _symbol_activity_df(events_df, symbol)
    if frame.empty:
        return None
    return (
        alt.Chart(frame)
        .mark_line(strokeWidth=1.8, color="#66d7ff")
        .encode(
            x=alt.X("ts:T", title=None),
            y=alt.Y("cum_position:Q", title=None),
            tooltip=["ts:T", "event_type:N", "side:N", "qty:Q", "cum_position:Q"],
        )
        .properties(height=120)
        .configure_axis(labelColor="#95abcf", titleColor="#95abcf", gridColor="rgba(159,179,213,0.16)")
        .configure_view(strokeOpacity=0)
    )


def _watchlist_card_spark_chart(events_df: pd.DataFrame, symbol: str, points: int = 28) -> alt.Chart | None:
    frame = _symbol_activity_df(events_df, symbol)
    if frame.empty:
        return None
    plot = frame[["ts", "cum_position"]].copy()
    plot["ts"] = pd.to_datetime(plot["ts"], errors="coerce")
    plot["cum_position"] = pd.to_numeric(plot["cum_position"], errors="coerce")
    plot = plot.dropna(subset=["ts", "cum_position"]).sort_values("ts")
    if plot.empty:
        return None
    plot = plot.tail(max(8, int(points)))
    line = (
        alt.Chart(plot)
        .mark_line(strokeWidth=2.0, color="#8ab4ff", interpolate="monotone")
        .encode(
            x=alt.X("ts:T", axis=None),
            y=alt.Y("cum_position:Q", axis=None),
            tooltip=[
                alt.Tooltip("ts:T", title="Time"),
                alt.Tooltip("cum_position:Q", title="Cum Pos", format=",.4f"),
            ],
        )
    )
    points_layer = (
        alt.Chart(plot.tail(1))
        .mark_point(size=26, filled=True, color="#c4d7ff")
        .encode(x=alt.X("ts:T", axis=None), y=alt.Y("cum_position:Q", axis=None))
    )
    return (
        (line + points_layer)
        .properties(height=36)
        .configure_axis(grid=False, ticks=False, domain=False, labels=False)
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


def _render_execution_kpi_strip(orders_enriched: pd.DataFrame) -> None:
    if orders_enriched.empty:
        return
    frame = orders_enriched.copy()
    if "ts_parsed" not in frame.columns:
        frame["ts_parsed"] = pd.to_datetime(frame.get("ts"), errors="coerce")
    else:
        frame["ts_parsed"] = pd.to_datetime(frame["ts_parsed"], errors="coerce")
    frame = frame.dropna(subset=["ts_parsed"]).sort_values("ts_parsed")
    if frame.empty:
        return

    latest = frame.iloc[-1]
    latest_sym = str(latest.get("symbol_norm") or latest.get("symbol") or "-")
    latest_side = str(latest.get("side_norm") or latest.get("side") or "-").upper()
    latest_qty = float(pd.to_numeric(latest.get("qty_num"), errors="coerce") or pd.to_numeric(latest.get("qty"), errors="coerce") or 0.0)
    latest_px = float(pd.to_numeric(latest.get("exec_price"), errors="coerce") or 0.0)
    latest_ts = pd.to_datetime(latest.get("ts_parsed"), errors="coerce")
    if pd.isna(latest_ts):
        latest_ts_txt = "-"
    else:
        try:
            if latest_ts.tzinfo is None:
                latest_ts = latest_ts.tz_localize(NY_TZ)
            else:
                latest_ts = latest_ts.tz_convert(NY_TZ)
            latest_ts_txt = latest_ts.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            latest_ts_txt = str(latest_ts)

    _, sl = _execution_slippage_summary(frame)
    avg_adv = float(sl.get("avg_adverse_bps", 0.0) or 0.0) if sl else 0.0
    p95_adv = float(sl.get("p95_adverse_bps", 0.0) or 0.0) if sl else 0.0
    avg_lat = sl.get("avg_fill_latency_sec") if sl else None
    avg_lat_txt = "-" if avg_lat is None else f"{float(avg_lat):.2f}s"

    cutoff = pd.Timestamp.now(tz=NY_TZ) - pd.Timedelta(hours=24)
    recent = frame[frame["ts_parsed"] >= cutoff].copy()
    recent_fills = int(len(recent))
    recent_syms = int(recent["symbol_norm"].astype(str).nunique()) if "symbol_norm" in recent.columns else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Latest Fill", f"{latest_sym} {latest_side}", f"qty {latest_qty:.4f} @ {latest_px:.4f}")
    c2.metric("Latest Fill Time (NY)", latest_ts_txt)
    c3.metric("Avg Adverse Slippage", f"{avg_adv:.2f} bps", f"P95 {p95_adv:.2f} bps")
    c4.metric("Avg Fill Latency", avg_lat_txt)
    c5.metric("24h Fill Flow", f"{recent_fills:,} fills", f"{recent_syms} symbols")


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
        _render_skeleton(lines=4)
        _render_empty_state(
            "No symbol stream detected",
            "No symbols are available in current runtime state/events.",
            "Confirm DB path, runtime loop health, and active event ingestion before trading decisions.",
        )
        st.markdown("</div>", unsafe_allow_html=True)
        return
    _apply_terminal_pending_ui_state(symbols)
    layout_locked = bool(st.session_state.get("ui_panel_layout_locked", False))

    default_symbol = symbols[0]
    if "ui_selected_symbol" not in st.session_state or st.session_state.ui_selected_symbol not in symbols:
        st.session_state.ui_selected_symbol = default_symbol

    top_ctrl_left, top_ctrl_mid, top_ctrl_right = st.columns([1.0, 1.0, 1.8])
    with top_ctrl_left:
        jump_symbol = st.selectbox(
            "Quick Symbol Jump",
            options=symbols,
            index=symbols.index(st.session_state.ui_selected_symbol),
            key="ui_symbol_jump_select",
        )
        st.session_state.ui_selected_symbol = jump_symbol
    with top_ctrl_mid:
        st.selectbox(
            "Panel Layout",
            options=["Balanced", "Chart Focus", "Watchlist Focus", "Stats Focus", "Custom"],
            index=["Balanced", "Chart Focus", "Watchlist Focus", "Stats Focus", "Custom"].index(
                str(st.session_state.get("ui_tradeboard_split", "Balanced"))
                if str(st.session_state.get("ui_tradeboard_split", "Balanced")) in {"Balanced", "Chart Focus", "Watchlist Focus", "Stats Focus", "Custom"}
                else "Balanced"
            ),
            key="ui_tradeboard_split",
            disabled=layout_locked,
        )
    if str(st.session_state.get("ui_tradeboard_split", "Balanced")) == "Custom":
        cw1, cw2, cw3 = st.columns(3)
        raw = st.session_state.get("ui_tradeboard_custom_weights", [0.8, 1.95, 0.75])
        raw = raw if isinstance(raw, list) and len(raw) == 3 else [0.8, 1.95, 0.75]
        with cw1:
            l_w = st.slider("Left Width", min_value=0.2, max_value=3.0, value=float(pd.to_numeric(raw[0], errors="coerce") or 0.8), step=0.05, key="ui_tb_custom_w_left", disabled=layout_locked)
        with cw2:
            c_w = st.slider("Center Width", min_value=0.2, max_value=3.0, value=float(pd.to_numeric(raw[1], errors="coerce") or 1.95), step=0.05, key="ui_tb_custom_w_center", disabled=layout_locked)
        with cw3:
            r_w = st.slider("Right Width", min_value=0.2, max_value=3.0, value=float(pd.to_numeric(raw[2], errors="coerce") or 0.75), step=0.05, key="ui_tb_custom_w_right", disabled=layout_locked)
        st.session_state["ui_tradeboard_custom_weights"] = [float(l_w), float(c_w), float(r_w)]
    if layout_locked:
        st.caption("Panel layout is locked. Disable `Lock Panel Layout` in Preferences to edit.")
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
    sync_left, sync_mid, sync_cur, sync_right = st.columns([1.0, 0.9, 1.25, 2.0])
    with sync_left:
        current_window = str(st.session_state.get("ui_time_window", "1D") or "1D")
        if current_window not in TIME_WINDOW_OPTIONS:
            current_window = "1D"
        st.selectbox("Time Window", options=list(TIME_WINDOW_OPTIONS), index=list(TIME_WINDOW_OPTIONS).index(current_window), key="ui_time_window")
    with sync_mid:
        st.checkbox("Sync Charts", value=bool(st.session_state.get("ui_sync_charts", True)), key="ui_sync_charts")
    with sync_cur:
        st.checkbox("Linked Cursor", value=bool(st.session_state.get("ui_linked_cursor_enabled", True)), key="ui_linked_cursor_enabled")
        cursor_opts: list[str] = []
        if not events_df.empty and "ts_ny" in events_df.columns:
            tsv = pd.to_datetime(events_df["ts_ny"], errors="coerce").dropna().sort_values()
            if not tsv.empty:
                cursor_opts = [pd.Timestamp(t).tz_convert(NY_TZ).strftime("%Y-%m-%d %H:%M:%S") for t in tsv.tail(120)]
        if cursor_opts:
            default_cursor = str(st.session_state.get("ui_linked_cursor_ts", cursor_opts[-1]) or cursor_opts[-1])
            if default_cursor not in cursor_opts:
                default_cursor = cursor_opts[-1]
            st.selectbox(
                "Cursor Time (NY)",
                options=cursor_opts,
                index=cursor_opts.index(default_cursor),
                key="ui_linked_cursor_ts",
                disabled=not bool(st.session_state.get("ui_linked_cursor_enabled", True)),
            )
    with sync_right:
        _render_tradeboard_command_palette(events_df, state_df, symbols)
    pop1, pop2, pop3 = st.columns([1.0, 1.0, 2.0])
    with pop1:
        if st.button("Pop-out Chart", use_container_width=True, key="ui_tb_popout_chart"):
            st.session_state["ui_popout_panel"] = "tradeboard_chart"
            st.rerun()
    with pop2:
        if st.button("Pop-out Alerts", use_container_width=True, key="ui_tb_popout_alerts"):
            st.session_state["ui_popout_panel"] = "alerts"
            st.rerun()

    selected_symbol = st.session_state.ui_selected_symbol
    sync_enabled = bool(st.session_state.get("ui_sync_charts", True))
    window = str(st.session_state.get("ui_time_window", "1D") or "1D")
    scoped_events_df = _events_in_time_window(events_df, window) if sync_enabled else events_df.copy()
    cursor_ts: Any = None
    if bool(st.session_state.get("ui_linked_cursor_enabled", True)):
        cursor_raw = str(st.session_state.get("ui_linked_cursor_ts", "") or "").strip()
        if cursor_raw:
            cursor_ts = pd.to_datetime(cursor_raw, errors="coerce")
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

    left, center, right = st.columns(_tradeboard_split_columns(str(st.session_state.get("ui_tradeboard_split", "Balanced"))))

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
                chart, market_status = _market_candlestick_chart(selected_symbol, window=window, feed=feed, events_df=scoped_events_df, reference_ts=cursor_ts)
                if chart is not None:
                    st.caption(f"Market candles source: Alpaca `{feed}` ({window}) with VWAP overlay.")
                else:
                    st.caption(f"Market candles unavailable ({market_status}). Falling back to execution-derived candles.")
                    chart = _symbol_candlestick_chart(scoped_events_df, selected_symbol, reference_ts=cursor_ts)
                if chart is None and sync_enabled:
                    chart = _symbol_candlestick_chart(events_df, selected_symbol, reference_ts=cursor_ts)
                    if chart is not None:
                        st.caption("No candle-priced events in selected time window; showing full available range for symbol.")
                if chart is None:
                    # Runtime market-order events often omit explicit fill prices. Keep the
                    # panel useful by falling back to execution-activity bars.
                    chart = _symbol_activity_chart(scoped_events_df, selected_symbol, chart_type="Bar", reference_ts=cursor_ts)
                    if chart is None and sync_enabled:
                        chart = _symbol_activity_chart(events_df, selected_symbol, chart_type="Bar", reference_ts=cursor_ts)
                    if chart is not None:
                        st.caption("Candles unavailable (no priced execution events). Showing execution bars instead.")
            else:
                chart = _symbol_activity_chart(
                    scoped_events_df,
                    selected_symbol,
                    chart_type=chart_type,
                    with_markers=(str(chart_type).strip().lower() == "line"),
                    reference_ts=cursor_ts,
                )
        elif mode == "Lifecycle":
            chart = _order_lifecycle_chart(scoped_events_df, symbol_filter=selected_symbol, chart_type=chart_type)
            if chart is None:
                chart = _order_lifecycle_chart(scoped_events_df, symbol_filter="", chart_type=chart_type)
                if chart is not None:
                    st.caption("No lifecycle events for selected symbol in this range; showing all symbols.")
        else:
            chart = _symbol_event_mix_chart(scoped_events_df, selected_symbol)

        if chart is None:
            _render_empty_state(
                f"No chartable data for {selected_symbol}",
                "Chart data is unavailable in the selected scope.",
                "Try widening the time window or disabling sync to inspect full symbol history.",
            )
        else:
            st.altair_chart(chart, use_container_width=True)
            with st.expander("Compare Symbols In Scope", expanded=False):
                default_compare = [selected_symbol] if selected_symbol in symbols else ([symbols[0]] if symbols else [])
                compare_symbols = st.multiselect(
                    "Symbols",
                    options=symbols,
                    default=default_compare,
                    key="ui_tradeboard_compare_symbols",
                    help="Compare event-derived position deltas across symbols.",
                )
                compare_chart = _multi_symbol_compare_chart(scoped_events_df, compare_symbols, limit=4, reference_ts=cursor_ts)
                if compare_chart is None:
                    st.caption("Select symbols with execution history in the current scope.")
                else:
                    st.altair_chart(compare_chart, use_container_width=True)
                    mini_syms = compare_symbols[:4]
                    if mini_syms:
                        cols = st.columns(2)
                        for i, sym in enumerate(mini_syms):
                            with cols[i % 2]:
                                st.caption(f"{sym} mini-chart")
                                mini = _mini_symbol_activity_chart(scoped_events_df, sym)
                                if mini is None:
                                    st.caption("No scoped events.")
                                else:
                                    st.altair_chart(mini, use_container_width=True)
        export_a, export_b, export_c = st.columns([1.0, 1.0, 1.0])
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
        with export_c:
            pack_payload = {
                "generated_at_ny": pd.Timestamp.now(tz=NY_TZ).isoformat(),
                "symbol": selected_symbol,
                "window": window,
                "view_mode": str(st.session_state.get("ui_symbol_view_mode", "Intraday Activity")),
                "chart_type": str(st.session_state.get("ui_symbol_chart_type", "Line")),
                "linked_cursor_ts": str(st.session_state.get("ui_linked_cursor_ts", "") or ""),
                "scope_rows": int(len(scoped_events_df)),
            }
            zbuf = io.BytesIO()
            with zipfile.ZipFile(zbuf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("context.json", json.dumps(pack_payload, indent=2))
                zf.writestr("scope_events.csv", scoped_events_df.to_csv(index=False))
                zf.writestr("symbol_executions.csv", _symbol_activity_df(scoped_events_df, selected_symbol).to_csv(index=False))
                if chart is not None:
                    try:
                        zf.writestr("chart_spec.json", chart.to_json())
                    except Exception:
                        pass
                    png = _chart_png_bytes(chart)
                    if png:
                        zf.writestr("chart.png", png)
            st.download_button(
                "Export Chart Pack (ZIP)",
                data=zbuf.getvalue(),
                file_name=f"tradeboard_pack_{selected_symbol}_{window.lower()}.zip",
                mime="application/zip",
                use_container_width=True,
                key=f"ui_tradeboard_export_pack_{selected_symbol}_{window}",
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
            tbl = _table_customize(
                sym_table[cols].copy(),
                key="tradeboard_recent_exec",
                default_sort_col="ts",
                default_desc=True,
            )
            st.dataframe(
                _paged_view(tbl, key="tradeboard_recent_exec_page", default_page_size=50),
                use_container_width=True,
                hide_index=True,
                height=220,
            )
        else:
            _render_empty_state(
                "No execution rows in scope",
                "No execution events were found for this symbol and filter window.",
            )
        with st.expander("Symbol Event Drilldown", expanded=False):
            if sym_table.empty:
                st.caption("No symbol rows to inspect.")
            else:
                row_opts = [
                    f"{i+1}. {str(r.get('ts',''))} | {str(r.get('event_type',''))} | {str(r.get('side',''))} | qty={str(r.get('qty',''))}"
                    for i, (_, r) in enumerate(sym_table.head(200).iterrows())
                ]
                pick = st.selectbox("Pick Event", options=row_opts, index=0, key="ui_tradeboard_drill_pick")
                idx = max(0, min(len(row_opts) - 1, int(str(pick).split(".", 1)[0]) - 1))
                row = sym_table.iloc[idx]
                st.write(
                    {
                        "ts": str(row.get("ts", "")),
                        "event_type": str(row.get("event_type", "")),
                        "symbol": str(selected_symbol),
                        "side": str(row.get("side", "")),
                        "qty": _safe_float(row.get("qty")),
                        "cum_position": _safe_float(row.get("cum_position")),
                        "order_type": str(row.get("order_type", "")),
                    }
                )
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div class='terminal-shell'>", unsafe_allow_html=True)
        st.markdown("<div class='terminal-title'>Linked Panels: Orders Timeline + Position Lens</div>", unsafe_allow_html=True)
        lp1, lp2 = st.columns([1.2, 1.0])
        with lp1:
            lifecycle_chart = _order_lifecycle_chart(scoped_events_df, symbol_filter=selected_symbol, chart_type="Line")
            if lifecycle_chart is None:
                _render_empty_state(
                    "Lifecycle timeline unavailable",
                    "Order lifecycle points are missing for the selected symbol in current scope.",
                )
            else:
                st.altair_chart(lifecycle_chart, use_container_width=True)
            linked_orders = _orders_table(scoped_events_df)
            if not linked_orders.empty:
                linked_orders = linked_orders[linked_orders["symbol"].astype(str).str.upper() == selected_symbol].copy()
            if linked_orders.empty:
                st.caption("No linked order rows for this symbol.")
            else:
                cols = [c for c in ["ts", "event_type", "side", "qty", "order_type", "variant", "order_status"] if c in linked_orders.columns]
                st.dataframe(linked_orders[cols].sort_values("ts", ascending=False).head(25), use_container_width=True, hide_index=True, height=220)
        with lp2:
            linked_pos = _estimated_positions_table(scoped_events_df, state_df)
            if not linked_pos.empty:
                linked_pos = linked_pos[linked_pos["symbol"].astype(str).str.upper() == selected_symbol].copy()
            if linked_pos.empty:
                _render_empty_state(
                    "Position lens unavailable",
                    "No event-derived position rows for selected symbol in current scope.",
                )
            else:
                st.dataframe(linked_pos, use_container_width=True, hide_index=True, height=220)
                net_qty = float(pd.to_numeric(linked_pos["net_qty_est"], errors="coerce").fillna(0.0).sum())
                st.metric("Linked Net Qty (est)", f"{net_qty:,.4f}")
                target_col = "target_wt_%" if "target_wt_%" in linked_pos.columns else (
                    "target_weight_pct" if "target_weight_pct" in linked_pos.columns else ""
                )
                target_pct = float(pd.to_numeric(linked_pos[target_col], errors="coerce").fillna(0.0).sum()) if target_col else 0.0
                st.metric("Linked Target Wt %", f"{target_pct:,.2f}%")
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
            _render_empty_state("No symbol summary", "Summary KPIs are unavailable for the selected symbol.")

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
            _render_empty_state("No liquidity ladder", "No bucketed liquidity/activity bars are available in scope.")
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
        _render_empty_state("No portfolio positions", "No event-derived position table can be formed from current filtered events.")
    else:
        pos_tbl = _table_customize(pos_df.copy(), key="tradeboard_positions", default_sort_col="symbol", default_desc=False)
        st.dataframe(
            _paged_view(pos_tbl, key="tradeboard_positions_page", default_page_size=100),
            use_container_width=True,
            hide_index=True,
            height=240,
        )
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
        _render_skeleton(lines=5)
        _render_empty_state(
            "No execution journal yet",
            "No order/exit events were found in current runtime scope.",
            "Run paper/live loop or load a populated DB to inspect lifecycle and slippage.",
        )
        st.markdown("</div>", unsafe_allow_html=True)
        return
    orders_enriched = _orders_blotter_enriched(orders)
    _render_execution_kpi_strip(orders_enriched)
    layout_locked = bool(st.session_state.get("ui_panel_layout_locked", False))
    st.selectbox(
        "Execution Panel Layout",
        options=["Balanced", "Blotter Focus", "Analytics Focus", "Custom"],
        index=["Balanced", "Blotter Focus", "Analytics Focus", "Custom"].index(
            str(st.session_state.get("ui_execution_split", "Balanced"))
            if str(st.session_state.get("ui_execution_split", "Balanced")) in {"Balanced", "Blotter Focus", "Analytics Focus", "Custom"}
            else "Balanced"
        ),
        key="ui_execution_split",
        disabled=layout_locked,
    )
    if str(st.session_state.get("ui_execution_split", "Balanced")) == "Custom":
        ew1, ew2 = st.columns(2)
        raw = st.session_state.get("ui_execution_custom_weights", [1.15, 1.0])
        raw = raw if isinstance(raw, list) and len(raw) == 2 else [1.15, 1.0]
        with ew1:
            l_w = st.slider("Left Width", min_value=0.2, max_value=3.0, value=float(pd.to_numeric(raw[0], errors="coerce") or 1.15), step=0.05, key="ui_ex_custom_w_left", disabled=layout_locked)
        with ew2:
            r_w = st.slider("Right Width", min_value=0.2, max_value=3.0, value=float(pd.to_numeric(raw[1], errors="coerce") or 1.0), step=0.05, key="ui_ex_custom_w_right", disabled=layout_locked)
        st.session_state["ui_execution_custom_weights"] = [float(l_w), float(r_w)]
    if layout_locked:
        st.caption("Panel layout is locked. Disable `Lock Panel Layout` in Preferences to edit.")
    p1, p2 = st.columns([1.0, 3.0])
    with p1:
        if st.button("Pop-out Blotter", use_container_width=True, key="ui_exec_popout_blotter"):
            st.session_state["ui_popout_panel"] = "execution_blotter"
            st.rerun()

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

    left, right = st.columns(_execution_split_columns(str(st.session_state.get("ui_execution_split", "Balanced"))))
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
            blotter = _table_customize(
                filtered[show_cols].copy(),
                key="exec_blotter_adv",
                default_sort_col="ts",
                default_desc=True,
            )
            st.dataframe(
                _paged_view(blotter, key="exec_blotter", default_page_size=100),
                use_container_width=True,
                hide_index=True,
            )
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
    st.subheader("Advanced Lifecycle Lanes (Status + Retry/Cancel Markers)")
    adv_lanes = _lifecycle_status_lane_chart(events_df, symbol_filter=tl_filter)
    if adv_lanes is None:
        st.info("Advanced lifecycle lane view unavailable.")
    else:
        st.altair_chart(adv_lanes, use_container_width=True)
    timeline_tbl = _execution_timeline_frame(orders, symbol_filter=("" if tl_symbol == "All" else tl_symbol))
    if not timeline_tbl.empty:
        cols = ["ts_parsed", "symbol_norm", "event_type", "side", "order_type", "qty", "latency_sec", "broker_order_id", "client_order_id"]
        for c in cols:
            if c not in timeline_tbl.columns:
                timeline_tbl[c] = None
        timeline_df = _table_customize(
            timeline_tbl[cols].copy(),
            key="exec_timeline_adv",
            default_sort_col="ts_parsed",
            default_desc=True,
        )
        st.dataframe(
            _paged_view(timeline_df, key="exec_timeline_tbl", default_page_size=50),
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
        lt_tbl = _table_customize(lt.copy(), key="exec_lifecycle_adv", default_sort_col="step", default_desc=True)
        st.dataframe(
            _paged_view(lt_tbl, key="exec_lifecycle_tbl", default_page_size=50),
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
                # Full lifecycle context around the selected trade.
                broker_oid = str(row.get("broker_order_id") or "").strip()
                client_oid = str(row.get("client_order_id") or "").strip()
                ts_sel_parsed = pd.to_datetime(ts_sel, errors="coerce")
                context = order_rows.copy()
                if not context.empty:
                    context["ts_parsed"] = pd.to_datetime(context[ts_col], errors="coerce")
                    if broker_oid or client_oid:
                        oid_hit = pd.Series(False, index=context.index)
                        if broker_oid and ("broker_order_id" in context.columns):
                            oid_hit = oid_hit | (context["broker_order_id"].astype(str) == broker_oid)
                        if client_oid and ("client_order_id" in context.columns):
                            oid_hit = oid_hit | (context["client_order_id"].astype(str) == client_oid)
                        context = context[oid_hit].copy()
                    else:
                        # Fallback: same symbol in a bounded time window around selected event.
                        if (not pd.isna(ts_sel_parsed)) and symbol:
                            lo = ts_sel_parsed - pd.Timedelta(hours=3)
                            hi = ts_sel_parsed + pd.Timedelta(hours=3)
                            context = context[
                                (context["symbol"].astype(str).str.upper() == symbol)
                                & (context["ts_parsed"] >= lo)
                                & (context["ts_parsed"] <= hi)
                            ].copy()
                st.caption("Related Lifecycle Events")
                if context.empty:
                    st.caption("No related lifecycle rows found for this event.")
                else:
                    rel_cols = [
                        c
                        for c in [
                            "id",
                            "ts_parsed",
                            "event_type",
                            "symbol",
                            "side",
                            "qty",
                            "order_type",
                            "order_status",
                            "broker_order_id",
                            "client_order_id",
                            "variant",
                        ]
                        if c in context.columns
                    ]
                    rel = context[rel_cols].sort_values("ts_parsed", ascending=True).reset_index(drop=True)
                    st.dataframe(rel, use_container_width=True, hide_index=True, height=220)
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

    st.subheader("Symbol x Signal x Regime Heatmap")
    heat = _symbol_signal_regime_heatmap(events_df)
    if heat is None:
        st.info("Heatmap unavailable. Needs symbol/event/variant data in scope.")
    else:
        st.altair_chart(heat, use_container_width=True)

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
    raw_notice_count = int(len(notices))
    if notices.empty:
        st.success("No notifications available.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    if "symbol" not in notices.columns:
        notices["symbol"] = ""
    notices["symbol"] = notices["symbol"].astype(str).str.upper().replace({"NAN": "", "NONE": ""})
    dd1, dd2, dd3 = st.columns([1.25, 1.0, 1.25])
    with dd1:
        st.checkbox(
            "Group by Symbol + Time Bucket",
            value=bool(st.session_state.get("notice_group_symbol_bucket", True)),
            key="notice_group_symbol_bucket",
            help="Deduplicates repeated alerts into grouped alerts per symbol/time-bucket.",
        )
    with dd2:
        st.selectbox(
            "Bucket",
            options=[5, 15, 30, 60],
            index=[5, 15, 30, 60].index(int(pd.to_numeric(st.session_state.get("notice_bucket_minutes", 30), errors="coerce") or 30))
            if int(pd.to_numeric(st.session_state.get("notice_bucket_minutes", 30), errors="coerce") or 30) in {5, 15, 30, 60}
            else 2,
            key="notice_bucket_minutes",
            disabled=not bool(st.session_state.get("notice_group_symbol_bucket", True)),
        )
    with dd3:
        st.caption("Grouping is UI-only and does not alter runtime events.")
    if bool(st.session_state.get("notice_group_symbol_bucket", True)):
        bucket_minutes = int(pd.to_numeric(st.session_state.get("notice_bucket_minutes", 30), errors="coerce") or 30)
        grouped_input = notices.copy()
        grouped_input["ts"] = pd.to_datetime(grouped_input["ts"], errors="coerce")
        grouped_input = grouped_input.dropna(subset=["ts"]).copy()
        grouped_input["bucket_ts"] = grouped_input["ts"].dt.floor(f"{bucket_minutes}min")
        grouped_input["symbol_norm"] = grouped_input["symbol"].astype(str).str.upper().replace({"NAN": "-", "NONE": "-", "": "-"})
        grouped_input["title_key"] = grouped_input["title"].astype(str).str.lower().str.strip()
        grouped_counts = (
            grouped_input.groupby(["severity", "source", "symbol_norm", "title_key", "bucket_ts"], as_index=False)
            .agg(
                count=("title_key", "count"),
                ts=("ts", "max"),
                title=("title", "first"),
                detail=("detail", "first"),
                symbol=("symbol_norm", "first"),
            )
            .sort_values("ts", ascending=False)
        )
        if not grouped_counts.empty:
            notices = grouped_counts[["ts", "severity", "title", "detail", "source", "symbol", "count"]].copy()
            notices["count"] = pd.to_numeric(notices["count"], errors="coerce").fillna(1).astype(int)
            notices["title"] = notices.apply(
                lambda r: f"{str(r['title'])} x{int(r['count'])}" if int(r["count"]) > 1 else str(r["title"]),
                axis=1,
            )
            notices = notices.drop(columns=["count"]).reset_index(drop=True)
            st.caption(f"Grouped notices: {len(notices):,} (raw {raw_notice_count:,}, bucket {bucket_minutes}m)")

    actions = st.session_state.get("notice_actions", {})
    if not isinstance(actions, dict):
        actions = {}
    now = pd.Timestamp.now(tz=NY_TZ)

    def _alert_key(row: pd.Series) -> str:
        base = f"{row.get('ts')}|{row.get('severity')}|{row.get('title')}|{row.get('source')}|{row.get('symbol')}"
        return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]

    notices["alert_key"] = notices.apply(_alert_key, axis=1)
    status_col: list[str] = []
    snooze_col: list[str] = []
    muted_raw = st.session_state.get("notice_muted_until", {})
    muted_map = muted_raw if isinstance(muted_raw, dict) else {}
    for _, row in notices.iterrows():
        key = str(row["alert_key"])
        rec = actions.get(key, {})
        status = str(rec.get("status", "open"))
        snooze_until = rec.get("snooze_until")
        mute_until = muted_map.get(key)
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
        if mute_until:
            try:
                mute_ts = pd.Timestamp(mute_until)
                if mute_ts.tzinfo is None:
                    mute_ts = mute_ts.tz_localize(NY_TZ)
                if now < mute_ts:
                    status = "muted"
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
    unread_n = int(active_notices["status"].isin(["open", "escalated"]).sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("High", str(high_n))
    c2.metric("Medium", str(med_n))
    c3.metric("Info", str(info_n))
    c4.metric("Unread", str(unread_n))
    t1, t2, t3 = st.columns([1.3, 1.2, 1.5])
    with t1:
        inbox_tab = st.radio(
            "Inbox",
            options=["All", "Open", "Escalated", "Snoozed", "Muted", "Acknowledged"],
            horizontal=True,
            key="notice_inbox_tab",
        )
    with t2:
        st.caption(f"Muted active: {int((notices['status'] == 'muted').sum())}")
    with t3:
        if st.button("Clear Expired Mutes", use_container_width=True, key="notice_clear_expired_mutes"):
            next_m = {}
            for k, v in muted_map.items():
                ts = pd.to_datetime(v, errors="coerce")
                if pd.isna(ts):
                    continue
                if ts.tzinfo is None:
                    ts = ts.tz_localize(NY_TZ)
                if now < ts:
                    next_m[str(k)] = ts.isoformat()
            st.session_state["notice_muted_until"] = next_m
            st.rerun()

    with st.expander("Grouped Notification Summary", expanded=False):
        grouped = (
            notices.groupby(["severity", "status", "source", "symbol"], as_index=False)
            .size()
            .rename(columns={"size": "count"})
            .sort_values(["severity", "status", "count"], ascending=[True, True, False])
        )
        st.dataframe(grouped, use_container_width=True, hide_index=True)
        dedup = (
            notices.groupby(["severity", "source", "symbol", "title"], as_index=False)
            .agg(count=("alert_key", "count"), latest_ts=("ts", "max"))
            .sort_values(["count", "latest_ts"], ascending=[False, False])
        )
        if not dedup.empty:
            st.caption("Dedupe view (same severity+source+title collapsed):")
            st.dataframe(dedup.head(100), use_container_width=True, hide_index=True)

    left, right = st.columns([1.2, 0.9])
    with left:
        preset = st.selectbox(
            "Severity Preset",
            options=["All", "Critical Focus", "Operations", "Info Review"],
            index=0,
            key="notice_severity_preset",
        )
        preset_map = {
            "All": ["high", "medium", "info"],
            "Critical Focus": ["high"],
            "Operations": ["high", "medium"],
            "Info Review": ["info"],
        }
        selected = st.multiselect(
            "Severity Filter",
            options=["high", "medium", "info"],
            default=preset_map.get(preset, ["high", "medium", "info"]),
            key="notice_severity_filter",
        )
        status_filter = st.multiselect(
            "Status Filter",
            options=["open", "snoozed", "muted", "acknowledged", "escalated"],
            default=["open", "snoozed", "muted", "escalated"],
            key="notice_status_filter",
        )
        if inbox_tab != "All":
            tab_map = {
                "Open": ["open"],
                "Escalated": ["escalated"],
                "Snoozed": ["snoozed"],
                "Muted": ["muted"],
                "Acknowledged": ["acknowledged"],
            }
            status_filter = tab_map.get(inbox_tab, status_filter)
        source_opts = sorted([str(x) for x in notices["source"].dropna().astype(str).unique().tolist() if str(x).strip()])
        source_filter = st.multiselect(
            "Source Filter",
            options=source_opts,
            default=source_opts,
            key="notice_source_filter",
        )
        keyword = st.text_input(
            "Title/Detail Contains",
            value=str(st.session_state.get("notice_keyword_filter", "") or ""),
            key="notice_keyword_filter",
            placeholder="search alert title/detail...",
        )
        view = active_notices if selected else notices.copy()
        if selected:
            view = view[view["severity"].isin(selected)]
        if status_filter:
            view = view[view["status"].isin(status_filter)]
        if source_filter:
            view = view[view["source"].astype(str).isin(source_filter)]
        needle = str(keyword or "").strip().lower()
        if needle:
            hit = (
                view["title"].astype(str).str.lower().str.contains(needle, na=False)
                | view["detail"].astype(str).str.lower().str.contains(needle, na=False)
            )
            view = view[hit].copy()
        cols = ["ts", "severity", "status", "symbol", "title", "detail", "source", "snooze_until", "alert_key"]
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
                if st.button("Mute 30m", use_container_width=True, key="notice_mute_btn"):
                    mute_map_raw = st.session_state.get("notice_muted_until", {})
                    mute_map = mute_map_raw if isinstance(mute_map_raw, dict) else {}
                    mute_map[selected_key] = (now + pd.Timedelta(minutes=30)).isoformat()
                    st.session_state["notice_muted_until"] = mute_map
                    _append_ui_audit("alert_mute", {"alert_key": selected_key, "minutes": 30})
                    st.info("Alert muted for 30 minutes.")
                    st.rerun()
                if st.button("Unmute", use_container_width=True, key="notice_unmute_btn"):
                    mute_map_raw = st.session_state.get("notice_muted_until", {})
                    mute_map = mute_map_raw if isinstance(mute_map_raw, dict) else {}
                    mute_map.pop(selected_key, None)
                    st.session_state["notice_muted_until"] = mute_map
                    _append_ui_audit("alert_unmute", {"alert_key": selected_key})
                    st.info("Alert unmuted.")
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
            audit_tbl = _table_customize(audit_view.copy(), key="audit_explorer_adv", default_sort_col="ts", default_desc=True)
            st.dataframe(
                _paged_view(audit_tbl, key="audit_explorer", default_page_size=100),
                use_container_width=True,
                hide_index=True,
            )
    with right:
        st.subheader("Cycle Metrics Ledger")
        cmt = _cycle_metrics_table(events_df)
        if cmt.empty:
            st.info("No cycle metrics entries found.")
        else:
            cmt_tbl = _table_customize(cmt.copy(), key="audit_cycle_metrics_adv", default_sort_col="ts_ny", default_desc=True)
            st.dataframe(_paged_view(cmt_tbl, key="audit_cycle_metrics_page", default_page_size=100), use_container_width=True, hide_index=True)

        st.subheader("Current State Snapshot")
        if state_df.empty:
            st.info("No state keys available.")
        else:
            state_render = state_df[["key", "value", "updated_at_ny"]].rename(columns={"updated_at_ny": "updated_at"})
            state_tbl = _table_customize(state_render.copy(), key="audit_state_adv", default_sort_col="updated_at", default_desc=True)
            st.dataframe(_paged_view(state_tbl, key="audit_state_page", default_page_size=100), use_container_width=True, hide_index=True)

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
                        replay_mode = st.selectbox(
                            "Replay Mode",
                            options=["Event-by-Event", "Minute-by-Minute"],
                            index=0,
                            key="audit_replay_mode",
                        )
                        pending_speed = st.session_state.get("audit_replay_pending_speed_sec", None)
                        if pending_speed is not None:
                            try:
                                speed_v = int(pending_speed)
                            except Exception:
                                speed_v = None
                            if speed_v in {1, 2, 3, 5, 10}:
                                st.session_state["audit_replay_speed_sec"] = speed_v
                            st.session_state["audit_replay_pending_speed_sec"] = None
                        p1, p2, p3 = st.columns([1.2, 1.0, 2.0])
                        with p1:
                            playing = st.checkbox("Auto Play Replay", value=bool(st.session_state.get("audit_replay_playing", False)), key="audit_replay_playing")
                        with p2:
                            speed = st.selectbox("Tick (sec)", options=[1, 2, 3, 5, 10], index=1, key="audit_replay_speed_sec", disabled=not playing)
                            sp1, sp2, sp3 = st.columns(3)
                            with sp1:
                                if st.button("1x", use_container_width=True, key="audit_speed_1x"):
                                    st.session_state["audit_replay_pending_speed_sec"] = 1
                                    st.rerun()
                            with sp2:
                                if st.button("2x", use_container_width=True, key="audit_speed_2x"):
                                    st.session_state["audit_replay_pending_speed_sec"] = 2
                                    st.rerun()
                            with sp3:
                                if st.button("5x", use_container_width=True, key="audit_speed_5x"):
                                    st.session_state["audit_replay_pending_speed_sec"] = 5
                                    st.rerun()
                        with p3:
                            cprev, cnext = st.columns(2)
                            with cprev:
                                if st.button("Step Back", use_container_width=True, key="audit_step_back"):
                                    if replay_mode == "Minute-by-Minute":
                                        cur_idx = int(st.session_state.get("audit_replay_minute_idx", len(day_df)) or len(day_df))
                                        st.session_state["audit_replay_pending_minute_idx"] = max(1, cur_idx - 1)
                                    else:
                                        cur_idx = int(st.session_state.get("audit_replay_idx", len(day_df)) or len(day_df))
                                        st.session_state["audit_replay_pending_idx"] = max(1, cur_idx - 1)
                                    st.rerun()
                            with cnext:
                                if st.button("Step Forward", use_container_width=True, key="audit_step_fwd"):
                                    if replay_mode == "Minute-by-Minute":
                                        cur_idx = int(st.session_state.get("audit_replay_minute_idx", 1) or 1)
                                        st.session_state["audit_replay_pending_minute_idx"] = min(len(day_df), cur_idx + 1)
                                    else:
                                        cur_idx = int(st.session_state.get("audit_replay_idx", 1) or 1)
                                        st.session_state["audit_replay_pending_idx"] = min(len(day_df), cur_idx + 1)
                                    st.rerun()
                        if replay_mode == "Minute-by-Minute":
                            minute_df = day_df.copy()
                            minute_df["minute_ny"] = pd.to_datetime(minute_df["ts_ny"], errors="coerce").dt.floor("min")
                            minute_keys = [m for m in minute_df["minute_ny"].dropna().unique().tolist()]
                            minute_keys = sorted(minute_keys)
                            if not minute_keys:
                                st.info("Minute replay unavailable for selected day.")
                                idx = 1
                                current = day_df.iloc[0]
                            else:
                                if "audit_replay_minute_idx" not in st.session_state:
                                    st.session_state["audit_replay_minute_idx"] = len(minute_keys)
                                pending_m_idx = st.session_state.get("audit_replay_pending_minute_idx", None)
                                if pending_m_idx is not None:
                                    try:
                                        st.session_state["audit_replay_minute_idx"] = int(pending_m_idx)
                                    except Exception:
                                        pass
                                    st.session_state["audit_replay_pending_minute_idx"] = None
                                st.session_state["audit_replay_minute_idx"] = max(
                                    1,
                                    min(len(minute_keys), int(st.session_state.get("audit_replay_minute_idx", len(minute_keys)) or len(minute_keys))),
                                )
                                if playing and len(minute_keys) > 1:
                                    cur_m_idx = int(st.session_state.get("audit_replay_minute_idx", 1) or 1)
                                    next_m_idx = min(len(minute_keys), cur_m_idx + 1)
                                    st.session_state["audit_replay_minute_idx"] = next_m_idx
                                    if next_m_idx < len(minute_keys):
                                        _auto_refresh_pulse(enabled=True, interval_seconds=int(speed))
                                if len(minute_keys) <= 1:
                                    m_idx = 1
                                    st.caption("Replay Minute Index: 1/1")
                                else:
                                    m_idx = st.slider(
                                        "Replay Minute Index",
                                        min_value=1,
                                        max_value=len(minute_keys),
                                        value=int(st.session_state.get("audit_replay_minute_idx", len(minute_keys)) or len(minute_keys)),
                                        step=1,
                                        key="audit_replay_minute_idx",
                                    )
                                target_minute = pd.Timestamp(minute_keys[int(m_idx) - 1])
                                slice_df = minute_df[minute_df["minute_ny"] <= target_minute].copy()
                                idx = max(1, len(slice_df))
                                current = slice_df.iloc[-1]
                                day_df = slice_df.reset_index(drop=True)
                                st.caption(
                                    f"Replay minute {m_idx}/{len(minute_keys)} | up to {target_minute} | rows={len(day_df)}"
                                )
                        else:
                            if "audit_replay_idx" not in st.session_state:
                                st.session_state["audit_replay_idx"] = len(day_df)
                            pending_idx = st.session_state.get("audit_replay_pending_idx", None)
                            if pending_idx is not None:
                                try:
                                    st.session_state["audit_replay_idx"] = int(pending_idx)
                                except Exception:
                                    pass
                                st.session_state["audit_replay_pending_idx"] = None
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
                        bm_store_raw = st.session_state.get("audit_replay_bookmarks", {})
                        bm_store = bm_store_raw if isinstance(bm_store_raw, dict) else {}
                        day_marks_raw = bm_store.get(day_sel, [])
                        day_marks = day_marks_raw if isinstance(day_marks_raw, list) else []
                        b1, b2, b3, b4 = st.columns([1.0, 1.4, 1.0, 1.0])
                        with b1:
                            if st.button("Bookmark This Event", use_container_width=True, key="audit_replay_bookmark_save"):
                                mark = {
                                    "idx": int(idx),
                                    "ts_ny": str(current.get("ts_ny")),
                                    "event_type": str(current.get("event_type", "")),
                                    "symbol": str(current.get("symbol", "")),
                                }
                                exists = any(int(m.get("idx", -1)) == int(idx) for m in day_marks if isinstance(m, dict))
                                if not exists:
                                    day_marks.append(mark)
                                day_marks = sorted(
                                    [m for m in day_marks if isinstance(m, dict)],
                                    key=lambda m: int(pd.to_numeric(m.get("idx", 0), errors="coerce") or 0),
                                )[:200]
                                bm_store[day_sel] = day_marks
                                st.session_state["audit_replay_bookmarks"] = bm_store
                                st.success("Replay bookmark saved.")
                        with b2:
                            mark_opts = ["(none)"] + [
                                f"#{int(pd.to_numeric(m.get('idx', 0), errors='coerce') or 0)} | {str(m.get('ts_ny', ''))} | {str(m.get('event_type', ''))}"
                                for m in day_marks
                            ]
                            chosen_mark = st.selectbox("Bookmarks", options=mark_opts, index=0, key="audit_replay_bookmark_pick")
                            if st.button("Jump To Bookmark", use_container_width=True, key="audit_replay_bookmark_jump"):
                                if chosen_mark != "(none)":
                                    pick_idx = mark_opts.index(chosen_mark) - 1
                                    if 0 <= pick_idx < len(day_marks):
                                        target_idx = int(pd.to_numeric(day_marks[pick_idx].get("idx", 1), errors="coerce") or 1)
                                        st.session_state["audit_replay_pending_idx"] = max(1, min(len(day_df), target_idx))
                                        st.rerun()
                        with b3:
                            if st.button("Prev Bookmark", use_container_width=True, key="audit_replay_bookmark_prev"):
                                mark_values = sorted(
                                    [int(pd.to_numeric(m.get("idx", 0), errors="coerce") or 0) for m in day_marks if isinstance(m, dict)],
                                )
                                cur_idx = int(idx)
                                prev_marks = [m for m in mark_values if m < cur_idx]
                                if prev_marks:
                                    st.session_state["audit_replay_pending_idx"] = max(1, prev_marks[-1])
                                    st.rerun()
                        with b4:
                            if st.button("Clear Day Bookmarks", use_container_width=True, key="audit_replay_bookmark_clear_day"):
                                bm_store.pop(day_sel, None)
                                st.session_state["audit_replay_bookmarks"] = bm_store
                                st.info("Cleared replay bookmarks for selected day.")
                            if st.button("Next Bookmark", use_container_width=True, key="audit_replay_bookmark_next"):
                                mark_values = sorted(
                                    [int(pd.to_numeric(m.get("idx", 0), errors="coerce") or 0) for m in day_marks if isinstance(m, dict)],
                                )
                                cur_idx = int(idx)
                                next_marks = [m for m in mark_values if m > cur_idx]
                                if next_marks:
                                    st.session_state["audit_replay_pending_idx"] = min(len(day_df), next_marks[0])
                                    st.rerun()
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

    d1, d2 = st.columns(2)
    with d1:
        if "side" in orders.columns:
            by_side = orders.copy()
            by_side["side"] = by_side["side"].astype(str).str.lower().replace({"": "unknown"})
            by_side = by_side.groupby("side", as_index=False).size().rename(columns={"size": "count"})
            st.altair_chart(
                alt.Chart(by_side)
                .mark_arc(innerRadius=48, outerRadius=90)
                .encode(
                    theta=alt.Theta("count:Q"),
                    color=alt.Color("side:N", scale=alt.Scale(range=["#00c805", "#ff5d7a", "#2ea7ff", "#f3b13c"])),
                    tooltip=["side:N", "count:Q"],
                )
                .properties(height=240),
                use_container_width=True,
            )
        else:
            st.caption("No side field available in current order rows.")
    with d2:
        if "order_type" in orders.columns:
            by_ot = orders.copy()
            by_ot["order_type"] = by_ot["order_type"].astype(str).str.lower().replace({"": "unknown"})
            by_ot = by_ot.groupby("order_type", as_index=False).size().rename(columns={"size": "count"})
            st.altair_chart(
                alt.Chart(by_ot)
                .mark_bar(opacity=0.9, cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color="#2ea7ff")
                .encode(
                    x=alt.X("order_type:N", sort="-y"),
                    y=alt.Y("count:Q", title="Count"),
                    tooltip=["order_type:N", "count:Q"],
                )
                .properties(height=240),
                use_container_width=True,
            )
        else:
            st.caption("No order_type field available in current order rows.")

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
    with st.expander("Runtime Report Builder", expanded=False):
        st.caption("Build ad-hoc runtime reports with custom columns, filters, ranges, and export templates.")
        if events_df.empty:
            st.info("No runtime events available for report builder.")
        else:
            all_cols = [str(c) for c in events_df.columns.tolist()]
            default_cols = [c for c in ["id", "ts_ny", "event_type", "symbol", "side", "qty", "variant", "order_type"] if c in all_cols]
            cols = st.multiselect(
                "Columns",
                options=all_cols,
                default=default_cols if default_cols else all_cols[: min(10, len(all_cols))],
                key="ui_report_builder_cols",
            )
            r1, r2, r3 = st.columns(3)
            with r1:
                win = st.selectbox("Time Range", options=list(TIME_WINDOW_OPTIONS), index=list(TIME_WINDOW_OPTIONS).index("1M"), key="ui_report_builder_window")
            with r2:
                q = st.text_input("Contains", value="", key="ui_report_builder_query", placeholder="payload/event/symbol text")
            with r3:
                tmpl = st.selectbox("Template", options=["Execution Audit", "Variant Journal", "Signal Feed", "Custom"], index=0, key="ui_report_builder_template")
            rep = _events_in_time_window(events_df, str(win))
            if str(q or "").strip():
                needle = str(q).strip().lower()
                mask = pd.Series(False, index=rep.index)
                for c in ["payload_text", "event_type", "symbol", "variant", "order_type", "side"]:
                    if c in rep.columns:
                        mask = mask | rep[c].astype(str).str.lower().str.contains(needle, na=False)
                rep = rep[mask]
            if tmpl == "Execution Audit":
                pref = [c for c in ["id", "ts_ny", "event_type", "symbol", "side", "qty", "order_type", "order_status", "broker_order_id", "client_order_id"] if c in all_cols]
                cols = pref or cols
            elif tmpl == "Variant Journal":
                pref = [c for c in ["id", "ts_ny", "event_type", "variant", "variant_reason", "threshold_pct", "profile"] if c in all_cols]
                cols = pref or cols
            elif tmpl == "Signal Feed":
                pref = [c for c in ["id", "ts_ny", "event_type", "symbol", "side", "qty", "payload_text"] if c in all_cols]
                cols = pref or cols
            cols = [c for c in cols if c in rep.columns]
            if not cols:
                st.warning("Select at least one available column.")
            else:
                out = rep[cols].copy()
                st.dataframe(_paged_view(out, key="ui_report_builder_tbl", default_page_size=100), use_container_width=True, hide_index=True)
                e1, e2 = st.columns(2)
                with e1:
                    st.download_button(
                        "Export Report CSV",
                        data=out.to_csv(index=False).encode("utf-8"),
                        file_name=f"runtime_report_{tmpl.lower().replace(' ', '_')}_{str(win).lower()}.csv",
                        mime="text/csv",
                        use_container_width=True,
                        key="ui_report_builder_export_csv",
                    )
                with e2:
                    st.download_button(
                        "Export Report JSON",
                        data=json.dumps(out.to_dict(orient="records"), indent=2, default=str).encode("utf-8"),
                        file_name=f"runtime_report_{tmpl.lower().replace(' ', '_')}_{str(win).lower()}.json",
                        mime="application/json",
                        use_container_width=True,
                        key="ui_report_builder_export_json",
                    )
    with st.expander("Quick Load Standard Multi-Window Report", expanded=False):
        st.caption("Load the generated multi-window summary directly from disk for instant review in UI.")
        default_summary_path = (
            "/home/chewy/projects/trading-compose-dev/switch_runtime_v1/reports/"
            "multiwindow_intraday_pl5m_switch_v1_2026-03-20/"
            "summary_aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m_switch_v1_"
            "paper_live_style_optimistic.csv"
        )
        quick_path = st.text_input(
            "Summary CSV Path",
            value=str(st.session_state.get("ui_bt_quick_path", default_summary_path) or default_summary_path),
            key="ui_bt_quick_path",
        )
        cqa, cqb = st.columns([0.9, 1.1])
        with cqa:
            if st.button("Load Summary From Path", use_container_width=True, key="ui_bt_quick_load"):
                st.session_state["ui_bt_quick_loaded_path"] = str(quick_path or "").strip()
        with cqb:
            if st.button("Clear Loaded Summary", use_container_width=True, key="ui_bt_quick_clear"):
                st.session_state["ui_bt_quick_loaded_path"] = ""

        loaded_path = str(st.session_state.get("ui_bt_quick_loaded_path", "") or "").strip()
        if loaded_path:
            p = Path(loaded_path)
            if not p.exists():
                st.warning(f"File not found: {loaded_path}")
            else:
                try:
                    quick_df = pd.read_csv(p)
                    if quick_df.empty:
                        st.info("Loaded summary is empty.")
                    else:
                        order = {
                            "1m": 1,
                            "2m": 2,
                            "3m": 3,
                            "4m": 4,
                            "5m": 5,
                            "6m": 6,
                            "9m": 9,
                            "1y": 12,
                            "2y": 24,
                            "3y": 36,
                            "4y": 48,
                            "5y": 60,
                            "7y": 84,
                            "10y": 120,
                        }
                        if "Window" in quick_df.columns:
                            quick_df["window_order"] = quick_df["Window"].astype(str).str.lower().map(order).fillna(9999)
                            quick_df = quick_df.sort_values("window_order").reset_index(drop=True)
                        show_cols = [
                            c
                            for c in [
                                "Profile",
                                "Window",
                                "Mode",
                                "Start Equity",
                                "CPU Final Equity",
                                "CPU Return %",
                                "CPU PnL",
                                "CPU MaxDD %",
                                "CPU Trades",
                                "GPU Backend",
                                "GPU Final Equity",
                                "GPU Return %",
                                "CPU-GPU Diff (bps)",
                            ]
                            if c in quick_df.columns
                        ]
                        st.dataframe(quick_df[show_cols], use_container_width=True, hide_index=True)
                        score_df = quick_df.copy()
                        if "CPU Return %" in score_df.columns:
                            score_df["CPU Return %"] = pd.to_numeric(score_df["CPU Return %"], errors="coerce")
                        if "CPU MaxDD %" in score_df.columns:
                            score_df["CPU MaxDD %"] = pd.to_numeric(score_df["CPU MaxDD %"], errors="coerce").abs()
                        if ("CPU Return %" in score_df.columns) and ("CPU MaxDD %" in score_df.columns):
                            score_df["Risk-Adjusted Score"] = score_df["CPU Return %"] / score_df["CPU MaxDD %"].clip(lower=0.01)
                        if "Risk-Adjusted Score" in score_df.columns:
                            rank_cols = [c for c in ["Profile", "Window", "Mode", "CPU Return %", "CPU MaxDD %", "Risk-Adjusted Score"] if c in score_df.columns]
                            ranked = score_df[rank_cols].dropna(subset=["Risk-Adjusted Score"]).sort_values(
                                ["Risk-Adjusted Score", "CPU Return %"], ascending=False
                            ).head(10)
                            if not ranked.empty:
                                st.subheader("Leaderboard (Risk-Adjusted)")
                                st.dataframe(ranked, use_container_width=True, hide_index=True)
                                top = ranked.iloc[0]
                                cta1, cta2, cta3 = st.columns(3)
                                cta1.metric("Top Profile", str(top.get("Profile", "-")))
                                cta2.metric("Top Window", str(top.get("Window", "-")))
                                cta3.metric("Top Score", f"{float(pd.to_numeric(top.get('Risk-Adjusted Score'), errors='coerce') or 0.0):.2f}")
                                promote_profile = str(top.get("Profile", "") or "").strip()
                                if promote_profile and st.button(
                                    f"Promote {promote_profile}",
                                    use_container_width=True,
                                    key="ui_bt_promote_profile",
                                ):
                                    st.session_state["ui_palette_status"] = f"Promoted backtest profile: {promote_profile}"
                                    st.session_state["ui_pending_saved_workspace"] = "Backtest Hub"
                                    st.rerun()
                        st.download_button(
                            "Download Loaded Summary CSV",
                            data=quick_df.to_csv(index=False).encode("utf-8"),
                            file_name=p.name,
                            mime="text/csv",
                            use_container_width=True,
                            key="ui_bt_quick_download",
                        )

                        if ("Window" in quick_df.columns) and ("CPU Return %" in quick_df.columns):
                            plot_df = quick_df.copy()
                            plot_df["Window"] = plot_df["Window"].astype(str)
                            plot_df["CPU Return %"] = pd.to_numeric(plot_df["CPU Return %"], errors="coerce")
                            if "GPU Return %" in plot_df.columns:
                                plot_df["GPU Return %"] = pd.to_numeric(plot_df["GPU Return %"], errors="coerce")
                            melt_cols = ["Window", "CPU Return %"] + (["GPU Return %"] if "GPU Return %" in plot_df.columns else [])
                            melt = plot_df[melt_cols].melt(id_vars=["Window"], var_name="Series", value_name="ReturnPct")
                            melt = melt.dropna(subset=["ReturnPct"]).copy()
                            if not melt.empty:
                                chart = (
                                    alt.Chart(melt)
                                    .mark_bar(size=18)
                                    .encode(
                                        x=alt.X("Window:N", sort=list(order.keys()), title="Window"),
                                        y=alt.Y("ReturnPct:Q", title="Return %"),
                                        color=alt.Color("Series:N", title="Series"),
                                        xOffset="Series:N",
                                        tooltip=["Window:N", "Series:N", alt.Tooltip("ReturnPct:Q", format=".2f")],
                                    )
                                    .properties(height=280)
                                    .configure_axis(
                                        labelColor="#afc3e6",
                                        titleColor="#d8e6ff",
                                        gridColor="rgba(159,179,213,0.2)",
                                    )
                                    .configure_view(strokeOpacity=0)
                                )
                                st.altair_chart(chart, use_container_width=True)
                except Exception as exc:
                    st.error(f"Quick-load failed: {exc}")
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
        _render_skeleton(lines=4)
        _render_empty_state(
            "No backtest files uploaded",
            "Upload one or more CSV/JSON outputs to populate comparative analytics.",
            "Tip: use Quick Load for standard multi-window reports, then compare against runtime references.",
        )
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


def _render_workspace_help_drawer(workspace_name: str) -> None:
    help_map: dict[str, list[str]] = {
        "Command Center": [
            "Use this as your landing workspace for quick operational overview.",
            "Apply workspace presets and global filters before deep drilling.",
            "Check freshness confidence and alert counts first.",
        ],
        "Tradeboard": [
            "Select symbol from watchlist and validate event scope/time window.",
            "Use Compare Symbols for side-by-side event-derived position movement.",
            "Use Symbol Event Drilldown before interpreting sudden quantity shifts.",
        ],
        "Multi-Chart": [
            "Use synchronized window/chart mode to compare four symbols side by side.",
            "Start with SOXL/SOXS or TQQQ/SQQQ pairs to inspect directional regime shifts.",
            "Use the same window before comparing signal quality across symbols.",
        ],
        "Execution Journal": [
            "Start with Risk Guard Panel to verify drift/slippage thresholds.",
            "Use blotter presets for faster operational review.",
            "Use lifecycle timeline and trade drawer for order-level forensics.",
        ],
        "Notifications": [
            "Acknowledge, snooze, or escalate from Manage Alert panel.",
            "Use Grouped Summary to reduce alert noise.",
            "Track incident progression in Incident Timeline Mode.",
        ],
        "Audit Trail": [
            "Use replay scrubber + bookmarks for deterministic investigations.",
            "Filter by event types and payload terms.",
            "Export UI audit log for operational review.",
        ],
        "UI Diagnostics": [
            "Inspect render timing history before enabling tighter auto-refresh cadence.",
            "Check stale-widget detector when dashboards look delayed or inconsistent.",
            "Use this workspace for UI performance troubleshooting only.",
        ],
    }
    tips = help_map.get(workspace_name, [])
    if not tips:
        return
    with st.expander(f"{workspace_name} Help", expanded=False):
        for tip in tips:
            st.markdown(f"- {tip}")


def _render_command_center_workspace(events_df: pd.DataFrame, state_df: pd.DataFrame, user_role: str) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown("<div class='section-caption'>Role-tuned command center with customizable summary tiles.</div>", unsafe_allow_html=True)
    hs = _health_snapshot(events_df, state_df)
    notices = _notification_center_table(events_df, state_df)
    curve = _equity_curve_frame(events_df, state_df)
    pnl = _pnl_snapshot(curve, state_df)
    default_tiles = [
        "Freshness",
        "Events 1h",
        "Cycles 24h",
        "Errors 24h",
        "Current Equity",
        "Return %",
        "Max Drawdown %",
        "Open Alerts",
    ]
    selected_tiles = st.session_state.get("ui_home_tiles", [])
    if not isinstance(selected_tiles, list) or not selected_tiles:
        selected_tiles = default_tiles
    selected_tiles = st.multiselect(
        "Visible Tiles",
        options=default_tiles,
        default=[t for t in selected_tiles if t in default_tiles] or default_tiles,
        key="ui_home_tiles",
    )
    metrics: dict[str, str] = {
        "Freshness": "-" if hs.get("freshness_min") is None else f"{float(hs['freshness_min']):.1f}m",
        "Events 1h": f"{int(hs.get('events_1h', 0)):,}",
        "Cycles 24h": f"{int(hs.get('cycles_24h', 0)):,}",
        "Errors 24h": f"{int(hs.get('error_events_24h', 0)):,}",
        "Current Equity": "-" if pnl.get("end_equity") is None else f"${float(pnl['end_equity']):,.2f}",
        "Return %": "-" if pnl.get("return_pct") is None else f"{float(pnl['return_pct']):.2f}%",
        "Max Drawdown %": "-" if pnl.get("max_drawdown_pct") is None else f"{float(pnl['max_drawdown_pct']):.2f}%",
        "Open Alerts": f"{int(len(notices)):,}",
    }
    cols = st.columns(4)
    for i, tile in enumerate(selected_tiles):
        with cols[i % 4]:
            st.metric(tile, metrics.get(tile, "-"))
    st.caption(f"Persona mode: `{st.session_state.get('ui_persona_mode', 'Operator')}` | Role: `{user_role}`")
    st.markdown("</div>", unsafe_allow_html=True)


def _render_ui_changelog_workspace() -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown("<div class='section-caption'>Built-in UI release notes and feature changelog.</div>", unsafe_allow_html=True)
    rows = [
        {"date": "2026-03-23", "area": "Session Strip", "change": "Added market session + eval countdown chips"},
        {"date": "2026-03-23", "area": "Watchlist", "change": "Added min-event filter and multi-mode sorting controls"},
        {"date": "2026-03-23", "area": "Notifications", "change": "Added source filter and title/detail keyword search"},
        {"date": "2026-03-23", "area": "Execution", "change": "Added side donut and order-type distribution charts"},
        {"date": "2026-03-23", "area": "Tradeboard", "change": "Symbol compare + mini charts + event drilldown"},
        {"date": "2026-03-23", "area": "Notifications", "change": "Grouped summary + toast dedupe + alert action polish"},
        {"date": "2026-03-23", "area": "Layout", "change": "Visible workspace editor + custom panel widths + mobile nav"},
        {"date": "2026-03-23", "area": "Command", "change": "Top command favorites/history/search-jump + shortcut help"},
        {"date": "2026-03-23", "area": "Accessibility", "change": "Persona/accessibility presets and reduced-motion mode"},
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _symbol_signal_regime_heatmap(events_df: pd.DataFrame) -> alt.Chart | None:
    if events_df.empty:
        return None
    frame = events_df.copy()
    for col in ["symbol", "event_type", "variant"]:
        if col not in frame.columns:
            frame[col] = None
    frame["symbol"] = frame["symbol"].astype(str).str.upper().replace({"": pd.NA, "NONE": pd.NA, "NAN": pd.NA})
    frame["signal"] = frame["event_type"].astype(str).replace({"": pd.NA, "None": pd.NA, "nan": pd.NA})
    frame["regime"] = frame["variant"].astype(str).replace({"": "unknown", "None": "unknown", "nan": "unknown"})
    frame = frame.dropna(subset=["symbol", "signal"])
    if frame.empty:
        return None
    grouped = (
        frame.groupby(["symbol", "signal", "regime"], as_index=False)
        .size()
        .rename(columns={"size": "count"})
    )
    grouped["symbol_regime"] = grouped["symbol"].astype(str) + " | " + grouped["regime"].astype(str)
    return (
        alt.Chart(grouped)
        .mark_rect()
        .encode(
            x=alt.X("signal:N", title="Signal/Event Type", sort="-y"),
            y=alt.Y("symbol_regime:N", title="Symbol | Regime"),
            color=alt.Color("count:Q", title="Count", scale=alt.Scale(scheme="teals")),
            tooltip=["symbol:N", "signal:N", "regime:N", "count:Q"],
        )
        .properties(height=340)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff")
        .configure_view(strokeOpacity=0)
    )


def _lifecycle_status_lane_chart(events_df: pd.DataFrame, symbol_filter: str = "") -> alt.Chart | None:
    orders = _orders_table(events_df)
    if orders.empty:
        return None
    frame = orders.copy()
    if symbol_filter:
        frame = frame[frame["symbol"].astype(str).str.upper() == str(symbol_filter).upper()].copy()
    if frame.empty:
        return None
    frame["ts_plot"] = pd.to_datetime(frame.get("ts_ny"), errors="coerce")
    if frame["ts_plot"].isna().all():
        frame["ts_plot"] = pd.to_datetime(frame.get("ts"), errors="coerce")
    frame = frame.dropna(subset=["ts_plot"]).copy()
    if frame.empty:
        return None
    frame["status_lane"] = frame["order_status"].astype(str).str.lower().replace(
        {
            "": "unknown",
            "none": "unknown",
            "nan": "unknown",
            "new": "submitted",
            "accepted": "submitted",
            "partially_filled": "partially_filled",
            "filled": "filled",
            "canceled": "canceled",
            "rejected": "rejected",
        }
    )
    frame["shape"] = frame["status_lane"].map(
        lambda s: "triangle-up" if s in {"submitted", "new"} else ("diamond" if s in {"partially_filled"} else ("triangle-down" if s in {"canceled", "rejected"} else "circle"))
    )
    frame["side_norm"] = frame["side"].astype(str).str.lower().map(lambda s: "buy" if s == "buy" else ("sell" if s == "sell" else "other"))
    return (
        alt.Chart(frame)
        .mark_point(filled=True, size=90, opacity=0.9)
        .encode(
            x=alt.X("ts_plot:T", title="Time (NY)"),
            y=alt.Y("status_lane:N", title="Lifecycle Lane"),
            color=alt.Color(
                "side_norm:N",
                title="Side",
                scale=alt.Scale(domain=["buy", "sell", "other"], range=["#00c805", "#ff5d7a", "#aab4be"]),
            ),
            shape=alt.Shape("shape:N", title="Marker"),
            tooltip=[
                "ts_plot:T",
                "symbol:N",
                "event_type:N",
                "side:N",
                "order_type:N",
                "order_status:N",
                alt.Tooltip("qty:Q", format=",.6f"),
            ],
        )
        .properties(height=300)
        .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
        .configure_legend(labelColor="#d8e6ff", titleColor="#d8e6ff")
        .configure_view(strokeOpacity=0)
    )


def _render_multi_chart_workspace(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-caption'>2x2 linked chart desk. All four charts share the same window and chart mode for synchronized analysis.</div>",
        unsafe_allow_html=True,
    )
    symbols = _symbol_universe(events_df, state_df)
    if not symbols:
        _render_empty_state("No symbols available", "No symbols found in runtime scope for multi-chart view.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    base_default = symbols[:4] if len(symbols) >= 4 else (symbols + [symbols[0]] * (4 - len(symbols)))
    defaults = st.session_state.get("ui_multichart_symbols", base_default)
    if not isinstance(defaults, list) or len(defaults) != 4:
        defaults = base_default
    c0, c1, c2 = st.columns([1.1, 1.1, 1.2])
    with c0:
        win = st.selectbox("Linked Window", options=list(TIME_WINDOW_OPTIONS), index=list(TIME_WINDOW_OPTIONS).index(str(st.session_state.get("ui_multichart_window", "1W")) if str(st.session_state.get("ui_multichart_window", "1W")) in TIME_WINDOW_OPTIONS else "1W"), key="ui_multichart_window")
    with c1:
        ctype = st.selectbox("Linked Chart", options=["Line", "Bar", "Candles"], index=["Line", "Bar", "Candles"].index(str(st.session_state.get("ui_multichart_chart_type", "Line")) if str(st.session_state.get("ui_multichart_chart_type", "Line")) in {"Line", "Bar", "Candles"} else "Line"), key="ui_multichart_chart_type")
    with c2:
        st.caption("Tip: use matching leveraged/inverse pairs to compare regime behavior.")
    scoped = _events_in_time_window(events_df, win)
    pick_cols = st.columns(4)
    picks: list[str] = []
    for i in range(4):
        with pick_cols[i]:
            p = st.selectbox(
                f"Symbol {i+1}",
                options=symbols,
                index=symbols.index(defaults[i]) if defaults[i] in symbols else 0,
                key=f"ui_multichart_symbol_{i+1}",
            )
            picks.append(p)
    st.session_state["ui_multichart_symbols"] = picks
    row_a = st.columns(2)
    row_b = st.columns(2)
    grid_cols = [row_a[0], row_a[1], row_b[0], row_b[1]]
    for i, sym in enumerate(picks):
        with grid_cols[i]:
            st.markdown(f"**{sym}**")
            chart: alt.Chart | None
            if str(ctype).lower() == "candles":
                chart = _symbol_candlestick_chart(scoped, sym)
                if chart is None:
                    chart = _symbol_activity_chart(scoped, sym, chart_type="Bar")
            else:
                chart = _symbol_activity_chart(scoped, sym, chart_type=ctype, with_markers=(str(ctype).lower() == "line"))
            if chart is None:
                _render_empty_state("No chart data", f"No chartable rows for {sym} in `{win}`.")
            else:
                st.altair_chart(chart, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_ui_diagnostics_workspace(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-caption'>UI diagnostics for render timing, data volume, and stale-widget signals.</div>",
        unsafe_allow_html=True,
    )
    hs = _health_snapshot(events_df, state_df)
    timings_raw = st.session_state.get("ui_render_timings", [])
    timings = timings_raw if isinstance(timings_raw, list) else []
    tdf = pd.DataFrame(timings)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Events Loaded", f"{int(len(events_df)):,}")
    m2.metric("State Keys", f"{int(len(state_df)):,}")
    m3.metric("Freshness (min)", "-" if hs.get("freshness_min") is None else f"{float(hs['freshness_min']):.2f}")
    avg_render_ms = float(pd.to_numeric(tdf["render_ms"], errors="coerce").dropna().mean()) if (not tdf.empty and "render_ms" in tdf.columns) else None
    m4.metric("Avg Render (ms)", "-" if avg_render_ms is None else f"{avg_render_ms:.1f}")
    stale_rows = [
        {
            "widget": "Runtime stream freshness",
            "age_min": None if hs.get("freshness_min") is None else float(hs.get("freshness_min") or 0.0),
            "threshold_min": 10.0,
            "status": "STALE" if (hs.get("freshness_min") is not None and float(hs.get("freshness_min") or 0.0) > 10.0) else "OK",
        },
        {
            "widget": "Heavy widget cadence",
            "age_min": float(pd.to_numeric(st.session_state.get("ui_heavy_widget_last_age_min", 0.0), errors="coerce") or 0.0),
            "threshold_min": float(pd.to_numeric(st.session_state.get("ui_heavy_widget_cadence_sec", 30), errors="coerce") or 30.0) / 60.0,
            "status": "OK",
        },
        {
            "widget": "Fast widget cadence",
            "age_min": float(pd.to_numeric(st.session_state.get("ui_fast_widget_last_age_min", 0.0), errors="coerce") or 0.0),
            "threshold_min": float(pd.to_numeric(st.session_state.get("ui_fast_widget_cadence_sec", 5), errors="coerce") or 5.0) / 60.0,
            "status": "OK",
        },
    ]
    st.subheader("Stale Widget Detector")
    st.dataframe(pd.DataFrame(stale_rows), use_container_width=True, hide_index=True)
    st.subheader("Render Timing History")
    if tdf.empty:
        st.info("No render timings captured yet.")
    else:
        st.dataframe(tdf.sort_values("ts_ny", ascending=False).head(200), use_container_width=True, hide_index=True)
        if "render_ms" in tdf.columns:
            ch = (
                alt.Chart(tdf.dropna(subset=["render_ms"]))
                .mark_line(point=True, color="#66d7ff")
                .encode(
                    x=alt.X("ts_ny:T", title="Render Time (NY)"),
                    y=alt.Y("render_ms:Q", title="Render ms"),
                    tooltip=["ts_ny:T", alt.Tooltip("render_ms:Q", format=".2f")],
                )
                .properties(height=260)
                .configure_axis(labelColor="#afc3e6", titleColor="#d8e6ff", gridColor="rgba(159,179,213,0.2)")
                .configure_view(strokeOpacity=0)
            )
            st.altair_chart(ch, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_popout_panel(events_df: pd.DataFrame, state_df: pd.DataFrame) -> None:
    pop = str(st.session_state.get("ui_popout_panel", "") or "").strip()
    if not pop:
        return
    st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
    top1, top2 = st.columns([3.0, 1.0])
    with top1:
        st.subheader(f"Pop-out Panel: {pop}")
    with top2:
        if st.button("Close Pop-out", use_container_width=True, key="ui_popout_close"):
            st.session_state["ui_popout_panel"] = ""
            st.rerun()
    if pop == "tradeboard_chart":
        sym = str(st.session_state.get("ui_selected_symbol", "") or "")
        ch = _symbol_activity_chart(events_df, sym, chart_type="Line", with_markers=True) if sym else None
        if ch is None:
            st.info("No chart data available.")
        else:
            st.altair_chart(ch, use_container_width=True)
    elif pop == "execution_blotter":
        orders = _orders_table(events_df)
        if orders.empty:
            st.info("No blotter rows.")
        else:
            st.dataframe(_paged_view(orders, key="popout_exec_blotter", default_page_size=100), use_container_width=True, hide_index=True)
    elif pop == "alerts":
        notices = _notification_center_table(events_df, state_df)
        if notices.empty:
            st.info("No active notices.")
        else:
            st.dataframe(_paged_view(notices, key="popout_alerts", default_page_size=80), use_container_width=True, hide_index=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _workspace_use_global_toggle(name: str, default_value: bool = True) -> bool:
    key = f"ui_ws_use_global_{str(name).lower().replace(' ', '_').replace('&', 'and')}"
    return st.checkbox(f"Use Global Filters ({name})", value=default_value, key=key)


def _render_workspace_by_name(
    workspace_name: str,
    *,
    events_df: pd.DataFrame,
    events_global: pd.DataFrame,
    state_df: pd.DataFrame,
    db_path: str,
    current_user: str,
    user_role: str,
    user_default_db: str,
    strict_isolation: bool,
) -> None:
    _render_workspace_help_drawer(workspace_name)
    fast_sec = int(pd.to_numeric(st.session_state.get("ui_fast_widget_cadence_sec", 5), errors="coerce") or 5)
    heavy_sec = int(pd.to_numeric(st.session_state.get("ui_heavy_widget_cadence_sec", 30), errors="coerce") or 30)

    def _scope(use_global: bool, ws_key: str, fast: bool) -> pd.DataFrame:
        base = events_global if use_global else events_df
        return _apply_widget_cadence(base, key=f"ws_{ws_key}", cadence_sec=(fast_sec if fast else heavy_sec))

    if workspace_name == "Command Center":
        use_global = _workspace_use_global_toggle("Command Center", default_value=True)
        _render_command_center_workspace(events_df=_scope(use_global, "command_center", True), state_df=state_df, user_role=user_role)
    elif workspace_name == "Tradeboard":
        use_global = _workspace_use_global_toggle("Tradeboard", default_value=True)
        _render_terminal_workspace(events_df=_scope(use_global, "tradeboard", True), state_df=state_df)
    elif workspace_name == "Multi-Chart":
        use_global = _workspace_use_global_toggle("Multi-Chart", default_value=True)
        _render_multi_chart_workspace(events_df=_scope(use_global, "multi_chart", False), state_df=state_df)
    elif workspace_name == "Portfolio Pulse":
        use_global = _workspace_use_global_toggle("Portfolio Pulse", default_value=True)
        _render_live_overview_workspace(events_df=_scope(use_global, "portfolio_pulse", True), state_df=state_df)
    elif workspace_name == "Execution Journal":
        use_global = _workspace_use_global_toggle("Execution Journal", default_value=True)
        _render_execution_orders_workspace(events_df=_scope(use_global, "execution_journal", True), state_df=state_df)
    elif workspace_name == "Strategy Lab":
        use_global = _workspace_use_global_toggle("Strategy Lab", default_value=True)
        _render_strategy_analytics_workspace(events_df=_scope(use_global, "strategy_lab", False), state_df=state_df)
    elif workspace_name == "P&L Monitor":
        use_global = _workspace_use_global_toggle("P&L Monitor", default_value=True)
        _render_pnl_monitor_workspace(events_df=_scope(use_global, "pnl_monitor", False), state_df=state_df)
    elif workspace_name == "Notifications":
        use_global = _workspace_use_global_toggle("Notifications", default_value=True)
        _render_notifications_workspace(events_df=_scope(use_global, "notifications", True), state_df=state_df)
    elif workspace_name == "Ops Health":
        use_global = _workspace_use_global_toggle("Ops Health", default_value=True)
        _render_system_health_workspace(events_df=_scope(use_global, "ops_health", False), state_df=state_df, db_path=db_path)
    elif workspace_name == "UI Diagnostics":
        use_global = _workspace_use_global_toggle("UI Diagnostics", default_value=True)
        _render_ui_diagnostics_workspace(events_df=_scope(use_global, "ui_diagnostics", False), state_df=state_df)
    elif workspace_name == "Audit Trail":
        use_global = _workspace_use_global_toggle("Audit Trail", default_value=True)
        _render_audit_workspace(events_df=_scope(use_global, "audit_trail", False), state_df=state_df)
    elif workspace_name == "Operator Guide":
        _render_runbook_tab(
            db_path=db_path,
            current_user=current_user,
            user_default_db=user_default_db,
            strict_isolation=strict_isolation,
        )
    elif workspace_name == "Backtest Hub":
        use_global = _workspace_use_global_toggle("Backtest Hub", default_value=True)
        _render_backtest_import_hub(events_df=_scope(use_global, "backtest_hub", False), state_df=state_df)
    elif workspace_name == "UI Changelog":
        _render_ui_changelog_workspace()


def _main_app() -> None:
    render_start = pd.Timestamp.now(tz=NY_TZ)
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
    _handle_keyboard_shortcut_event(user_role=user_role)
    st.session_state["auth_role_cached"] = user_role
    user_default_db = str(_user_runtime_db_path(current_user))

    # User-scoped preferences.
    all_prefs = _load_prefs()
    user_prefs = all_prefs.get(current_user, {}) if isinstance(all_prefs, dict) else {}
    _load_user_ui_prefs_once(current_user=current_user, user_prefs=user_prefs if isinstance(user_prefs, dict) else {})
    _inject_dynamic_theme_css()
    _inject_compact_mode_css(enabled=bool(st.session_state.get("ui_mobile_compact_mode", False)))
    _inject_keyboard_shortcuts_js(enabled=bool(st.session_state.get("ui_keyboard_shortcuts_enabled", True)))

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
                    {"section": "Command Center", "min_role": "viewer"},
                    {"section": "Tradeboard", "min_role": "viewer"},
                    {"section": "Multi-Chart", "min_role": "viewer"},
                    {"section": "Portfolio Pulse", "min_role": "viewer"},
                    {"section": "Execution Journal", "min_role": "operator"},
                    {"section": "Strategy Lab", "min_role": "viewer"},
                    {"section": "P&L Monitor", "min_role": "viewer"},
                    {"section": "Notifications", "min_role": "viewer"},
                    {"section": "Ops Health", "min_role": "operator"},
                    {"section": "UI Diagnostics", "min_role": "operator"},
                    {"section": "Audit Trail", "min_role": "admin"},
                    {"section": "Operator Guide", "min_role": "viewer"},
                    {"section": "Backtest Hub", "min_role": "viewer"},
                    {"section": "UI Changelog", "min_role": "viewer"},
                ]
            )
            cap_rows["available"] = cap_rows["min_role"].map(lambda r: "YES" if _role_at_least(user_role, str(r)) else "NO")
            st.dataframe(cap_rows, use_container_width=True, hide_index=True)
        st.caption(f"Auto-logout: `{cfg.session_timeout_min}m`")
        if st.session_state.get("auth_method"):
            st.caption(f"Auth method: `{st.session_state.auth_method}`")
        _render_session_sidebar_tools(cfg, current_user=current_user)
        if st.button("Logout", use_container_width=True):
            _append_ui_audit("logout", {})
            _clear_auth_session(cfg)
            st.rerun()

        _render_admin_sidebar(cfg, st.session_state.auth_user)

        st.markdown("---")
        st.markdown("<div class='left-nav-card'>", unsafe_allow_html=True)
        st.markdown("### Workspace Navigator")
        workspace_options = _workspace_nav_options(user_role)
        visible_pref_raw = st.session_state.get("ui_workspace_visible_tabs", [])
        visible_pref = [t for t in visible_pref_raw if t in workspace_options] if isinstance(visible_pref_raw, list) else []
        workspace_nav_options = visible_pref if visible_pref else workspace_options
        nav_default = str(st.session_state.get("ui_workspace_nav_selection", st.session_state.get("ui_saved_workspace", "Tradeboard")) or "Tradeboard")
        if nav_default not in workspace_nav_options:
            nav_default = workspace_options[0] if workspace_options else "Tradeboard"
        nav_selection = st.radio(
            "Sections",
            options=workspace_nav_options,
            index=workspace_nav_options.index(nav_default) if workspace_nav_options else 0,
            key="ui_workspace_nav_selection",
            label_visibility="collapsed",
        )
        view_mode = st.selectbox(
            "Layout Mode",
            options=["Single Workspace", "Tabbed Workspace"],
            index=["Single Workspace", "Tabbed Workspace"].index(str(st.session_state.get("ui_workspace_view_mode", "Single Workspace")) if str(st.session_state.get("ui_workspace_view_mode", "Single Workspace")) in {"Single Workspace", "Tabbed Workspace"} else "Single Workspace"),
            key="ui_workspace_view_mode",
            help="Single Workspace keeps focus. Tabbed Workspace shows all sections.",
        )
        if st.button("Set As Preferred Workspace", use_container_width=True, key="ui_set_preferred_workspace_btn"):
            st.session_state["ui_pending_saved_workspace"] = nav_selection
            st.session_state["ui_palette_status"] = f"Preferred workspace set to {nav_selection}."
            st.rerun()
        st.checkbox(
            "Enable Layout Editor",
            value=bool(st.session_state.get("ui_layout_editor_enabled", False)),
            key="ui_layout_editor_enabled",
            disabled=bool(st.session_state.get("ui_panel_layout_locked", False)),
        )
        if bool(st.session_state.get("ui_panel_layout_locked", False)):
            st.caption("Layout editor is disabled because panel layout lock is ON.")
        if bool(st.session_state.get("ui_layout_editor_enabled", False)):
            current_visible_raw = st.session_state.get("ui_workspace_visible_tabs", [])
            current_visible = [t for t in current_visible_raw if t in workspace_options] if isinstance(current_visible_raw, list) else []
            if not current_visible:
                current_visible = list(workspace_options)
            new_visible = st.multiselect(
                "Visible Workspaces",
                options=workspace_options,
                default=current_visible,
                key="ui_workspace_visible_tabs",
                help="Only selected workspaces appear in tabbed/single mode navigation.",
            )
            if not new_visible:
                st.warning("At least one workspace must remain visible.")
            else:
                # Reorder editor avoids direct widget-state mutation by queuing a pending layout patch.
                r1, r2, r3 = st.columns([1.55, 1.0, 1.0])
                with r1:
                    reorder_pick = st.selectbox(
                        "Reorder Workspace",
                        options=new_visible,
                        index=0,
                        key="ui_workspace_reorder_pick",
                    )
                with r2:
                    if st.button("Move Up", use_container_width=True, key="ui_workspace_reorder_up"):
                        ordered = list(new_visible)
                        i = ordered.index(reorder_pick)
                        if i > 0:
                            ordered[i - 1], ordered[i] = ordered[i], ordered[i - 1]
                            st.session_state["ui_layout_pending_load"] = {"ui_workspace_visible_tabs": ordered}
                            st.rerun()
                with r3:
                    if st.button("Move Down", use_container_width=True, key="ui_workspace_reorder_down"):
                        ordered = list(new_visible)
                        i = ordered.index(reorder_pick)
                        if i < len(ordered) - 1:
                            ordered[i + 1], ordered[i] = ordered[i], ordered[i + 1]
                            st.session_state["ui_layout_pending_load"] = {"ui_workspace_visible_tabs": ordered}
                            st.rerun()
            st.caption("Layout editor controls are UI-only and per-user.")
        st.caption(f"Active: `{nav_selection}` | Mode: `{view_mode}`")
        st.markdown("</div>", unsafe_allow_html=True)

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
        perf_enabled_sidebar = bool(st.session_state.get("ui_perf_mode_enabled", False))
        perf_cap_rows = int(pd.to_numeric(st.session_state.get("ui_perf_max_rows", 3000), errors="coerce") or 3000)
        perf_cap_rows = max(500, min(10000, perf_cap_rows))
        effective_event_limit = min(int(event_limit), perf_cap_rows) if perf_enabled_sidebar else int(event_limit)
        if perf_enabled_sidebar:
            st.caption(f"Performance mode active: effective row cap `{effective_event_limit}` (requested `{int(event_limit)}`)")
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
                options=list(WORKSPACE_ORDER),
                index=list(WORKSPACE_ORDER).index(str(st.session_state.get("ui_saved_workspace", "Tradeboard")) if str(st.session_state.get("ui_saved_workspace", "Tradeboard")) in WORKSPACE_ORDER else "Tradeboard"),
                key="ui_saved_workspace",
            )
            st.text_input(
                "Default Eval Time (ET, HH:MM)",
                value=_normalize_eval_time(st.session_state.get("ui_eval_time", "15:55"), default="15:55"),
                key="ui_eval_time",
                help="Used for runtime command previews and countdown chips.",
            )
            compact_mode = st.checkbox(
                "Compact table density",
                value=bool(user_prefs.get("compact_mode", True)),
                key="pref_compact_mode",
            )
            st.selectbox(
                "Theme Preset",
                options=list(THEME_PRESETS.keys()),
                index=list(THEME_PRESETS.keys()).index(str(st.session_state.get("ui_theme_preset", "Neo Green")))
                if str(st.session_state.get("ui_theme_preset", "Neo Green")) in THEME_PRESETS
                else 0,
                key="ui_theme_preset",
            )
            st.selectbox(
                "Persona Mode",
                options=["Viewer", "Operator", "Admin"],
                index=["Viewer", "Operator", "Admin"].index(str(st.session_state.get("ui_persona_mode", "Operator")))
                if str(st.session_state.get("ui_persona_mode", "Operator")) in {"Viewer", "Operator", "Admin"}
                else 1,
                key="ui_persona_mode",
                help="Viewer narrows workspace scope and reduces operational noise.",
            )
            if st.button("Apply Persona Defaults", use_container_width=True, key="ui_apply_persona_defaults"):
                persona = str(st.session_state.get("ui_persona_mode", "Operator") or "Operator")
                if persona == "Viewer":
                    st.session_state["ui_layout_pending_load"] = {
                        "ui_saved_workspace": "Command Center",
                        "ui_workspace_nav_selection": "Command Center",
                        "ui_workspace_view_mode": "Single Workspace",
                        "ui_workspace_visible_tabs": [
                            "Command Center",
                            "Tradeboard",
                            "Multi-Chart",
                            "Portfolio Pulse",
                            "P&L Monitor",
                            "Notifications",
                            "Operator Guide",
                            "Backtest Hub",
                            "UI Changelog",
                        ],
                        "ui_focus_mode": True,
                        "ui_incident_mode": False,
                    }
                elif persona == "Admin":
                    st.session_state["ui_layout_pending_load"] = {
                        "ui_saved_workspace": "UI Diagnostics",
                        "ui_workspace_nav_selection": "UI Diagnostics",
                        "ui_workspace_view_mode": "Tabbed Workspace",
                        "ui_workspace_visible_tabs": [],
                        "ui_focus_mode": False,
                    }
                else:
                    st.session_state["ui_layout_pending_load"] = {
                        "ui_saved_workspace": "Tradeboard",
                        "ui_workspace_nav_selection": "Tradeboard",
                        "ui_workspace_view_mode": "Single Workspace",
                        "ui_workspace_visible_tabs": [],
                        "ui_focus_mode": False,
                    }
                st.session_state["ui_palette_status"] = f"Persona defaults applied: {persona}"
                st.rerun()
            st.selectbox(
                "Accessibility Preset",
                options=["Default", "High Contrast", "Large Text", "Reduced Motion"],
                index=["Default", "High Contrast", "Large Text", "Reduced Motion"].index(
                    str(st.session_state.get("ui_accessibility_preset", "Default"))
                )
                if str(st.session_state.get("ui_accessibility_preset", "Default")) in {"Default", "High Contrast", "Large Text", "Reduced Motion"}
                else 0,
                key="ui_accessibility_preset",
            )
            st.selectbox(
                "Density Mode",
                options=list(DENSITY_MODES),
                index=list(DENSITY_MODES).index(str(st.session_state.get("ui_density_mode", "Comfortable")))
                if str(st.session_state.get("ui_density_mode", "Comfortable")) in DENSITY_MODES
                else 0,
                key="ui_density_mode",
            )
            st.slider(
                "Font Scale %",
                min_value=85,
                max_value=130,
                value=int(pd.to_numeric(st.session_state.get("ui_font_scale_pct", 100), errors="coerce") or 100),
                step=5,
                key="ui_font_scale_pct",
            )
            st.checkbox(
                "High Contrast",
                value=bool(st.session_state.get("ui_high_contrast", False)),
                key="ui_high_contrast",
            )
            st.checkbox(
                "Reduced Motion",
                value=bool(st.session_state.get("ui_reduced_motion", False)),
                key="ui_reduced_motion",
                help="Disables transitions/animations for stability and accessibility.",
            )
            st.checkbox(
                "Focus Mode",
                value=bool(st.session_state.get("ui_focus_mode", False)),
                key="ui_focus_mode",
                help="Reduces secondary visual noise for operator concentration.",
            )
            st.checkbox(
                "Minimal Mode",
                value=bool(st.session_state.get("ui_minimal_mode", False)),
                key="ui_minimal_mode",
                help="Hides non-essential chrome so operators can focus on critical workflow cards.",
            )
            st.checkbox(
                "Keyboard Shortcuts",
                value=bool(st.session_state.get("ui_keyboard_shortcuts_enabled", True)),
                key="ui_keyboard_shortcuts_enabled",
                help="Ctrl/Cmd+K: command context, Alt+R: refresh, Alt+1..6: workspace jump.",
            )
            st.checkbox(
                "Mobile Compact Mode",
                value=bool(st.session_state.get("ui_mobile_compact_mode", False)),
                key="ui_mobile_compact_mode",
                help="Uses smaller table pages and denser controls for smaller screens.",
            )
            st.checkbox(
                "Incident Mode",
                value=bool(st.session_state.get("ui_incident_mode", False)),
                key="ui_incident_mode",
                help="Highlights incident-state styling across workspace cards.",
            )
            st.checkbox(
                "Mobile Bottom Navigation",
                value=bool(st.session_state.get("ui_mobile_bottom_nav", False)),
                key="ui_mobile_bottom_nav",
                help="Shows a bottom navigation rail for quick workspace switching.",
            )
            st.checkbox(
                "Show Onboarding Guide",
                value=bool(st.session_state.get("ui_show_onboarding", True)),
                key="ui_show_onboarding",
            )
            perf_enabled = st.checkbox(
                "Performance Mode",
                value=bool(st.session_state.get("ui_perf_mode_enabled", False)),
                key="ui_perf_mode_enabled",
                help="Caps loaded rows for faster rendering on large runtime DBs.",
            )
            st.selectbox(
                "Fast Widgets Cadence (sec)",
                options=[1, 2, 3, 5, 10, 15, 30],
                index=[1, 2, 3, 5, 10, 15, 30].index(int(st.session_state.get("ui_fast_widget_cadence_sec", 5)))
                if int(st.session_state.get("ui_fast_widget_cadence_sec", 5)) in [1, 2, 3, 5, 10, 15, 30]
                else 3,
                key="ui_fast_widget_cadence_sec",
            )
            st.selectbox(
                "Heavy Widgets Cadence (sec)",
                options=[5, 10, 15, 30, 60, 120, 300],
                index=[5, 10, 15, 30, 60, 120, 300].index(int(st.session_state.get("ui_heavy_widget_cadence_sec", 30)))
                if int(st.session_state.get("ui_heavy_widget_cadence_sec", 30)) in [5, 10, 15, 30, 60, 120, 300]
                else 3,
                key="ui_heavy_widget_cadence_sec",
            )
            st.slider(
                "Performance Max Rows",
                min_value=500,
                max_value=10000,
                value=int(pd.to_numeric(st.session_state.get("ui_perf_max_rows", 3000), errors="coerce") or 3000),
                step=250,
                key="ui_perf_max_rows",
                disabled=not bool(perf_enabled),
            )
            st.checkbox(
                "Lock Panel Layout",
                value=bool(st.session_state.get("ui_panel_layout_locked", False)),
                key="ui_panel_layout_locked",
                help="Prevents accidental layout/split edits in Tradeboard/Execution workspaces.",
            )
            if st.button("Reset UI Layout To Defaults", use_container_width=True, key="ui_reset_layout_defaults"):
                st.session_state["ui_layout_pending_load"] = {
                    "ui_workspace_view_mode": "Single Workspace",
                    "ui_workspace_nav_selection": "Tradeboard",
                    "ui_saved_workspace": "Tradeboard",
                    "ui_workspace_visible_tabs": [],
                    "ui_tradeboard_split": "Balanced",
                    "ui_execution_split": "Balanced",
                    "ui_tradeboard_custom_weights": [0.8, 1.95, 0.75],
                    "ui_execution_custom_weights": [1.15, 1.0],
                    "ui_panel_layout_locked": False,
                    "ui_popout_panel": "",
                }
                st.session_state["ui_palette_status"] = "UI layout reset to defaults."
                st.rerun()
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

            st.markdown("---")
            st.caption("Saved View Packs")
            view_pack_name = st.text_input("View Pack Name", value="", key="ui_saved_view_pack_name")
            vp1, vp2, vp3 = st.columns(3)
            with vp1:
                if st.button("Save View Pack", use_container_width=True, key="ui_save_view_pack"):
                    pack_name = str(view_pack_name or "").strip()
                    if not pack_name:
                        st.warning("Enter a view pack name.")
                    else:
                        saved_views_raw = st.session_state.get("ui_saved_views", {})
                        saved_views = saved_views_raw if isinstance(saved_views_raw, dict) else {}
                        packs_raw = saved_views.get("__packs__", {})
                        packs = packs_raw if isinstance(packs_raw, dict) else {}
                        packs[pack_name] = {
                            "saved_at_ny": pd.Timestamp.now(tz=NY_TZ).isoformat(),
                            "ui_state": {k: st.session_state.get(k) for k in USER_UI_PREF_KEYS},
                        }
                        saved_views["__packs__"] = packs
                        st.session_state["ui_saved_views"] = saved_views
                        st.success(f"Saved view pack: {pack_name}")
            saved_views_raw = st.session_state.get("ui_saved_views", {})
            saved_views = saved_views_raw if isinstance(saved_views_raw, dict) else {}
            packs_raw = saved_views.get("__packs__", {})
            packs = packs_raw if isinstance(packs_raw, dict) else {}
            pack_opts = sorted(packs.keys())
            selected_pack = ""
            with vp2:
                if pack_opts:
                    selected_pack = st.selectbox("Saved Pack", options=pack_opts, index=0, key="ui_saved_view_pack_pick")
                else:
                    st.caption("No saved view packs.")
            with vp3:
                if pack_opts and st.button("Apply View Pack", use_container_width=True, key="ui_apply_view_pack"):
                    payload = packs.get(selected_pack, {})
                    ui_payload = payload.get("ui_state", {}) if isinstance(payload, dict) else {}
                    if isinstance(ui_payload, dict):
                        st.session_state["ui_layout_pending_load"] = ui_payload
                        st.success(f"Applied view pack: {selected_pack}")
                        st.rerun()
            if pack_opts:
                dp1, dp2 = st.columns(2)
                with dp1:
                    if st.button("Delete View Pack", use_container_width=True, key="ui_delete_view_pack"):
                        target = str(st.session_state.get("ui_saved_view_pack_pick", "") or "")
                        if target and target in packs:
                            packs.pop(target, None)
                            saved_views["__packs__"] = packs
                            st.session_state["ui_saved_views"] = saved_views
                            st.success(f"Deleted view pack: {target}")
                            st.rerun()
                with dp2:
                    export_payload = {
                        "schema": "switch_ui_saved_views_v1",
                        "exported_at_ny": pd.Timestamp.now(tz=NY_TZ).isoformat(),
                        "saved_views": packs,
                    }
                    st.download_button(
                        "Export View Packs JSON",
                        data=json.dumps(export_payload, indent=2).encode("utf-8"),
                        file_name=f"{current_user}_view_packs.json",
                        mime="application/json",
                        use_container_width=True,
                        key="ui_export_view_packs",
                    )
            import_file = st.file_uploader("Import View Packs JSON", type=["json"], key="ui_import_view_packs")
            if import_file is not None:
                try:
                    imported = json.loads(import_file.getvalue().decode("utf-8"))
                    imported_views = imported.get("saved_views", {}) if isinstance(imported, dict) else {}
                    if not isinstance(imported_views, dict):
                        raise ValueError("missing saved_views map")
                    merged = dict(saved_views)
                    merged_packs_raw = merged.get("__packs__", {})
                    merged_packs = merged_packs_raw if isinstance(merged_packs_raw, dict) else {}
                    for k, v in imported_views.items():
                        if isinstance(k, str) and isinstance(v, dict):
                            merged_packs[str(k)] = v
                    merged["__packs__"] = merged_packs
                    st.session_state["ui_saved_views"] = merged
                    st.success(f"Imported view packs: {len(imported_views)}")
                except Exception as exc:
                    st.error(f"Invalid view-pack JSON: {exc}")
            st.markdown("---")
            snapshot_payload = {
                "schema": "switch_ui_workspace_snapshot_v1",
                "generated_at_ny": pd.Timestamp.now(tz=NY_TZ).isoformat(),
                "user": current_user,
                "workspace": str(st.session_state.get("ui_workspace_nav_selection", "Tradeboard")),
                "ui_state": {k: st.session_state.get(k) for k in USER_UI_PREF_KEYS},
                "runtime_db_path": str(db_path),
                "event_limit": int(event_limit),
                "effective_event_limit": int(effective_event_limit),
            }
            st.download_button(
                "Export Workspace Snapshot JSON",
                data=json.dumps(snapshot_payload, indent=2, default=str).encode("utf-8"),
                file_name=f"{current_user}_workspace_snapshot.json",
                mime="application/json",
                use_container_width=True,
                key="ui_export_workspace_snapshot",
            )

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
                                st.session_state["ui_pending_saved_workspace"] = str(payload.get("workspace"))
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

    state_df, events_df = _load_runtime_db(db_path=db_path, event_limit=effective_event_limit)
    notices_df = _notification_center_table(events_df, state_df)
    _sync_toast_history(notices_df)

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

    events_global_raw = _global_filter_toolbar(events_df)
    fast_sec = int(pd.to_numeric(st.session_state.get("ui_fast_widget_cadence_sec", 5), errors="coerce") or 5)
    heavy_sec = int(pd.to_numeric(st.session_state.get("ui_heavy_widget_cadence_sec", 30), errors="coerce") or 30)
    events_fast = _apply_widget_cadence(events_global_raw, key="fast", cadence_sec=fast_sec)
    events_heavy = _apply_widget_cadence(events_global_raw, key="heavy", cadence_sec=heavy_sec)
    events_global = events_fast
    _render_top_command_bar(events_fast, state_df, user_role=user_role)
    _render_global_command_palette_modal(events_fast, state_df, user_role=user_role)
    _render_change_since_refresh_strip(events_fast, notices_df)
    _render_realtime_status_ribbon(events_fast, state_df, current_user=current_user, user_role=user_role)
    _render_active_context_bar(events_fast, state_df)
    _render_workspace_preset_chips()
    _render_onboarding_guide()
    _render_workspace_quick_nav(_workspace_nav_options(user_role))
    _render_keyboard_cheatsheet()
    _render_activity_timeline_strip(events_fast)

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

    _render_runtime_health_banner(cfg, db_path=db_path, events_df=events_fast, state_df=state_df)
    _render_banner(db_path=db_path, state_df=state_df, events_df=events_fast)
    _render_status_bar(events_df=events_fast, state_df=state_df)
    _render_risk_strip(events_df=events_fast, state_df=state_df)
    _render_actionable_alert_center(notices_df=notices_df)
    _render_operator_brief(events_df=events_fast, state_df=state_df)
    _render_kpis(events_df=events_fast, state_df=state_df)
    _render_persistent_pnl_tiles(events_df=events_heavy, state_df=state_df)
    st.caption(f"Preferred Workspace: `{st.session_state.get('ui_saved_workspace', 'Tradeboard')}` | Tip: pin browser tab with this dashboard open on your preferred tab.")

    allowed_tabs = _workspace_nav_options(user_role)
    visible_tabs_raw = st.session_state.get("ui_workspace_visible_tabs", [])
    if isinstance(visible_tabs_raw, list) and visible_tabs_raw:
        filtered_tabs = [t for t in visible_tabs_raw if t in allowed_tabs]
        if filtered_tabs:
            allowed_tabs = filtered_tabs
    _render_mobile_bottom_nav(allowed_tabs)
    layout_mode = str(st.session_state.get("ui_workspace_view_mode", "Single Workspace") or "Single Workspace")
    if layout_mode == "Tabbed Workspace":
        tab_objs = st.tabs(allowed_tabs)
        for tab_obj, tab_name in zip(tab_objs, allowed_tabs, strict=False):
            with tab_obj:
                _render_workspace_by_name(
                    tab_name,
                    events_df=events_df,
                    events_global=events_heavy,
                    state_df=state_df,
                    db_path=db_path,
                    current_user=current_user,
                    user_role=user_role,
                    user_default_db=user_default_db,
                    strict_isolation=bool(st.session_state.get("ui_strict_user_db", True)),
                )
    else:
        active_workspace = str(st.session_state.get("ui_workspace_nav_selection", st.session_state.get("ui_saved_workspace", "Tradeboard")) or "Tradeboard")
        if active_workspace not in allowed_tabs and allowed_tabs:
            active_workspace = allowed_tabs[0]
        st.markdown("<div class='desk-block'>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='section-caption'>Focused workspace mode: `{active_workspace}`. Switch in left navigator for faster operator flow.</div>",
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)
        _render_workspace_by_name(
            active_workspace,
            events_df=events_df,
            events_global=events_heavy,
            state_df=state_df,
            db_path=db_path,
            current_user=current_user,
            user_role=user_role,
            user_default_db=user_default_db,
            strict_isolation=bool(st.session_state.get("ui_strict_user_db", True)),
        )
    _render_popout_panel(events_df=events_global, state_df=state_df)
    _render_toast_center()
    render_end = pd.Timestamp.now(tz=NY_TZ)
    ms = max(0.0, (render_end - render_start).total_seconds() * 1000.0)
    rec = {
        "ts_ny": render_end.isoformat(),
        "render_ms": round(float(ms), 3),
        "rows_events": int(len(events_df)),
        "rows_state": int(len(state_df)),
        "workspace": str(st.session_state.get("ui_workspace_nav_selection", "Tradeboard")),
    }
    timings_raw = st.session_state.get("ui_render_timings", [])
    timings = list(timings_raw) if isinstance(timings_raw, list) else []
    timings.append(rec)
    st.session_state["ui_render_timings"] = timings[-500:]


if __name__ == "__main__":
    _main_app()
