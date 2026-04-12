from __future__ import annotations

import calendar
import html
import os
from datetime import datetime, date, timedelta, timezone

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

load_dotenv()
import database as db


def _env_flag_true(name: str, *, default: str = "0") -> bool:
    """環境変数が 1 / true / yes / on（大小無視）なら True。未設定は default に従う。"""
    v = os.environ.get(name, default)
    if v is None:
        v = default
    return str(v).strip().lower() in ("1", "true", "yes", "on")

@st.cache_data(ttl=60)
def _cached_fetch_notion_events(
    database_id: str,
    time_min_iso: str,
    time_max_iso: str,
    date_prop: str,
    token_hash: str,
) -> tuple[list[dict], str | None]:
    """Notion DB から期間内のイベントをキャッシュ付きで取得。token_hash はキャッシュ無効化用。"""
    import notion_calendar_client as ncc

    _ = token_hash
    return ncc.fetch_events(database_id, time_min_iso, time_max_iso, date_prop)


def _month_bounds_utc_iso(year: int, month: int, tz: ZoneInfo) -> tuple[str, str]:
    start = datetime(year, month, 1, 0, 0, 0, tzinfo=tz)
    if month == 12:
        end = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=tz)
    else:
        end = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=tz)
    tmin = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    tmax = end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return tmin, tmax


def _event_local_date(ev: dict, tz: ZoneInfo) -> date | None:
    """一覧表用。終日は開始日（複数日終日の代表日）。"""
    s = ev.get("start")
    if not s:
        return None
    if ev.get("all_day"):
        return date.fromisoformat(s[:10])
    iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).date()


def _timed_event_local_start_date(ev: dict, tz: ZoneInfo) -> date | None:
    if ev.get("all_day"):
        return None
    s = ev.get("start")
    if not s:
        return None
    iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).date()


def _all_day_event_covers_date(ev: dict, day: date) -> bool:
    """Google の終日イベント（end.date は排他的）が day を含むか。"""
    s = ev.get("start") or ""
    if not s:
        return False
    start_d = date.fromisoformat(s[:10])
    end_raw = ev.get("end")
    if end_raw:
        end_exclusive = date.fromisoformat(str(end_raw)[:10])
    else:
        end_exclusive = start_d + timedelta(days=1)
    return start_d <= day < end_exclusive


def _event_occurs_on_day(ev: dict, day: date, tz: ZoneInfo) -> bool:
    if ev.get("all_day"):
        return _all_day_event_covers_date(ev, day)
    ld = _timed_event_local_start_date(ev, tz)
    return ld == day if ld is not None else False


def _events_for_day(events: list[dict], day: date, tz: ZoneInfo) -> list[dict]:
    out = [ev for ev in events if _event_occurs_on_day(ev, day, tz)]
    out.sort(key=lambda e: (not e.get("all_day"), e.get("start") or ""))
    return out


def _calendar_display_label(cal_id: str) -> str:
    if cal_id.strip().lower() == "primary":
        return "メイン（primary）"
    return cal_id


def _calendar_sources_caption(cal_ids: tuple[str, ...]) -> str:
    """ユーザー向けの取得元の短い説明（1本または複数）。"""
    if len(cal_ids) == 1:
        return _calendar_display_label(cal_ids[0])
    parts = [_calendar_display_label(c) for c in cal_ids[:3]]
    head = "、".join(parts)
    if len(cal_ids) > 3:
        return f"{head} ほか {len(cal_ids) - 3} 本（計 {len(cal_ids)} 本）"
    return f"{head}（計 {len(cal_ids)} 本）"


def _sort_events_by_start(evs: list[dict]) -> list[dict]:
    return sorted(evs, key=lambda e: (e.get("start") or "", e.get("summary") or ""))


def _merge_events_dedup(a: list[dict], b: list[dict]) -> list[dict]:
    """同一カレンダー内の id 重複に加え、複数カレンダー間は iCalUID があれば1件にまとめる。"""
    merged: dict[str, dict] = {}
    n_fallback = 0
    for ev in a + b:
        iu = ev.get("iCalUID")
        if iu:
            key = f"ical:{iu}"
        else:
            cid = ev.get("calendar_id") or ""
            eid = ev.get("id")
            if eid:
                key = f"ev:{cid}:{eid}"
            else:
                key = f"nf:{cid}:{n_fallback}"
                n_fallback += 1
        merged.setdefault(key, ev)
    return list(merged.values())


def _format_all_calendars_failed(errs: list[tuple[str, str]]) -> str:
    parts = [f"{_calendar_display_label(cid)}: {msg}" for cid, msg in errs]
    return "すべてのカレンダーで取得に失敗しました — " + " | ".join(parts)


def _format_partial_calendar_failures(errs: list[tuple[str, str]]) -> str:
    parts = [f"{_calendar_display_label(cid)}: {msg}" for cid, msg in errs]
    return "次のカレンダーのみ失敗（他は表示中）— " + " | ".join(parts)


@st.cache_data(ttl=60)
def _cached_fetch_notion_tasks(
    database_id: str,
    day_iso: str,
    date_prop: str,
    status_prop: str | None,
    token_hash: str,
) -> tuple[list[dict], str | None]:
    """Notion タスク DB から期限が day_iso のタスクをキャッシュ付きで取得。"""
    import notion_calendar_client as ncc

    _ = token_hash
    return ncc.fetch_tasks_for_day(
        database_id, date.fromisoformat(day_iso), date_prop, status_prop
    )


@st.cache_data(ttl=60)
def _cached_fetch_google_events(
    calendar_id: str,
    time_min_iso: str,
    time_max_iso: str,
    cred_mtime: float,
) -> tuple[list[dict], str | None]:
    """Google Calendar から期間内のイベントをキャッシュ付きで取得。"""
    import google_calendar_client as gcc
    from googleapiclient.errors import HttpError

    _ = cred_mtime
    creds = gcc.get_credentials(interactive=False)
    if creds is None:
        return [], "Google 認証情報がありません（token.json を確認してください）。"
    svc = gcc.build_calendar_service(creds)
    try:
        evs, _ = gcc.list_events(svc, calendar_id, time_min_iso, time_max_iso)
        return evs, None
    except HttpError as e:
        return [], gcc.fetch_events_http_error_message(e, calendar_id)
    except Exception as e:
        return [], str(e)


def _fetch_google_months_events(
    cal_ids: tuple[str, ...],
    months: set[tuple[int, int]],
    cal_tz: ZoneInfo,
    cred_mtime: float,
) -> tuple[list[dict], list[tuple[str, str]]]:
    """複数月・複数カレンダーのイベントを Google Calendar から取得して重複除去。"""
    all_evs: list[dict] = []
    errors: list[tuple[str, str]] = []
    for y, m in sorted(months):
        tmin, tmax = _month_bounds_utc_iso(y, m, cal_tz)
        for cid in cal_ids:
            evs, err = _cached_fetch_google_events(cid, tmin, tmax, cred_mtime)
            if err:
                errors.append((cid, err))
            else:
                all_evs = _merge_events_dedup(all_evs, evs)
    return _sort_events_by_start(all_evs), errors


@st.cache_data(ttl=60)
def _cached_fetch_google_tasks(
    day_iso: str,
    tz_name: str,
    cred_mtime: float,
) -> tuple[list[dict], str | None]:
    """Google Tasks から期限が day_iso のタスクをキャッシュ付きで取得。"""
    import google_calendar_client as gcc
    from googleapiclient.errors import HttpError

    _ = cred_mtime
    creds = gcc.get_credentials(interactive=False)
    if creds is None:
        return [], None
    try:
        tsvc = gcc.build_tasks_service(creds)
        tz = ZoneInfo(tz_name)
        return gcc.fetch_tasks_for_calendar_day_all_lists(tsvc, date.fromisoformat(day_iso), tz)
    except HttpError as e:
        import google_calendar_client as gcc2
        return [], gcc2.fetch_tasks_http_error_message(e)
    except Exception as e:
        return [], str(e)


def _task_is_google_date_only_due(due: str) -> bool:
    """Google が日付のみ期限でよく使う UTC 0:00（JST では 9:00 に見えるが時刻指定ではない）。"""
    iso = due.replace("Z", "+00:00") if due.endswith("Z") else due
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    u = dt.astimezone(timezone.utc)
    return u.hour == 0 and u.minute == 0 and u.second == 0


def _format_tasks_html(tasks: list[dict], tz: ZoneInfo) -> str:
    if not tasks:
        return ""
    list_titles = {t.get("tasklist_title") for t in tasks if t.get("tasklist_title")}
    show_list = len(list_titles) > 1
    parts: list[str] = []
    for t in tasks:
        prefix = "↳ " if t.get("parent") else ""
        title = html.escape(prefix + (t.get("title") or ""))
        list_note = ""
        if show_list and t.get("tasklist_title"):
            lt = html.escape(str(t["tasklist_title"]))
            list_note = f" <span style='opacity:0.65;font-size:0.82rem'>（{lt}）</span>"
        due_note = ""
        du = t.get("due")
        if du:
            if _task_is_google_date_only_due(du):
                due_note = " <span style='opacity:0.7;font-size:0.85rem'>日付のみ</span>"
            else:
                try:
                    iso = du.replace("Z", "+00:00") if du.endswith("Z") else du
                    dtv = datetime.fromisoformat(iso)
                    if dtv.tzinfo is None:
                        dtv = dtv.replace(tzinfo=timezone.utc)
                    loc = dtv.astimezone(tz)
                    due_note = (
                        f" <span style='opacity:0.7;font-size:0.85rem'>{loc.strftime('%H:%M')}</span>"
                    )
                except ValueError:
                    pass
        if t.get("status") == "completed":
            parts.append(
                f"<li style='margin:0.35rem 0;opacity:0.85'><del>{title}</del>{due_note}{list_note}</li>"
            )
        else:
            parts.append(f"<li style='margin:0.35rem 0'>○ {title}{due_note}{list_note}</li>")
    return "<ul style='margin:0;padding-left:1.15rem'>" + "".join(parts) + "</ul>"


def _format_ev_line(
    ev: dict, tz: ZoneInfo, *, show_calendar_source: bool = False
) -> str:
    if ev.get("all_day"):
        line = f"終日 — {html.escape(ev.get('summary', ''))}"
    else:
        s = ev.get("start") or ""
        iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
        try:
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local = dt.astimezone(tz)
            t = local.strftime("%H:%M")
        except ValueError:
            t = "?"
        line = f"{t} — {html.escape(ev.get('summary', ''))}"
    if show_calendar_source and ev.get("calendar_id"):
        src = html.escape(_calendar_display_label(ev["calendar_id"]))
        line += (
            f" <span style='opacity:0.55;font-size:0.8rem'>（{src}）</span>"
        )
    return line


def _fetch_notion_months_events(
    db_id: str,
    months: set[tuple[int, int]],
    cal_tz: ZoneInfo,
    date_prop: str,
    token_hash: str,
) -> tuple[list[dict], dict[tuple[int, int], str]]:
    """複数月のイベントを Notion DB から取得して重複除去。

    戻り値: (merged_events, {(年, 月): エラーメッセージ})
    """
    all_evs: list[dict] = []
    failed: dict[tuple[int, int], str] = {}
    for y, m in sorted(months):
        tmin, tmax = _month_bounds_utc_iso(y, m, cal_tz)
        evs, err = _cached_fetch_notion_events(db_id, tmin, tmax, date_prop, token_hash)
        if err:
            failed[(y, m)] = err
        else:
            all_evs = _merge_events_dedup(all_evs, evs)
    return _sort_events_by_start(all_evs), failed


def _render_month_day_grid(
    year: int,
    month: int,
    selected: date,
    today: date,
) -> None:
    cal = calendar.Calendar(firstweekday=calendar.MONDAY)
    weeks = cal.monthdatescalendar(year, month)
    wdays = ["月", "火", "水", "木", "金", "土", "日"]
    header = st.columns(7)
    for i, w in enumerate(wdays):
        header[i].markdown(
            f"<div style='text-align:center;font-weight:700;font-size:0.72rem;opacity:0.85'>{html.escape(w)}</div>",
            unsafe_allow_html=True,
        )
    for week in weeks:
        cols = st.columns(7)
        for col, d in zip(cols, week):
            with col:
                in_month = d.month == month
                if not in_month:
                    st.markdown(
                        f"<div style='text-align:center;opacity:0.35;font-size:0.78rem;padding:0.35rem 0'>{d.day}</div>",
                        unsafe_allow_html=True,
                    )
                    continue
                ds = d.isoformat()
                is_sel = d == selected
                is_today = d == today
                label = str(d.day)
                if is_today:
                    label = f"◆ {label}"
                help_txt = ds + ("（今日）" if is_today else "")
                if st.button(
                    label,
                    key=f"day_btn_{year}_{month}_{ds}",
                    use_container_width=True,
                    type="primary" if is_sel else "secondary",
                    help=help_txt,
                ):
                    st.session_state["daily_date_input"] = d
                    st.session_state["cal_view_y"] = d.year
                    st.session_state["cal_view_m"] = d.month
                    st.rerun()


# ─── ページ設定 ────────────────────────────────────────────
st.set_page_config(
    page_title="Success Planning 2026",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

db.init_db()

# ─── CSS ──────────────────────────────────────────────────
st.markdown("""
<style>
.section-header {
    background: #f5c842;
    padding: 6px 14px;
    font-weight: bold;
    font-size: 1rem;
    margin: 1.2rem 0 0.6rem 0;
    border-radius: 4px;
    color: #333;
}
.static-label {
    font-weight: bold;
    font-size: 0.85rem;
    color: #555;
    margin-bottom: 2px;
}
.saved-badge {
    color: green;
    font-weight: bold;
}
.gt-day-events-panel {
    min-height: min(52vh, 520px);
    max-height: min(52vh, 520px);
    overflow-y: auto;
    border: 1px solid rgba(128, 128, 128, 0.35);
    border-radius: 8px;
    padding: 12px 14px;
    margin-top: 0.25rem;
    background: rgba(128, 128, 128, 0.06);
}
.gt-day-events-panel--compact {
    min-height: unset;
    max-height: none;
}
.gt-cal-month-title {
    font-weight: 700;
    font-size: 0.95rem;
    margin: 0.35rem 0 0.5rem 0;
}
</style>
""", unsafe_allow_html=True)


# ─── ユーティリティ ────────────────────────────────────────
def current_quarter(d: date) -> tuple[int, int]:
    q = (d.month - 1) // 3 + 1
    return d.year, q


def quarter_months(year: int, quarter: int) -> list[int]:
    start = (quarter - 1) * 3 + 1
    return [start, start + 1, start + 2]


def fmt_date(d: str) -> str:
    try:
        dt = datetime.strptime(d, "%Y-%m-%d")
        return f"{dt.year}年{dt.month}月{dt.day}日（{'月火水木金土日'[dt.weekday()]}）"
    except Exception:
        return d


# ─── サイドバー ────────────────────────────────────────────
with st.sidebar:
    st.markdown("# 🎯 Success Planning")
    st.markdown("---")
    page = st.radio(
        "ページ",
        ["🏆 ライフミッション", "📊 四半期目標", "📝 日次記録", "📖 過去の記録", "📈 分析"],
        label_visibility="collapsed",
    )
    st.markdown("---")
    all_dates = db.get_all_entry_dates()
    st.caption(f"記録日数: {len(all_dates)} 日")
    if all_dates:
        st.caption(f"最終記録: {all_dates[0]}")


# ══════════════════════════════════════════════════════════
# 📝 日次記録
# ══════════════════════════════════════════════════════════
if page == "📝 日次記録":
    import notion_calendar_client as ncc

    tz_name = os.environ.get("NOTION_CALENDAR_TZ", "Asia/Tokyo")
    cal_tz = ZoneInfo(tz_name)
    cal_db_id = os.environ.get("NOTION_CALENDAR_DATABASE_ID", "").strip()
    cal_date_prop = os.environ.get("NOTION_CALENDAR_DATE_PROPERTY", "Date")
    tasks_db_id = os.environ.get("NOTION_TASKS_DATABASE_ID", "").strip()
    tasks_date_prop = os.environ.get("NOTION_TASKS_DATE_PROPERTY", "Due")
    tasks_status_prop = os.environ.get("NOTION_TASKS_STATUS_PROPERTY", "").strip() or None
    notion_token = ncc.get_token()
    is_configured = bool(notion_token and cal_db_id)
    token_hash = str(hash(notion_token or ""))

    import google_calendar_client as gcc
    gcal_tz_name = os.environ.get("GOOGLE_CALENDAR_TZ", "Asia/Tokyo")
    gcal_tz = ZoneInfo(gcal_tz_name)
    gcal_ids = gcc.parse_calendar_ids(os.environ.get("GOOGLE_CALENDAR_ID"))
    _gcal_token_path = gcc.default_token_path()
    try:
        gcal_cred_mtime = os.path.getmtime(_gcal_token_path)
    except OSError:
        gcal_cred_mtime = 0.0
    gcal_configured = gcal_cred_mtime > 0.0 and os.path.isfile(gcc.default_credentials_path())

    if "daily_date_input" not in st.session_state:
        st.session_state["daily_date_input"] = date.today()
    if "cal_view_y" not in st.session_state:
        st.session_state["cal_view_y"] = st.session_state["daily_date_input"].year
        st.session_state["cal_view_m"] = st.session_state["daily_date_input"].month

    if st.session_state.get("edit_date"):
        ed = st.session_state.pop("edit_date")
        if isinstance(ed, str):
            st.session_state["daily_date_input"] = date.fromisoformat(ed)
        elif isinstance(ed, date):
            st.session_state["daily_date_input"] = ed
        d0 = st.session_state["daily_date_input"]
        st.session_state["cal_view_y"] = d0.year
        st.session_state["cal_view_m"] = d0.month

    cal_qp = st.query_params.get("cal")
    if cal_qp:
        try:
            dsel = date.fromisoformat(cal_qp)
            st.session_state["daily_date_input"] = dsel
            st.session_state["cal_view_y"] = dsel.year
            st.session_state["cal_view_m"] = dsel.month
        except ValueError:
            pass
        try:
            del st.query_params["cal"]
        except Exception:
            pass

    def _on_daily_date_change():
        d = st.session_state["daily_date_input"]
        st.session_state["cal_view_y"] = d.year
        st.session_state["cal_view_m"] = d.month

    cy = st.session_state["cal_view_y"]
    cm = st.session_state["cal_view_m"]
    selected_date = st.session_state["daily_date_input"]
    today_local = datetime.now(cal_tz).date()

    st.title(f"📝 {fmt_date(selected_date.isoformat())}")
    if selected_date != today_local:
        st.info(
            f"**編集中の日付:** {selected_date.year}年{selected_date.month}月{selected_date.day}日（今日ではありません）"
        )

    col_cal, col_ev = st.columns([1, 1], gap="large")

    month_events: list[dict] = []
    month_failed: dict[tuple[int, int], str] = {}
    sel_month_key = (selected_date.year, selected_date.month)

    with col_cal:
        nav1, nav2, nav3 = st.columns([1, 2.2, 1])
        with nav1:
            if st.button("◀", help="前の月（表示のみ）", key="cal_nav_prev"):
                if cm == 1:
                    st.session_state["cal_view_y"] = cy - 1
                    st.session_state["cal_view_m"] = 12
                else:
                    st.session_state["cal_view_m"] = cm - 1
                st.rerun()
        with nav3:
            if st.button("▶", help="次の月（表示のみ）", key="cal_nav_next"):
                if cm == 12:
                    st.session_state["cal_view_y"] = cy + 1
                    st.session_state["cal_view_m"] = 1
                else:
                    st.session_state["cal_view_m"] = cm + 1
                st.rerun()
        with nav2:
            st.caption(f"カレンダー表示: {cy}年{cm}月（週始まり: 月曜） · 日付は下のマスをクリック")

        st.markdown(
            f'<div class="gt-cal-month-title">{cy}年{cm}月</div>',
            unsafe_allow_html=True,
        )

        if is_configured:
            months_needed = {(cy, cm), (selected_date.year, selected_date.month)}
            month_events, month_failed = _fetch_notion_months_events(
                cal_db_id, months_needed, cal_tz, cal_date_prop, token_hash
            )
        elif cal_db_id:
            st.caption("NOTION_TOKEN を設定後、この月の予定件数がグリッドに表示されます。")

        gcal_month_errors: list[tuple[str, str]] = []
        if gcal_configured:
            months_needed_g = {(cy, cm), (selected_date.year, selected_date.month)}
            gcal_evs, gcal_month_errors = _fetch_google_months_events(
                gcal_ids, months_needed_g, gcal_tz, gcal_cred_mtime
            )
            month_events = _sort_events_by_start(_merge_events_dedup(month_events, gcal_evs))

        _render_month_day_grid(cy, cm, selected_date, today_local)

        with st.expander("日付を直接指定", expanded=False):
            st.date_input(
                "対象日",
                key="daily_date_input",
                label_visibility="collapsed",
                on_change=_on_daily_date_change,
            )

        if not notion_token:
            st.warning(
                "Notion との接続には `NOTION_TOKEN` が必要です。"
                " `.env` に `NOTION_TOKEN=secret_xxx...` を追加してください。"
            )
        elif not cal_db_id:
            st.warning(
                "`NOTION_CALENDAR_DATABASE_ID` が未設定です。"
                " Notion カレンダー用データベースの ID を `.env` に追加してください。"
            )

        for (fy, fm), fmsg in sorted(month_failed.items()):
            st.warning(f"{fy}年{fm}月の予定を取得できませんでした: {fmsg}")

        for gcal_err_cid, gcal_err_msg in gcal_month_errors:
            st.warning(f"Google カレンダー ({_calendar_display_label(gcal_err_cid)}): {gcal_err_msg}")

    with col_ev:
        day_tasks: list[dict] = []
        tasks_err: str | None = None
        if is_configured and tasks_db_id:
            day_tasks, tasks_err = _cached_fetch_notion_tasks(
                tasks_db_id, selected_date.isoformat(), tasks_date_prop, tasks_status_prop, token_hash
            )

        google_tasks: list[dict] = []
        google_tasks_err: str | None = None
        if gcal_configured:
            google_tasks, google_tasks_err = _cached_fetch_google_tasks(
                selected_date.isoformat(), gcal_tz_name, gcal_cred_mtime
            )

        st.markdown("**予定・タスク**")
        st.caption(
            "このアプリの日付・タイムゾーン基準で表示します。端末のカレンダーと日付がずれることがあります。"
        )
        if month_failed and sel_month_key not in month_failed:
            st.caption(
                "※ 別の月の予定取得に失敗しています。左の警告を確認してください。"
            )
        if tasks_err:
            st.warning(tasks_err, icon="⚠️")

        any_source_configured = is_configured or gcal_configured
        notion_sel_ok = not is_configured or sel_month_key not in month_failed
        day_evs = (
            _events_for_day(month_events, selected_date, cal_tz)
            if any_source_configured
            else []
        )

        cal_block = ""
        if not any_source_configured:
            cal_block = (
                "<p style='opacity:0.85;margin:0'>未設定です。.env に NOTION_TOKEN と"
                " NOTION_CALENDAR_DATABASE_ID、または Google カレンダー認証情報を設定してください。</p>"
            )
        elif not notion_sel_ok and not gcal_configured:
            msg = html.escape(month_failed[sel_month_key])
            cal_block = (
                "<p style='opacity:0.85;margin:0'>この日が属する月の予定を取得できませんでした。</p>"
                f"<p style='margin:0.5rem 0 0 0;font-size:0.9rem;opacity:0.9'>{msg}</p>"
            )
        elif not day_evs:
            cal_block = (
                "<p style='opacity:0.75;margin:0'>この日に表示する予定はありません。</p>"
            )
        else:
            items = "".join(
                f"<li style='margin:0.35rem 0'>"
                f"{_format_ev_line(ev, cal_tz)}</li>"
                for ev in day_evs
            )
            cal_block = f"<ul style='margin:0;padding-left:1.15rem'>{items}</ul>"

        task_block = ""
        if not is_configured:
            pass
        elif not tasks_db_id:
            task_block = (
                "<p style='opacity:0.75;margin:0.35rem 0 0 0;font-size:0.9rem'>"
                "タスク表示には <code>NOTION_TASKS_DATABASE_ID</code> の設定が必要です。</p>"
            )
        elif tasks_err:
            task_block = (
                "<p style='opacity:0.85;margin:0'>タスクを取得できませんでした（上の警告を確認）。</p>"
            )
        elif not day_tasks:
            task_block = (
                "<p style='opacity:0.75;margin:0'>期限がこの日のタスクはありません。</p>"
                "<p style='opacity:0.65;margin:0.35rem 0 0 0;font-size:0.85rem'>"
                "期限なしのタスクはここには出ません。</p>"
            )
        else:
            task_block = _format_tasks_html(day_tasks, cal_tz)

        google_task_block = ""
        if gcal_configured:
            if google_tasks_err:
                google_task_block = (
                    f"<p style='opacity:0.85;margin:0'>Google タスクを取得できませんでした: "
                    f"{html.escape(google_tasks_err)}</p>"
                )
            elif not google_tasks:
                google_task_block = (
                    "<p style='opacity:0.75;margin:0'>期限がこの日の Google タスクはありません。</p>"
                )
            else:
                google_task_block = _format_tasks_html(google_tasks, gcal_tz)

        inner = (
            "<p style='margin:0 0 0.4rem 0;font-weight:600;opacity:0.95'>予定（カレンダー）</p>"
            + cal_block
            + "<p style='margin:1rem 0 0.4rem 0;font-weight:600;opacity:0.95'>タスク（Notion）</p>"
            + task_block
        )
        if gcal_configured:
            inner += (
                "<p style='margin:1rem 0 0.4rem 0;font-weight:600;opacity:0.95'>タスク（Google）</p>"
                + google_task_block
            )

        has_cal_list = bool(day_evs)
        has_task_list = (bool(day_tasks) and not tasks_err) or (bool(google_tasks) and not google_tasks_err)
        use_compact = (
            any_source_configured
            and not has_cal_list
            and not has_task_list
            and not tasks_err
        ) or (not any_source_configured)

        panel_class = "gt-day-events-panel"
        if use_compact:
            panel_class += " gt-day-events-panel--compact"

        st.markdown(
            f'<div class="{panel_class}">{inner}</div>',
            unsafe_allow_html=True,
        )

    dk = st.session_state["daily_date_input"].isoformat()
    entry = db.get_daily_entry(dk)

    day_label = "今日" if selected_date == today_local else "この日"
    st.markdown(
        f'<div class="section-header">■ {day_label}の仕事は何か？（手入力・Google 予定とは別）</div>',
        unsafe_allow_html=True,
    )

    schedule = entry["schedule"]

    st.markdown("**TIME / この日の予定 / ゴールイメージ / GIVEできる価値**")

    updated_schedule = []
    h1, h2, h3, h4 = st.columns([1, 3, 3, 2])
    h1.markdown("**TIME**")
    h2.markdown("**この日に予定されている仕事**")
    h3.markdown("**ゴールイメージ**")
    h4.markdown("**GIVEできる価値**")

    for i, row in enumerate(schedule):
        c1, c2, c3, c4 = st.columns([1, 3, 3, 2])
        with c1:
            st.markdown(f"**{row['time']}**")
        with c2:
            task = st.text_input(
                "task",
                value=row.get("task", ""),
                key=f"task_{dk}_{i}",
                label_visibility="collapsed",
            )
        with c3:
            goal = st.text_input(
                "goal",
                value=row.get("goal_image", ""),
                key=f"goal_{dk}_{i}",
                label_visibility="collapsed",
            )
        with c4:
            give = st.text_input(
                "give",
                value=row.get("give_value", ""),
                key=f"give_{dk}_{i}",
                label_visibility="collapsed",
            )
        updated_schedule.append(
            {"time": row["time"], "task": task, "goal_image": goal, "give_value": give}
        )

    entry["schedule"] = updated_schedule

    # ──────────────────────────────────────────────────────
    st.markdown('<div class="section-header">■ 今日の仕事における最高のイメージは何か？</div>', unsafe_allow_html=True)

    image_questions = [
        ("image_q1", "① 今日1日が終わった時に、どのような仕事ができれば年間・半年・四半期・今月・今週のゴールに確実に到達するか？"),
        ("image_q2", "② 今月・来月の成果を確かにする上で、今日実行しておく価値あるアクションは何か？"),
        ("image_q3", "③ アクションはどのくらい見えているか？見えていないところがあるとしたらそれは何か？"),
        ("image_q4", "④ チームメンバー・クライアントがびっくりするために、今日できることと工夫は何か？"),
        ("image_q5", "⑤ どのようにすれば見える？誰に相談すればさらに視界は良好になるか？"),
        ("image_q6", "⑥ 繰り返し行われる失敗は何か？どのようにしてそれらを防ぐか？"),
    ]

    col_left, col_right = st.columns(2)
    for idx, (key, label) in enumerate(image_questions):
        col = col_left if idx % 2 == 0 else col_right
        with col:
            st.markdown(f'<div class="static-label">{label}</div>', unsafe_allow_html=True)
            entry[key] = st.text_area(
                label, value=entry.get(key, ""),
                height=90, key=f"img_{dk}_{key}",
                label_visibility="collapsed",
            )

    # ──────────────────────────────────────────────────────
    st.markdown('<div class="section-header">■ どのようなアクションがあなたの人生の成功を加速させるか？</div>', unsafe_allow_html=True)

    actions = entry["actions"]
    updated_actions = []
    ah1, ah2, ah3, ah4 = st.columns([1, 3, 3, 3])
    ah1.markdown("**TIME**")
    ah2.markdown("**今日行うべきアクション**")
    ah3.markdown("**結果**")
    ah4.markdown("**次に活かせること（同じ失敗を2度と繰り返さないために）**")

    for i, row in enumerate(actions):
        a1, a2, a3, a4 = st.columns([1, 3, 3, 3])
        with a1:
            t = st.text_input(
                "time", value=row.get("time", ""), key=f"at_{dk}_{i}", label_visibility="collapsed"
            )
        with a2:
            a = st.text_input(
                "action", value=row.get("action", ""), key=f"aa_{dk}_{i}", label_visibility="collapsed"
            )
        with a3:
            r = st.text_input(
                "result", value=row.get("result", ""), key=f"ar_{dk}_{i}", label_visibility="collapsed"
            )
        with a4:
            n = st.text_input(
                "next", value=row.get("next_learning", ""), key=f"an_{dk}_{i}", label_visibility="collapsed"
            )
        updated_actions.append({"time": t, "action": a, "result": r, "next_learning": n})
    entry["actions"] = updated_actions

    # ──────────────────────────────────────────────────────
    with st.expander("■ 問題解決 ver（任意）"):
        problem_fields = [
            ("problem",                  "問題は何か？その問題を紙に書き出したら、いくつに分けることができるか？またそれは何か？"),
            ("problem_root",             "問題を引き起こしている問題は何か？"),
            ("problem_source",           "誰に相談すると解決するか？"),
            ("problem_research_internal","調べる価値ある情報は何か？※社内のトップパフォーマーは何をしているか？"),
            ("problem_solution",         "その人ならどのような解決策を出すか？"),
            ("problem_research_same",    "調べる価値ある情報は何か？※同業他社のトップパフォーマーは何をしているか？"),
            ("problem_absolute",         "どのようにすれば絶対に解決するか？"),
            ("problem_research_other",   "調べる価値ある情報は何か？※異業種のトップパフォーマーは何をしているか？"),
            ("problem_principle",        "その領域の原理原則、正しいやり方は何か？"),
            ("problem_premise",          "今の仕事における前提条件は何か？"),
            ("problem_blind_spot",       "どこを実践していて、どこを見落としているか？"),
            ("problem_prevention",       "今後、同じことを繰り返さないために、変えることは何か？（ステップ、ツール、トーク、言動）"),
        ]
        col_l, col_r = st.columns(2)
        for idx, (key, label) in enumerate(problem_fields):
            col = col_l if idx % 2 == 0 else col_r
            with col:
                st.markdown(f'<div class="static-label">{label}</div>', unsafe_allow_html=True)
                entry[key] = st.text_area(
                    label,
                    value=entry.get(key, ""),
                    height=80,
                    key=f"prob_{dk}_{key}",
                    label_visibility="collapsed",
                )

    # ──────────────────────────────────────────────────────
    with st.expander("■ 誰にどのようなメッセージを送るか？（任意）"):
        entry["message"] = st.text_area(
            "メッセージ",
            value=entry.get("message", ""),
            height=120,
            key=f"msg_{dk}",
            label_visibility="collapsed",
        )

    db.save_daily_entry(entry)


# ══════════════════════════════════════════════════════════
# 📖 過去の記録
# ══════════════════════════════════════════════════════════
elif page == "📖 過去の記録":
    st.title("📖 過去の記録")

    all_dates = db.get_all_entry_dates()
    if not all_dates:
        st.info("まだ記録がありません。「日次記録」から入力してください。")
        st.stop()

    selected = st.selectbox("日付を選択", all_dates, format_func=fmt_date)
    entry = db.get_daily_entry(selected)

    st.subheader(fmt_date(selected))

    # スケジュール
    st.markdown('<div class="section-header">■ 今日の仕事</div>', unsafe_allow_html=True)
    schedule_rows = [s for s in entry["schedule"] if s.get("task")]
    if schedule_rows:
        df = pd.DataFrame(schedule_rows)
        df.columns = ["TIME", "仕事", "ゴールイメージ", "GIVEできる価値"]
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("（記録なし）")

    # 最高イメージ
    st.markdown('<div class="section-header">■ 今日のイメージ</div>', unsafe_allow_html=True)
    image_labels = [
        "① ゴールに到達するために今日できること",
        "② 今日実行しておく価値あるアクション",
        "③ アクションの見え方",
        "④ 驚かせるための工夫",
        "⑤ 視界を広げる方法",
        "⑥ 繰り返す失敗とその防止策",
    ]
    col_l, col_r = st.columns(2)
    for idx, (label, key) in enumerate(zip(image_labels,
            ["image_q1","image_q2","image_q3","image_q4","image_q5","image_q6"])):
        col = col_l if idx % 2 == 0 else col_r
        with col:
            val = entry.get(key, "")
            if val:
                st.markdown(f"**{label}**")
                st.write(val)

    # アクション
    action_rows = [a for a in entry["actions"] if a.get("action")]
    if action_rows:
        st.markdown('<div class="section-header">■ アクション記録</div>', unsafe_allow_html=True)
        df2 = pd.DataFrame(action_rows)
        df2.columns = ["TIME", "アクション", "結果", "次に活かすこと"]
        st.dataframe(df2, use_container_width=True, hide_index=True)

    # 問題解決
    prob = entry.get("problem", "")
    if prob:
        st.markdown('<div class="section-header">■ 問題解決</div>', unsafe_allow_html=True)
        problem_labels = {
            "problem": "問題", "problem_root": "根本原因",
            "problem_source": "相談相手", "problem_solution": "解決策",
            "problem_absolute": "絶対的解決方法", "problem_principle": "原理原則",
            "problem_blind_spot": "見落とし", "problem_prevention": "再発防止",
        }
        col_l, col_r = st.columns(2)
        for idx, (key, label) in enumerate(problem_labels.items()):
            val = entry.get(key, "")
            if val:
                col = col_l if idx % 2 == 0 else col_r
                with col:
                    st.markdown(f"**{label}**")
                    st.write(val)

    # メッセージ
    if entry.get("message"):
        st.markdown('<div class="section-header">■ メッセージ</div>', unsafe_allow_html=True)
        st.write(entry["message"])

    # 編集ボタン
    st.markdown("---")
    if st.button("✏️ この日を編集する"):
        st.session_state["edit_date"] = selected
        st.info("「日次記録」ページに移動して、日付をこの日に変更してください。")


# ══════════════════════════════════════════════════════════
# 📊 四半期目標
# ══════════════════════════════════════════════════════════
elif page == "📊 四半期目標":
    st.title("📊 四半期目標")

    year_now, q_now = current_quarter(date.today())
    col1, col2 = st.columns(2)
    with col1:
        year = st.number_input("年", value=year_now, min_value=2020, max_value=2030, step=1)
    with col2:
        quarter = st.selectbox("四半期", [1, 2, 3, 4], index=q_now - 1,
                               format_func=lambda q: f"Q{q} ({(q-1)*3+1}〜{(q-1)*3+3}月)")

    months = quarter_months(year, quarter)
    goals = db.get_quarterly_goals(year, quarter)

    st.markdown('<div class="section-header">■ 四半期の目的と目標</div>', unsafe_allow_html=True)

    goals["intention"] = st.text_area(
        "この四半期、何を意図して仕事をするか？",
        value=goals.get("intention", ""), height=100
    )

    _spacer, c1, c2, c3, _trail = st.columns([2, 4, 4, 4, 0.5])
    for i, (col, month) in enumerate(zip([c1, c2, c3], months)):
        with col:
            key = f"month{i+1}_theme"
            goals[key] = st.text_area(
                f"{month}月：この月、何を意図すると価値があるか？",
                value=goals.get(key, ""), height=80
            )

    # KGI/KPI テーブル
    st.markdown('<div class="section-header">■ KGI / KPI</div>', unsafe_allow_html=True)

    kpi_rows = db.get_quarterly_kpi(year, quarter)
    if not kpi_rows:
        kpi_rows = (
            [{"type": "KGI", "label": "", "month1_goal": "", "month1_result": "",
              "month2_goal": "", "month2_result": "", "month3_goal": "", "month3_result": ""}]
            + [{"type": "KPI", "label": "", "month1_goal": "", "month1_result": "",
                "month2_goal": "", "month2_result": "", "month3_goal": "", "month3_result": ""} for _ in range(4)]
        )

    updated_kpi = []
    delete_index = None
    header = st.columns([1, 1, 2, 2, 2, 2, 2, 2, 0.5])
    for col, label in zip(header, ["種別", "項目", f"{months[0]}月 目標", f"{months[0]}月 結果",
                                    f"{months[1]}月 目標", f"{months[1]}月 結果",
                                    f"{months[2]}月 目標", f"{months[2]}月 結果", ""]):
        col.markdown(f"**{label}**")

    for i, row in enumerate(kpi_rows):
        cols = st.columns([1, 1, 2, 2, 2, 2, 2, 2, 0.5])
        r_type = cols[0].selectbox("type", ["KGI", "KPI"], index=0 if row.get("type") == "KGI" else 1,
                                   key=f"ktype_{i}", label_visibility="collapsed")
        label = cols[1].text_input("label", value=row.get("label", ""), key=f"klabel_{i}", label_visibility="collapsed")
        m1g = cols[2].text_input("m1g", value=row.get("month1_goal", ""), key=f"m1g_{i}", label_visibility="collapsed")
        m1r = cols[3].text_input("m1r", value=row.get("month1_result", ""), key=f"m1r_{i}", label_visibility="collapsed")
        m2g = cols[4].text_input("m2g", value=row.get("month2_goal", ""), key=f"m2g_{i}", label_visibility="collapsed")
        m2r = cols[5].text_input("m2r", value=row.get("month2_result", ""), key=f"m2r_{i}", label_visibility="collapsed")
        m3g = cols[6].text_input("m3g", value=row.get("month3_goal", ""), key=f"m3g_{i}", label_visibility="collapsed")
        m3r = cols[7].text_input("m3r", value=row.get("month3_result", ""), key=f"m3r_{i}", label_visibility="collapsed")
        if cols[8].button("×", key=f"del_{i}"):
            delete_index = i
        updated_kpi.append({
            "type": r_type, "label": label,
            "month1_goal": m1g, "month1_result": m1r,
            "month2_goal": m2g, "month2_result": m2r,
            "month3_goal": m3g, "month3_result": m3r,
        })

    if delete_index is not None:
        updated_kpi.pop(delete_index)
        db.save_quarterly_goals(year, quarter, goals)
        db.save_quarterly_kpi(year, quarter, updated_kpi)
        st.rerun()

    if st.button("行を追加"):
        updated_kpi.append({"type": "KPI", "label": "", "month1_goal": "", "month1_result": "",
                             "month2_goal": "", "month2_result": "", "month3_goal": "", "month3_result": ""})
        db.save_quarterly_goals(year, quarter, goals)
        db.save_quarterly_kpi(year, quarter, updated_kpi)
        st.rerun()

    db.save_quarterly_goals(year, quarter, goals)
    db.save_quarterly_kpi(year, quarter, updated_kpi)


# ══════════════════════════════════════════════════════════
# 🏆 ライフミッション
# ══════════════════════════════════════════════════════════
elif page == "🏆 ライフミッション":
    st.title("🏆 ライフミッション・年間目標")
    st.caption("毎日読むセクション。人生の羅針盤として活用してください。")

    mission = db.get_life_mission()

    st.markdown('<div class="section-header">■ あなたの人生・仕事における目的と目標は何か？（毎日読む）</div>', unsafe_allow_html=True)

    fields_left = [
        ("mission",          "あなたの人生のミッションは何か？"),
        ("legacy",           "あなたは死んだ後、どのような人として記憶されたいか？"),
        ("values",           "あなたの価値観は何か？（頼まれてもいないのにしてしまうことは何か？）"),
        ("assets_now",       "あなたの総資産は現在いくらか？"),
        ("assets_at_60",     "あなたの総資産は六十歳までにいくらになっていると価値があるか？"),
        ("assets_this_year", "この1年間でいくらまでになっていると価値があるか？"),
    ]
    fields_right = [
        ("work_purpose",     "あなたの仕事をする目的は何か？"),
        ("goal_5years",      "5年後の目標は何か？"),
        ("goal_1year",       "この1年間の目標は何か？"),
        ("goal_1year_why",   "この1年間あなたはなぜこの目標に到達する必要があるのか？目的は何か？"),
        ("goal_1year_who",   "あなたが目標に到達することは、誰のどのような役に立つことができるか？"),
        ("goal_1year_without","その目標が手に入らないと、どのような社絶な人生を想像することができるか？"),
    ]

    col_l, col_r = st.columns(2)
    for key, label in fields_left:
        with col_l:
            st.markdown(f'<div class="static-label">{label}</div>', unsafe_allow_html=True)
            mission[key] = st.text_area(label, value=mission.get(key, ""), height=150,
                                        key=f"lm_{key}", label_visibility="collapsed")
    for key, label in fields_right:
        with col_r:
            st.markdown(f'<div class="static-label">{label}</div>', unsafe_allow_html=True)
            mission[key] = st.text_area(label, value=mission.get(key, ""), height=150,
                                        key=f"lm_{key}", label_visibility="collapsed")

    db.save_life_mission(mission)


# ══════════════════════════════════════════════════════════
# 📈 分析
# ══════════════════════════════════════════════════════════
elif page == "📈 分析":
    st.title("📈 分析")

    all_dates = db.get_all_entry_dates()
    if len(all_dates) < 2:
        st.info("分析には2日以上のデータが必要です。")
        st.stop()

    analysis = st.radio(
        "分析タイプ",
        ["📅 記録カレンダー", "🔄 回答の変遷", "🔎 キーワード検索"],
        horizontal=True,
    )

    if analysis == "📅 記録カレンダー":
        st.subheader("記録カレンダー")
        df = pd.DataFrame({"date": pd.to_datetime(all_dates)})
        df["count"] = 1
        df["week"] = df["date"].dt.isocalendar().week
        df["year"] = df["date"].dt.year
        df["weekday"] = df["date"].dt.weekday
        df["month"] = df["date"].dt.month

        fig = px.scatter(
            df, x="date", y=[1] * len(df),
            color_discrete_sequence=["#4CAF50"],
            title=f"記録日（計{len(all_dates)}日）",
        )
        fig.update_traces(marker=dict(size=12, symbol="square"))
        fig.update_layout(
            yaxis=dict(visible=False),
            xaxis_title="",
            height=200,
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

        # 月別集計
        df["year_month"] = df["date"].dt.to_period("M").astype(str)
        monthly = df.groupby("year_month").size().reset_index(name="記録数")
        fig2 = px.bar(monthly, x="year_month", y="記録数", title="月別記録数",
                      color_discrete_sequence=["#2196F3"])
        st.plotly_chart(fig2, use_container_width=True)

    elif analysis == "🔄 回答の変遷":
        field_map = {
            "① ゴールに到達するために今日できること": "image_q1",
            "② 今日実行するアクション": "image_q2",
            "③ アクションの見え方": "image_q3",
            "④ 驚かせるための工夫": "image_q4",
            "⑤ 視界を広げる方法": "image_q5",
            "⑥ 繰り返す失敗と防止策": "image_q6",
            "問題": "problem",
            "問題の根本原因": "problem_root",
            "再発防止策": "problem_prevention",
        }
        selected_field_name = st.selectbox("フィールドを選択", list(field_map.keys()))
        field_key = field_map[selected_field_name]

        entries = db.get_entries_range(all_dates[-1], all_dates[0])
        results = [(e["date"], e.get(field_key, "")) for e in entries if e.get(field_key, "").strip()]

        if results:
            st.markdown(f"### 「{selected_field_name}」の変遷（{len(results)}件）")
            for d, val in results:
                with st.expander(fmt_date(d), expanded=False):
                    st.write(val)
        else:
            st.info("この項目の記録がありません。")

    elif analysis == "🔎 キーワード検索":
        query = st.text_input("キーワードを入力", placeholder="例: ツール開発、AI事業、問題...")
        if query:
            entries = db.get_entries_range(all_dates[-1], all_dates[0])
            hits = []
            for e in entries:
                matched = []
                # スケジュールを検索
                for s in e.get("schedule", []):
                    for field in ["task", "goal_image", "give_value"]:
                        val = s.get(field, "")
                        if query.lower() in val.lower():
                            matched.append(f"[{s['time']}] {val}")
                # テキストフィールドを検索
                for key in ["image_q1","image_q2","image_q3","image_q4","image_q5","image_q6",
                            "problem","problem_root","problem_solution","problem_prevention","message"]:
                    val = e.get(key, "")
                    if val and query.lower() in val.lower():
                        matched.append(val[:100])
                if matched:
                    hits.append({"date": e["date"], "matches": matched})

            if hits:
                st.success(f"{len(hits)} 日でヒット")
                for hit in hits:
                    with st.expander(fmt_date(hit["date"])):
                        for m in hit["matches"]:
                            st.markdown(f"• {m.replace(query, f'**{query}**')}")
            else:
                st.info(f"「{query}」は見つかりませんでした。")
