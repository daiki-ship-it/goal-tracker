from __future__ import annotations

import calendar
import html
import os
from datetime import datetime, date, timedelta, timezone

import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

load_dotenv(override=True)
import database as db


def _env_flag_true(name: str, *, default: str = "0") -> bool:
    """環境変数が 1 / true / yes / on（大小無視）なら True。未設定は default に従う。"""
    v = os.environ.get(name, default)
    if v is None:
        v = default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _notion_web_url(database_id: str | None, url_override: str | None) -> str | None:
    """連携先 Notion DB をブラウザで開く URL。url_override（http/https）があれば優先。"""
    o = (url_override or "").strip()
    if o.startswith(("http://", "https://")):
        return o
    d = (database_id or "").strip()
    if not d:
        return None
    hex_id = d.replace("-", "").strip()
    if len(hex_id) != 32:
        return None
    try:
        int(hex_id, 16)
    except ValueError:
        return None
    return f"https://www.notion.so/{hex_id}"


def _notion_section_header_row_html(title: str, notion_url: str | None, *, margin_top: str) -> str:
    """予定・タスクパネル内の小見出し行（任意で Notion リンク）。"""
    title_esc = html.escape(title)
    if not notion_url:
        return (
            f"<p style='margin:{margin_top} 0 0.4rem 0;font-weight:600;opacity:0.95'>{title_esc}</p>"
        )
    u = html.escape(notion_url, quote=True)
    return (
        f'<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;'
        f"gap:0.35rem;margin:{margin_top} 0 0.4rem 0\">"
        f'<span style="font-weight:600;opacity:0.95">{title_esc}</span>'
        f'<a href="{u}" target="_blank" rel="noopener noreferrer" '
        'style="display:inline-block;font-size:0.78rem;font-weight:600;text-decoration:none;'
        "padding:0.2rem 0.55rem;border-radius:6px;border:1px solid rgba(255,255,255,0.28);"
        'color:inherit;opacity:0.95">Notionで開く</a>'
        f"</div>"
    )

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
    creds, auth_err = gcc.get_credentials(interactive=False)
    if creds is None:
        return [], auth_err
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
    creds, auth_err = gcc.get_credentials(interactive=False)
    if creds is None:
        return [], auth_err
    try:
        tsvc = gcc.build_tasks_service(creds)
        tz = ZoneInfo(tz_name)
        return gcc.fetch_tasks_for_calendar_day_all_lists(tsvc, date.fromisoformat(day_iso), tz)
    except HttpError as e:
        import google_calendar_client as gcc2
        return [], gcc2.fetch_tasks_http_error_message(e)
    except Exception as e:
        return [], str(e)


@st.cache_data(ttl=60)
def _cached_fetch_ai_launch_tasks(
    database_id: str,
    date_from_iso: str,
    date_prop: str,
    status_prop: str | None,
    token_hash: str,
    memo_prop: str | None = None,
    parent_prop: str | None = None,
    progress_prop: str | None = None,
) -> tuple[list[dict], str | None]:
    """Notion タスク DB から date_from 以降の全タスクをキャッシュ付きで取得。
    memo_prop / parent_prop / progress_prop を指定すると各フィールドも含めて返す。"""
    import notion_calendar_client as ncc

    _ = token_hash
    return ncc.fetch_upcoming_tasks(
        database_id,
        date.fromisoformat(date_from_iso),
        date_prop,
        status_prop,
        memo_prop=memo_prop,
        parent_prop=parent_prop,
        progress_prop=progress_prop,
    )


def _format_tasks_with_deadlines_html(tasks: list[dict]) -> str:
    """タスクリストを締切日付きで HTML 化。"""
    if not tasks:
        return ""
    parts: list[str] = []
    for t in tasks:
        title = html.escape(t.get("title") or "")
        deadline_iso = t.get("deadline_iso")
        date_label = ""
        if deadline_iso:
            try:
                d = date.fromisoformat(deadline_iso)
                date_label = (
                    f"<span style='opacity:0.65;font-size:0.85rem;"
                    f"margin-right:0.4rem'>{d.month}/{d.day}</span>"
                )
            except ValueError:
                pass
        if t.get("status") == "completed":
            parts.append(
                f"<li style='margin:0.35rem 0;opacity:0.55'>{date_label}<del>{title}</del></li>"
            )
        else:
            parts.append(f"<li style='margin:0.35rem 0'>{date_label}{title}</li>")
    return "<ul style='margin:0;padding-left:1.15rem'>" + "".join(parts) + "</ul>"


def _format_tasks_hierarchical_html(tasks: list[dict]) -> str:
    """タスクリストを親子階層・進捗・メモ付きで HTML 化。

    - 大タスク（parent_id=None または親が範囲外）をトップレベルで表示
    - 中タスクは左ボーダーつき入れ子リストで表示
    - 完了タスクは取り消し線 + opacity:0.65
    - メモは未完了タスクのみ、2行 line-clamp で表示
    - 親には完了数/全数を小さく表示
    - 親が範囲外の孤立した子は「（親タスクは表示範囲外）」グループにまとめる
    """
    if not tasks:
        return ""

    all_ids = {t["id"] for t in tasks if t.get("id")}

    # parent_id → 子タスクリスト
    children_map: dict[str, list[dict]] = {}
    top_level: list[dict] = []
    orphans: list[dict] = []

    for t in tasks:
        pid = t.get("parent_id")
        if pid:
            if pid in all_ids:
                children_map.setdefault(pid, []).append(t)
            else:
                orphans.append(t)
        else:
            top_level.append(t)

    def _date_label(deadline_iso: str | None) -> str:
        if not deadline_iso:
            return ""
        try:
            d = date.fromisoformat(deadline_iso)
            return (
                f"<span style='opacity:0.65;font-size:0.82rem;"
                f"margin-right:0.3rem'>{d.month}/{d.day}</span>"
            )
        except ValueError:
            return ""

    def _render_task_li(t: dict, *, is_child: bool = False) -> str:
        title = html.escape(t.get("title") or "")
        dl = _date_label(t.get("deadline_iso"))
        completed = t.get("status") == "completed"

        indent_marker = (
            "<span style='opacity:0.4;margin-right:0.2rem'>↳</span>" if is_child else ""
        )
        font_size = "font-size:0.9rem;" if is_child else "font-weight:600;font-size:0.95rem;"
        opacity = "opacity:0.65;" if completed else ""
        title_html = f"<del>{title}</del>" if completed else title

        memo_html = ""
        if not completed:
            raw_memo = t.get("memo") or ""
            if raw_memo:
                memo_escaped = html.escape(raw_memo)
                memo_html = (
                    f"<div style='color:rgba(128,128,128,0.8);font-size:0.78rem;"
                    f"line-height:1.35;margin-top:0.1rem;padding-left:0.5rem;"
                    f"display:-webkit-box;-webkit-line-clamp:2;"
                    f"-webkit-box-orient:vertical;overflow:hidden'>"
                    f"{memo_escaped}</div>"
                )

        return (
            f"<li style='margin:0.2rem 0;{font_size}{opacity}'>"
            f"{indent_marker}{dl}{title_html}{memo_html}</li>"
        )

    def _render_parent_with_children(t: dict) -> str:
        tid = t.get("id", "")
        kids = sorted(
            children_map.get(tid, []),
            key=lambda x: x.get("deadline_iso") or "",
        )

        # Notion の進捗プロパティをそのまま表示（0.0〜1.0 → 例: 25%）
        progress_html = ""
        raw_progress = t.get("progress")
        if raw_progress is not None:
            pct = int(round(raw_progress * 100)) if raw_progress <= 1.0 else int(round(raw_progress))
            progress_html = (
                f"<span style='opacity:0.55;font-size:0.78rem;"
                f"margin-left:0.4rem'>{pct}%</span>"
            )

        title = html.escape(t.get("title") or "")
        dl = _date_label(t.get("deadline_iso"))
        completed = t.get("status") == "completed"
        opacity = "opacity:0.65;" if completed else ""
        title_html = f"<del>{title}</del>" if completed else title

        parent_li = (
            f"<li style='margin:0.5rem 0 0.15rem;font-weight:600;"
            f"font-size:0.95rem;{opacity}'>"
            f"{dl}{title_html}{progress_html}</li>"
        )

        if not kids:
            return parent_li

        child_items = "".join(_render_task_li(k, is_child=True) for k in kids)
        child_ul = (
            f"<ul style='list-style:none;padding-left:1rem;margin:0 0 0.4rem;"
            f"border-left:2px solid rgba(128,128,128,0.2)'>"
            f"{child_items}</ul>"
        )
        return parent_li + child_ul

    parts: list[str] = []

    # トップレベルタスク（子を持つ場合はまとめて描画）
    top_sorted = sorted(top_level, key=lambda x: x.get("deadline_iso") or "")
    for t in top_sorted:
        parts.append(_render_parent_with_children(t))

    # 孤立した子タスク（親が表示範囲外）
    if orphans:
        orphans_sorted = sorted(orphans, key=lambda x: x.get("deadline_iso") or "")
        orphan_items = "".join(_render_task_li(o, is_child=True) for o in orphans_sorted)
        parts.append(
            "<li style='margin:0.5rem 0 0.15rem;font-size:0.82rem;opacity:0.5'>"
            "（親タスクは表示範囲外）</li>"
            f"<ul style='list-style:none;padding-left:1rem;margin:0 0 0.4rem;"
            f"border-left:2px solid rgba(128,128,128,0.15)'>{orphan_items}</ul>"
        )

    return (
        "<ul style='list-style:none;padding:0;margin:0'>"
        + "".join(parts)
        + "</ul>"
    )


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
    on_nav=None,
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
                    if on_nav:
                        on_nav()
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
/* スケジュール→アクション追加ボタン */
[data-testid="stButton"] button[kind="secondary"]:has(span:contains("＋")) {
    padding: 2px 6px;
    font-size: 1rem;
    line-height: 1;
    min-height: unset;
    height: 32px;
    background: rgba(245, 200, 66, 0.15);
    border: 1px solid rgba(245, 200, 66, 0.6);
    color: #f5c842;
    border-radius: 4px;
}
</style>
""", unsafe_allow_html=True)


def _inject_textarea_autoresize() -> None:
    """テキストエリアをコンテンツに応じて自動リサイズするJSを注入。同じ行の全セルを同一高さに揃える。"""
    components.html(
        """
        <script>
        (function() {
            const doc = window.parent.document;
            const win = window.parent;

            // 以前のタイマー・オブザーバーをクリアして蓄積を防ぐ
            if (win._gt_ar_timer != null) {
                win.clearInterval(win._gt_ar_timer);
                win._gt_ar_timer = null;
            }
            if (win._gt_ar_obs != null) {
                win._gt_ar_obs.disconnect();
                win._gt_ar_obs = null;
            }

            function getRowTextareas(el) {
                const row = el.closest('[data-testid="stHorizontalBlock"]');
                return row ? Array.from(row.querySelectorAll('textarea')) : [el];
            }

            // テキストエリアの自然な高さを計測（height=0px にして scrollHeight を読む）
            // JS内の連続した DOM 変更はブラウザが1フレームにまとめるため視覚的ちらつきは発生しない
            function measureHeight(t) {
                const savedH = t.style.height;
                const savedOF = t.style.overflow;
                t.style.overflow = 'hidden';
                t.style.height = '0px';
                const h = Math.max(t.scrollHeight, 48);
                t.style.height = savedH;
                t.style.overflow = savedOF;
                return h;
            }

            function resizeRow(trigger) {
                const textareas = getRowTextareas(trigger);
                if (!textareas.length) return;

                // 全テキストエリアの高さを計測し、行の最大値に揃える
                const heights = textareas.map(measureHeight);
                const maxH = Math.max.apply(null, heights);
                textareas.forEach(function(t) {
                    t.style.overflow = 'hidden';
                    t.style.resize = 'none';
                    t.style.height = maxH + 'px';
                });
            }

            function setup() {
                doc.querySelectorAll('textarea').forEach(function(el) {
                    if (el.dataset.arInit) return;
                    el.dataset.arInit = '1';
                    resizeRow(el);
                    el.addEventListener('input', function() { resizeRow(el); });
                });
            }

            // MutationObserver で Streamlit 再レンダリング後のテキストエリア追加を即座に検知
            win._gt_ar_obs = new MutationObserver(function(mutations) {
                var needSetup = false;
                mutations.forEach(function(m) {
                    m.addedNodes.forEach(function(n) {
                        if (n.nodeType === 1 && (
                            n.tagName === 'TEXTAREA' ||
                            (n.querySelector && n.querySelector('textarea'))
                        )) {
                            needSetup = true;
                        }
                    });
                });
                if (needSetup) setup();
            });
            win._gt_ar_obs.observe(doc.body, { childList: true, subtree: true });

            setup();
            // フォールバック: MutationObserver が拾いきれないケースに備えてポーリング
            win._gt_ar_timer = win.setInterval(setup, 1000);
        })();
        </script>
        """,
        height=0,
    )


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


def _time_sort_key(row: dict) -> float:
    t = row.get("time", "").strip().replace("：", ":")
    if not t:
        return float("inf")
    try:
        parts = t.split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        if h == 0:
            h = 24
        return h * 60 + m
    except Exception:
        return float("inf")


def _save_daily_for_date(dk: str) -> None:
    """session_state から指定日の日次記録を読み取って SQLite に保存する。
    ページ切り替え時など、日次記録ブロックが実行されないケースに対応。"""
    try:
        entry = db.get_daily_entry(dk)

        schedule_rows = entry.get("schedule", [])
        updated_schedule = []
        for i, row in enumerate(schedule_rows):
            updated_schedule.append({
                "time": row["time"],
                "task": st.session_state.get(f"task_{dk}_{i}", row.get("task", "")),
                "goal_image": st.session_state.get(f"goal_{dk}_{i}", row.get("goal_image", "")),
                "give_value": st.session_state.get(f"give_{dk}_{i}", row.get("give_value", "")),
            })
        entry["schedule"] = updated_schedule

        for img_key in ["image_q1", "image_q2", "image_q3", "image_q4", "image_q5", "image_q6"]:
            sk = f"img_{dk}_{img_key}"
            if sk in st.session_state:
                entry[img_key] = st.session_state[sk]

        actions_rows = entry.get("actions", [])
        updated_actions = []
        for i, row in enumerate(actions_rows):
            updated_actions.append({
                "time": st.session_state.get(f"at_{dk}_{i}", row.get("time", "")),
                "action": st.session_state.get(f"aa_{dk}_{i}", row.get("action", "")),
                "result": st.session_state.get(f"ar_{dk}_{i}", row.get("result", "")),
                "next_learning": st.session_state.get(f"an_{dk}_{i}", row.get("next_learning", "")),
            })
        entry["actions"] = sorted(updated_actions, key=_time_sort_key)

        for prob_key in [
            "problem", "problem_root", "problem_source", "problem_research_internal",
            "problem_solution", "problem_research_same", "problem_absolute",
            "problem_research_other", "problem_principle", "problem_premise",
            "problem_blind_spot", "problem_prevention",
        ]:
            sk = f"prob_{dk}_{prob_key}"
            if sk in st.session_state:
                entry[prob_key] = st.session_state[sk]

        if f"msg_{dk}" in st.session_state:
            entry["message"] = st.session_state[f"msg_{dk}"]

        db.save_daily_entry(entry)
    except Exception:
        pass


def _save_life_mission_for_session() -> None:
    """session_state の lm_* キーからライフミッションを SQLite に保存する。
    ページ切り替え時など、ライフミッションブロックが実行されないケースに対応。"""
    try:
        mission = db.get_life_mission()
        for lm_key in [
            "mission", "legacy", "values", "assets_now", "assets_at_60", "assets_this_year",
            "work_purpose", "goal_5years", "goal_1year", "goal_1year_why",
            "goal_1year_who", "goal_1year_without",
        ]:
            sk = f"lm_{lm_key}"
            if sk in st.session_state:
                mission[lm_key] = st.session_state[sk]
        db.save_life_mission(mission)
    except Exception:
        pass


def _save_quarterly_for_session() -> None:
    """session_state の q_* / ktype_* / m*g_* / m*r_* キーから四半期目標を SQLite に保存する。
    ページ切り替え時など、四半期目標ブロックが実行されないケースに対応。"""
    try:
        year = st.session_state.get("q_year_sel")
        quarter = st.session_state.get("q_quarter_sel")
        if year is None or quarter is None:
            return
        year = int(year)
        quarter = int(quarter)

        goals = db.get_quarterly_goals(year, quarter)
        for field in ["intention", "month1_theme", "month2_theme", "month3_theme", "kpi_memo"]:
            sk = f"q_{field}"
            if sk in st.session_state:
                goals[field] = st.session_state[sk]

        kpi_rows = db.get_quarterly_kpi(year, quarter)
        updated_kpi = []
        for i, row in enumerate(kpi_rows):
            updated_kpi.append({
                "type": st.session_state.get(f"ktype_{i}", row.get("type", "KPI")),
                "label": row.get("label", ""),
                "month1_goal":   st.session_state.get(f"m1g_{i}", row.get("month1_goal", "")),
                "month1_result": st.session_state.get(f"m1r_{i}", row.get("month1_result", "")),
                "month2_goal":   st.session_state.get(f"m2g_{i}", row.get("month2_goal", "")),
                "month2_result": st.session_state.get(f"m2r_{i}", row.get("month2_result", "")),
                "month3_goal":   st.session_state.get(f"m3g_{i}", row.get("month3_goal", "")),
                "month3_result": st.session_state.get(f"m3r_{i}", row.get("month3_result", "")),
            })

        db.save_quarterly_goals(year, quarter, goals)
        db.save_quarterly_kpi(year, quarter, updated_kpi)
    except Exception:
        pass


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


# ─── ページ切り替え検知：前ページのデータを自動保存 ─────────────
_prev_page = st.session_state.get("_current_page")
if _prev_page is not None and _prev_page != page:
    if _prev_page == "📝 日次記録":
        _dk_prev = st.session_state.get("daily_date_input")
        if _dk_prev is not None:
            _dk_str = _dk_prev.isoformat() if hasattr(_dk_prev, "isoformat") else str(_dk_prev)
            _save_daily_for_date(_dk_str)
    elif _prev_page == "🏆 ライフミッション":
        _save_life_mission_for_session()
    elif _prev_page == "📊 四半期目標":
        _save_quarterly_for_session()
st.session_state["_current_page"] = page


# ══════════════════════════════════════════════════════════
# 📝 日次記録
# ══════════════════════════════════════════════════════════
if page == "📝 日次記録":
    import notion_calendar_client as ncc

    tz_name = os.environ.get("NOTION_CALENDAR_TZ", "Asia/Tokyo")
    cal_tz = ZoneInfo(tz_name)
    ai_launch_db_id = os.environ.get("NOTION_AI_LAUNCH_DATABASE_ID", "").strip()
    ai_launch_date_prop = os.environ.get("NOTION_AI_LAUNCH_DATE_PROPERTY", "締切")
    ai_launch_status_prop = os.environ.get("NOTION_AI_LAUNCH_STATUS_PROPERTY", "").strip() or None
    ai_launch_memo_prop = os.environ.get("NOTION_AI_LAUNCH_MEMO_PROPERTY", "メモ").strip() or None
    ai_launch_parent_prop = os.environ.get("NOTION_AI_LAUNCH_PARENT_PROPERTY", "親アイテム").strip() or None
    ai_launch_progress_prop = os.environ.get("NOTION_AI_LAUNCH_PROGRESS_PROPERTY", "進捗").strip() or None
    ai_launch_web_url_override = os.environ.get("NOTION_AI_LAUNCH_WEB_URL", "").strip() or None
    tasks_db_id = os.environ.get("NOTION_TASKS_DATABASE_ID", "").strip()
    tasks_web_url_override = os.environ.get("NOTION_TASKS_WEB_URL", "").strip() or None
    tasks_date_prop = os.environ.get("NOTION_TASKS_DATE_PROPERTY", "Due")
    tasks_status_prop = os.environ.get("NOTION_TASKS_STATUS_PROPERTY", "").strip() or None
    tasks_memo_prop = os.environ.get("NOTION_TASKS_MEMO_PROPERTY", "メモ").strip() or None
    tasks_parent_prop = os.environ.get("NOTION_TASKS_PARENT_PROPERTY", "親アイテム").strip() or None
    tasks_progress_prop = os.environ.get("NOTION_TASKS_PROGRESS_PROPERTY", "進捗").strip() or None
    notion_token = ncc.get_token()
    token_hash = str(hash(notion_token or ""))
    ai_launch_notion_url = _notion_web_url(ai_launch_db_id or None, ai_launch_web_url_override)
    tasks_notion_url = _notion_web_url(tasks_db_id or None, tasks_web_url_override)

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

    # entryを読み込む
    dk = st.session_state["daily_date_input"].isoformat()
    entry = db.get_daily_entry(dk)

    st.title(f"📝 {fmt_date(selected_date.isoformat())}")
    if selected_date != today_local:
        st.info(
            f"**編集中の日付:** {selected_date.year}年{selected_date.month}月{selected_date.day}日（今日ではありません）"
        )

    col_cal, col_ev = st.columns([1, 1], gap="large")

    month_events: list[dict] = []

    with col_cal:
        nav1, nav2, nav3 = st.columns([1, 2.2, 1])
        with nav1:
            if st.button("◀", help="前の月（表示のみ）", key="cal_nav_prev"):
                _save_daily_for_date(dk)
                if cm == 1:
                    st.session_state["cal_view_y"] = cy - 1
                    st.session_state["cal_view_m"] = 12
                else:
                    st.session_state["cal_view_m"] = cm - 1
                st.rerun()
        with nav3:
            if st.button("▶", help="次の月（表示のみ）", key="cal_nav_next"):
                _save_daily_for_date(dk)
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

        gcal_month_errors: list[tuple[str, str]] = []
        if gcal_configured:
            months_needed_g = {(cy, cm), (selected_date.year, selected_date.month)}
            gcal_evs, gcal_month_errors = _fetch_google_months_events(
                gcal_ids, months_needed_g, gcal_tz, gcal_cred_mtime
            )
            month_events = _sort_events_by_start(gcal_evs)

        _render_month_day_grid(cy, cm, selected_date, today_local, on_nav=lambda: _save_daily_for_date(dk))

        with st.expander("日付を直接指定", expanded=False):
            st.date_input(
                "対象日",
                key="daily_date_input",
                label_visibility="collapsed",
                on_change=_on_daily_date_change,
            )

        for gcal_err_cid, gcal_err_msg in gcal_month_errors:
            st.warning(f"Google カレンダー ({_calendar_display_label(gcal_err_cid)}): {gcal_err_msg}")

    with col_ev:
        # Google Calendar イベント（選択日）
        day_evs = (
            _events_for_day(month_events, selected_date, gcal_tz)
            if gcal_configured
            else []
        )

        # AIローンチ関連タスク（締切が本日以降の全件）
        ai_launch_tasks: list[dict] = []
        ai_launch_err: str | None = None
        if notion_token and ai_launch_db_id:
            ai_launch_tasks, ai_launch_err = _cached_fetch_ai_launch_tasks(
                ai_launch_db_id, today_local.isoformat(), ai_launch_date_prop, ai_launch_status_prop, token_hash,
                memo_prop=ai_launch_memo_prop, parent_prop=ai_launch_parent_prop,
                progress_prop=ai_launch_progress_prop,
            )

        # 個人タスク関連（締切が本日以降の全件）
        day_tasks: list[dict] = []
        tasks_err: str | None = None
        if notion_token and tasks_db_id:
            day_tasks, tasks_err = _cached_fetch_ai_launch_tasks(
                tasks_db_id, today_local.isoformat(), tasks_date_prop, tasks_status_prop, token_hash,
                memo_prop=tasks_memo_prop, parent_prop=tasks_parent_prop,
                progress_prop=tasks_progress_prop,
            )

        st.markdown("**予定・タスク**")
        st.caption(
            "このアプリの日付・タイムゾーン基準で表示します。端末のカレンダーと日付がずれることがあります。"
        )
        if tasks_err:
            st.warning(tasks_err, icon="⚠️")
        if ai_launch_err:
            st.warning(ai_launch_err, icon="⚠️")

        # 予定（Google カレンダー）
        if not gcal_configured:
            cal_block = (
                "<p style='opacity:0.75;margin:0'>Google カレンダーの認証情報がありません"
                "（credentials.json / token.json を確認してください）。</p>"
            )
        elif not day_evs:
            cal_block = "<p style='opacity:0.75;margin:0'>この日に表示する予定はありません。</p>"
        else:
            items = "".join(
                f"<li style='margin:0.35rem 0'>{_format_ev_line(ev, gcal_tz)}</li>"
                for ev in day_evs
            )
            cal_block = f"<ul style='margin:0;padding-left:1.15rem'>{items}</ul>"

        # AIローンチ関連
        if not notion_token or not ai_launch_db_id:
            ai_block = (
                "<p style='opacity:0.75;margin:0;font-size:0.9rem'>"
                "NOTION_TOKEN / NOTION_AI_LAUNCH_DATABASE_ID を設定してください。</p>"
            )
        elif ai_launch_err:
            ai_block = "<p style='opacity:0.85;margin:0'>取得できませんでした（上の警告を確認）。</p>"
        elif not ai_launch_tasks:
            ai_block = "<p style='opacity:0.75;margin:0'>今後の AIローンチタスクはありません。</p>"
        else:
            ai_block = _format_tasks_hierarchical_html(ai_launch_tasks)

        # 個人タスク関連
        if not notion_token or not tasks_db_id:
            personal_block = (
                "<p style='opacity:0.75;margin:0;font-size:0.9rem'>"
                "NOTION_TOKEN / NOTION_TASKS_DATABASE_ID を設定してください。</p>"
            )
        elif tasks_err:
            personal_block = "<p style='opacity:0.85;margin:0'>取得できませんでした（上の警告を確認）。</p>"
        elif not day_tasks:
            personal_block = (
                "<p style='opacity:0.75;margin:0'>今後の個人タスクはありません。</p>"
            )
        else:
            personal_block = _format_tasks_hierarchical_html(day_tasks)

        inner = (
            "<p style='margin:0 0 0.4rem 0;font-weight:600;opacity:0.95'>予定（カレンダー）</p>"
            + cal_block
            + _notion_section_header_row_html("AIローンチ関連", ai_launch_notion_url, margin_top="1rem")
            + ai_block
            + _notion_section_header_row_html("個人タスク関連", tasks_notion_url, margin_top="1rem")
            + personal_block
        )

        has_cal_list = bool(day_evs)
        has_task_list = bool(ai_launch_tasks) or bool(day_tasks)
        any_source_configured = gcal_configured or bool(notion_token)
        use_compact = (
            any_source_configured
            and not has_cal_list
            and not has_task_list
        ) or (not any_source_configured)

        panel_class = "gt-day-events-panel"
        if use_compact:
            panel_class += " gt-day-events-panel--compact"

        st.markdown(
            f'<div class="{panel_class}">{inner}</div>',
            unsafe_allow_html=True,
        )

    day_label = "今日" if selected_date == today_local else "この日"
    st.markdown(
        f'<div class="section-header">■ {day_label}の仕事は何か？（手入力・Google 予定とは別）</div>',
        unsafe_allow_html=True,
    )

    schedule = entry["schedule"]

    # Google Calendar の時間帯イベントをスロットにマッピング（開始〜終了前まで全スロットに自動反映）
    gcal_time_slots: dict[str, str] = {}
    for ev in day_evs:
        if ev.get("all_day"):
            continue
        s = ev.get("start", "")
        if not s:
            continue
        try:
            s_iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
            dt_start = datetime.fromisoformat(s_iso)
            if dt_start.tzinfo is None:
                dt_start = dt_start.replace(tzinfo=timezone.utc)
            dt_start_local = dt_start.astimezone(gcal_tz)

            e = ev.get("end", "")
            if e:
                e_iso = e.replace("Z", "+00:00") if e.endswith("Z") else e
                dt_end = datetime.fromisoformat(e_iso)
                if dt_end.tzinfo is None:
                    dt_end = dt_end.replace(tzinfo=timezone.utc)
                dt_end_local = dt_end.astimezone(gcal_tz)
            else:
                dt_end_local = dt_start_local + timedelta(minutes=30)

            # 開始時刻を直前の 30 分境界に丸めてから全スロットを埋める
            rounded_minute = 0 if dt_start_local.minute < 30 else 30
            current = dt_start_local.replace(minute=rounded_minute, second=0, microsecond=0)
            while current < dt_end_local:
                slot = f"{current.hour}:{current.minute:02d}"
                if slot not in gcal_time_slots:
                    gcal_time_slots[slot] = ev.get("summary", "")
                current += timedelta(minutes=30)
        except ValueError:
            pass

    # カレンダー同期ロジック（ユーザー手入力の保護と両立）
    #
    # 問題: 毎描画で session_state を無条件に上書きすると、
    #       ユーザーが入力するたびに Streamlit が再描画し入力が消える。
    #
    # 解決策: カレンダーの「前回値」をキャッシュし、変化があった時だけ更新する。
    #   - 初回ロード         → カレンダー値で初期化（DB保存値より優先）
    #   - カレンダーが変化   → 現在値 == 前回カレンダー値（手動編集なし）なら更新
    #   - ユーザーが手動編集 → 現在値 != 前回カレンダー値なので上書きしない
    cal_cache_key = f"gcal_cache_{dk}"
    if cal_cache_key not in st.session_state:
        st.session_state[cal_cache_key] = {}
    prev_gcal_slots: dict[str, str] = st.session_state[cal_cache_key]

    for i, row in enumerate(schedule):
        task_key = f"task_{dk}_{i}"
        time_slot = row["time"]
        new_cal = gcal_time_slots.get(time_slot, "")
        prev_cal = prev_gcal_slots.get(time_slot)  # None = 未キャッシュ（初回）

        if prev_cal is None:
            # 初回ロード: カレンダー値優先、なければ DB 保存値
            st.session_state[task_key] = new_cal or row.get("task", "")
        elif new_cal != prev_cal:
            # カレンダーが変化: ユーザーが手動編集していなければ更新
            current_val = st.session_state.get(task_key, "")
            if current_val == prev_cal:
                st.session_state[task_key] = new_cal

    # 次の再描画での変化検知のためにキャッシュを更新
    st.session_state[cal_cache_key] = {
        row["time"]: gcal_time_slots.get(row["time"], "") for row in schedule
    }

    st.markdown("**TIME / この日の予定 / ゴールイメージ / GIVEできる価値**")

    updated_schedule = []
    h1, h2, h3, h4, h5 = st.columns([1, 3, 3, 2, 0.6])
    h1.markdown("**TIME**")
    h2.markdown("**この日に予定されている仕事**")
    h3.markdown("**ゴールイメージ**")
    h4.markdown("**GIVEできる価値**")

    for i, row in enumerate(schedule):
        c1, c2, c3, c4, c5 = st.columns([1, 3, 3, 2, 0.6])
        with c1:
            st.markdown(f"**{row['time']}**")
        with c2:
            task = st.text_area(
                "task",
                key=f"task_{dk}_{i}",
                height=68,
                label_visibility="collapsed",
            )
        with c3:
            goal = st.text_area(
                "goal",
                value=row.get("goal_image", ""),
                key=f"goal_{dk}_{i}",
                height=68,
                label_visibility="collapsed",
            )
        with c4:
            give = st.text_area(
                "give",
                value=row.get("give_value", ""),
                key=f"give_{dk}_{i}",
                height=68,
                label_visibility="collapsed",
            )
        with c5:
            if st.button("＋", key=f"add_action_{dk}_{i}", help="今日行うべきアクションに追加"):
                task_val = st.session_state.get(f"task_{dk}_{i}", "")
                if task_val.strip():
                    st.session_state[f"_pending_add_action_{dk}"] = {
                        "time": row["time"],
                        "action": task_val,
                    }
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
                height=68, key=f"img_{dk}_{key}",
                label_visibility="collapsed",
            )

    # ──────────────────────────────────────────────────────
    st.markdown('<div class="section-header">■ どのようなアクションがあなたの人生の成功を加速させるか？</div>', unsafe_allow_html=True)

    def _action_sort_key(row):
        t = row.get("time", "").strip().replace("：", ":")
        if not t:
            return float("inf")
        try:
            parts = t.split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            if h == 0:
                h = 24  # 0:00 は深夜扱い（一日の末尾）
            return h * 60 + m
        except Exception:
            return float("inf")

    def _collect_actions_from_state(dk, actions):
        return [
            {
                "time": st.session_state.get(f"at_{dk}_{i}", row.get("time", "")),
                "action": st.session_state.get(f"aa_{dk}_{i}", row.get("action", "")),
                "result": st.session_state.get(f"ar_{dk}_{i}", row.get("result", "")),
                "next_learning": st.session_state.get(f"an_{dk}_{i}", row.get("next_learning", "")),
            }
            for i, row in enumerate(actions)
        ]

    def _apply_actions_to_state(dk, sorted_actions, old_count):
        """描画前専用: session_state にソート済みの値を書き込む。描画後は使用不可。"""
        for i, row in enumerate(sorted_actions):
            st.session_state[f"at_{dk}_{i}"] = row["time"]
            st.session_state[f"aa_{dk}_{i}"] = row["action"]
            st.session_state[f"ar_{dk}_{i}"] = row["result"]
            st.session_state[f"an_{dk}_{i}"] = row["next_learning"]
        for i in range(len(sorted_actions), old_count):
            for pfx in ["at", "aa", "ar", "an"]:
                st.session_state.pop(f"{pfx}_{dk}_{i}", None)

    def _clear_action_keys(dk, old_count):
        """描画後専用: 既存のウィジェットキーを削除して次の rerun で DB 値から再初期化させる。"""
        for i in range(old_count):
            for pfx in ["at", "aa", "ar", "an"]:
                key = f"{pfx}_{dk}_{i}"
                if key in st.session_state:
                    del st.session_state[key]

    actions = entry["actions"]

    # スケジュール欄の＋ボタンで追加リクエストがあれば新しい行として追加してソート
    _pending_key = f"_pending_add_action_{dk}"
    if _pending_key in st.session_state:
        _pending = st.session_state.pop(_pending_key)
        current = _collect_actions_from_state(dk, actions)
        current.append({"time": _pending["time"], "action": _pending["action"], "result": "", "next_learning": ""})
        sorted_acts = sorted(current, key=_action_sort_key)
        _apply_actions_to_state(dk, sorted_acts, len(actions))
        entry["actions"] = sorted_acts
        db.save_daily_entry(entry)
        st.toast(f"「{_pending['action']}」をアクションに追加しました", icon="✅")
        st.rerun()

    ah1, ah2, ah3, ah4, _ah5 = st.columns([1, 3, 3, 3, 0.5])
    ah1.markdown("**TIME**")
    ah2.markdown("**今日行うべきアクション**")
    ah3.markdown("**結果**")
    ah4.markdown("**次に活かせること（同じ失敗を2度と繰り返さないために）**")

    updated_actions = []
    delete_action_index = None

    for i, row in enumerate(actions):
        a1, a2, a3, a4, a5 = st.columns([1, 3, 3, 3, 0.5])
        with a1:
            t = st.text_area(
                "time", value=row.get("time", ""), key=f"at_{dk}_{i}", height=68, label_visibility="collapsed"
            )
        with a2:
            a = st.text_area(
                "action", value=row.get("action", ""), key=f"aa_{dk}_{i}", height=68, label_visibility="collapsed"
            )
        with a3:
            r = st.text_area(
                "result", value=row.get("result", ""), key=f"ar_{dk}_{i}", height=68, label_visibility="collapsed"
            )
        with a4:
            n = st.text_area(
                "next", value=row.get("next_learning", ""), key=f"an_{dk}_{i}", height=68, label_visibility="collapsed"
            )
        with a5:
            if st.button("×", key=f"del_action_{dk}_{i}", help="この行を削除"):
                delete_action_index = i
        updated_actions.append({"time": t, "action": a, "result": r, "next_learning": n})

    if delete_action_index is not None:
        updated_actions.pop(delete_action_index)
        sorted_acts = sorted(updated_actions, key=_action_sort_key)
        _clear_action_keys(dk, len(actions))
        entry["actions"] = sorted_acts
        db.save_daily_entry(entry)
        st.rerun()

    if st.button("＋ 行を追加", key=f"add_action_row_{dk}"):
        updated_actions.append({"time": "", "action": "", "result": "", "next_learning": ""})
        sorted_acts = sorted(updated_actions, key=_action_sort_key)
        _clear_action_keys(dk, len(actions))
        entry["actions"] = sorted_acts
        db.save_daily_entry(entry)
        st.rerun()

    entry["actions"] = sorted(updated_actions, key=_action_sort_key)

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
                    height=68,
                    key=f"prob_{dk}_{key}",
                    label_visibility="collapsed",
                )

    # ──────────────────────────────────────────────────────
    with st.expander("■ 誰にどのようなメッセージを送るか？（任意）"):
        entry["message"] = st.text_area(
            "メッセージ",
            value=entry.get("message", ""),
            height=68,
            key=f"msg_{dk}",
            label_visibility="collapsed",
        )

    db.save_daily_entry(entry)
    _inject_textarea_autoresize()


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
        year = st.number_input("年", value=year_now, min_value=2020, max_value=2030, step=1,
                               key="q_year_sel")
    with col2:
        quarter = st.selectbox("四半期", [1, 2, 3, 4], index=q_now - 1,
                               format_func=lambda q: f"Q{q} ({(q-1)*3+1}〜{(q-1)*3+3}月)",
                               key="q_quarter_sel")

    months = quarter_months(year, quarter)
    goals = db.get_quarterly_goals(year, quarter)

    st.markdown('<div class="section-header">■ 四半期の目的と目標</div>', unsafe_allow_html=True)

    goals["intention"] = st.text_area(
        "この四半期、何を意図して仕事をするか？",
        value=goals.get("intention", ""), height=68,
        key="q_intention",
    )

    _spacer, c1, c2, c3, _trail = st.columns([2, 4, 4, 4, 0.5])
    for i, (col, month) in enumerate(zip([c1, c2, c3], months)):
        with col:
            key = f"month{i+1}_theme"
            goals[key] = st.text_area(
                f"{month}月：この月、何を意図すると価値があるか？",
                value=goals.get(key, ""), height=68,
                key=f"q_{key}",
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
    header = st.columns([1, 2, 2, 2, 2, 2, 2, 0.5])
    for col, label in zip(header, ["種別", f"{months[0]}月 目標", f"{months[0]}月 結果",
                                    f"{months[1]}月 目標", f"{months[1]}月 結果",
                                    f"{months[2]}月 目標", f"{months[2]}月 結果", ""]):
        col.markdown(f"**{label}**")

    for i, row in enumerate(kpi_rows):
        cols = st.columns([1, 2, 2, 2, 2, 2, 2, 0.5])
        r_type = cols[0].selectbox("type", ["KGI", "KPI"], index=0 if row.get("type") == "KGI" else 1,
                                   key=f"ktype_{i}", label_visibility="collapsed")
        m1g = cols[1].text_area("m1g", value=row.get("month1_goal", ""), key=f"m1g_{i}", label_visibility="collapsed", height=68)
        m1r = cols[2].text_area("m1r", value=row.get("month1_result", ""), key=f"m1r_{i}", label_visibility="collapsed", height=68)
        m2g = cols[3].text_area("m2g", value=row.get("month2_goal", ""), key=f"m2g_{i}", label_visibility="collapsed", height=68)
        m2r = cols[4].text_area("m2r", value=row.get("month2_result", ""), key=f"m2r_{i}", label_visibility="collapsed", height=68)
        m3g = cols[5].text_area("m3g", value=row.get("month3_goal", ""), key=f"m3g_{i}", label_visibility="collapsed", height=68)
        m3r = cols[6].text_area("m3r", value=row.get("month3_result", ""), key=f"m3r_{i}", label_visibility="collapsed", height=68)
        if cols[7].button("×", key=f"del_{i}"):
            delete_index = i
        updated_kpi.append({
            "type": r_type, "label": row.get("label", ""),
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

    st.markdown("---")
    goals["kpi_memo"] = st.text_area(
        "📝 メモ（KGI/KPIを設定した理由・達成に向けたアクションアイデアなど）",
        value=goals.get("kpi_memo", ""),
        height=150,
        key="q_kpi_memo",
        placeholder="例）なぜこのKGI/KPIを設定したか、達成するために必要な工夫や具体的なアクションを書いておきましょう。",
    )

    db.save_quarterly_goals(year, quarter, goals)
    db.save_quarterly_kpi(year, quarter, updated_kpi)
    _inject_textarea_autoresize()


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
            mission[key] = st.text_area(label, value=mission.get(key, ""), height=68,
                                        key=f"lm_{key}", label_visibility="collapsed")
    for key, label in fields_right:
        with col_r:
            st.markdown(f'<div class="static-label">{label}</div>', unsafe_allow_html=True)
            mission[key] = st.text_area(label, value=mission.get(key, ""), height=68,
                                        key=f"lm_{key}", label_visibility="collapsed")

    db.save_life_mission(mission)
    _inject_textarea_autoresize()


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
