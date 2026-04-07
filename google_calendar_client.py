"""
Google Calendar / Tasks API（読み取り専用）の薄いラッパー。
トークンは token.json に保存。初回のみブラウザで OAuth。
スコープを増やしたあとは token.json を削除して再認証が必要な場合があります。
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = (
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
)

# 1 回の list で多すぎる場合の安全上限（ページングで回収）
_MAX_PAGES = 10
_PAGE_SIZE = 250
# UI・ログ用（この件数を超えた分は nextPageToken が残る場合がある）
LIST_EVENTS_MAX_ITEMS = _MAX_PAGES * _PAGE_SIZE

_TASK_PAGE_SIZE = 100
_MAX_TASK_LIST_PAGES = 10
_MAX_TASK_PAGES_PER_LIST = 25


def default_credentials_path() -> str:
    return os.environ.get("GOOGLE_CALENDAR_CREDENTIALS_PATH", "credentials.json")


def default_token_path() -> str:
    return os.environ.get("GOOGLE_CALENDAR_TOKEN_PATH", "token.json")


def _chmod_private(path: str) -> None:
    if sys.platform == "win32":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def get_credentials(*, interactive: bool = False) -> Credentials | None:
    """
    credentials.json と token.json から認証情報を返す。
    interactive=False のとき、有効なトークンがなければ None。
    interactive=True のとき、トークンがなければローカルサーバで OAuth。
    """
    cred_path = default_credentials_path()
    token_path = default_token_path()

    if not os.path.isfile(cred_path):
        return None

    creds: Credentials | None = None
    if os.path.isfile(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            _chmod_private(token_path)
            return creds
        except Exception:
            if not interactive:
                return None
            creds = None

    if not interactive:
        return None

    print(
        "OAuth: このあとブラウザが開きます。開かない場合は、次に表示される URL をコピーしてブラウザに貼ってください。",
        flush=True,
    )
    flow = InstalledAppFlow.from_client_secrets_file(cred_path, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    with open(token_path, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    _chmod_private(token_path)
    return creds


def build_calendar_service(creds: Credentials):
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def build_tasks_service(creds: Credentials):
    return build("tasks", "v1", credentials=creds, cache_discovery=False)


def parse_calendar_ids(raw: str | None) -> tuple[str, ...]:
    """GOOGLE_CALENDAR_ID の値を解釈する。カンマ区切りで複数可（前後空白・空要素は無視、重複は除く）。

    未設定・空のときは ``(\"primary\",)``。
    """
    s = (raw or "").strip()
    if not s:
        return ("primary",)
    parts = [p.strip() for p in s.split(",")]
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if not p:
            continue
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return tuple(out) if out else ("primary",)


def _parse_event_times(item: dict[str, Any]) -> tuple[str | None, str | None, bool]:
    start = item.get("start") or {}
    end = item.get("end") or {}
    if "dateTime" in start:
        return start["dateTime"], end.get("dateTime"), False
    if "date" in start:
        return start["date"], end.get("date"), True
    return None, None, False


def list_events(
    service,
    calendar_id: str,
    time_min_iso: str,
    time_max_iso: str,
) -> tuple[list[dict[str, Any]], bool]:
    """events.list をページングし、キャンセル済みを除き正規化したリストを返す。

    戻り値の bool は、安全上限のためまだ nextPageToken があるとき True（取りこぼしあり）。
    """
    out: list[dict[str, Any]] = []
    page_token: str | None = None
    pages = 0

    while pages < _MAX_PAGES:
        pages += 1
        req = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min_iso,
                timeMax=time_max_iso,
                singleEvents=True,
                orderBy="startTime",
                maxResults=_PAGE_SIZE,
                pageToken=page_token,
            )
        )
        resp = req.execute()
        for ev in resp.get("items", []):
            if ev.get("status") == "cancelled":
                continue
            start_s, end_s, all_day = _parse_event_times(ev)
            out.append(
                {
                    "id": ev.get("id"),
                    "iCalUID": ev.get("iCalUID"),
                    "calendar_id": calendar_id,
                    "summary": (ev.get("summary") or "(タイトルなし)").strip(),
                    "start": start_s,
                    "end": end_s,
                    "all_day": all_day,
                    "html_link": ev.get("htmlLink"),
                }
            )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    truncated = bool(page_token)
    return out, truncated


def list_tasklists(service) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page_token: str | None = None
    pages = 0
    while pages < _MAX_TASK_LIST_PAGES:
        pages += 1
        resp = (
            service.tasklists()
            .list(maxResults=100, pageToken=page_token)
            .execute()
        )
        for tl in resp.get("items", []):
            out.append(
                {
                    "id": tl["id"],
                    "title": (tl.get("title") or "(無題)").strip(),
                }
            )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def task_due_local_date(due: str | None, tz: ZoneInfo) -> date | None:
    """Tasks API の due（RFC3339）を、指定 TZ のカレンダー日付に変換。"""
    if not due:
        return None
    iso = due.replace("Z", "+00:00") if due.endswith("Z") else due
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).date()


def due_bounds_wide_rfc3339(day: date, tz: ZoneInfo) -> tuple[str, str]:
    """前日〜明後日 0:00（ローカル）を UTC で。API の境界の解釈差を吸収し後段でローカル日付に絞る。"""
    start = datetime.combine(day - timedelta(days=1), datetime.min.time(), tzinfo=tz)
    end = datetime.combine(day + timedelta(days=2), datetime.min.time(), tzinfo=tz)
    due_min = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    due_max = end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return due_min, due_max


def list_tasks_in_due_range(
    service,
    tasklist_id: str,
    due_min_rfc3339: str,
    due_max_rfc3339: str,
) -> list[dict[str, Any]]:
    """期限が [dueMin, dueMax) に入るタスク（完了・非表示も含む。削除済みは除外）。"""
    out: list[dict[str, Any]] = []
    page_token: str | None = None
    pages = 0
    while pages < _MAX_TASK_PAGES_PER_LIST:
        pages += 1
        resp = (
            service.tasks()
            .list(
                tasklist=tasklist_id,
                dueMin=due_min_rfc3339,
                dueMax=due_max_rfc3339,
                showCompleted=True,
                showHidden=True,
                maxResults=_TASK_PAGE_SIZE,
                pageToken=page_token,
            )
            .execute()
        )
        for t in resp.get("items", []):
            if t.get("deleted"):
                continue
            title = (t.get("title") or "").strip()
            out.append(
                {
                    "id": t.get("id"),
                    "title": title or "（無題）",
                    "due": t.get("due"),
                    "status": t.get("status"),
                    "parent": t.get("parent"),
                    "tasklist_id": tasklist_id,
                }
            )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def fetch_tasks_for_calendar_day_all_lists(
    service,
    target_day: date,
    tz: ZoneInfo,
) -> tuple[list[dict[str, Any]], str | None]:
    """全タスクリストから、期限のローカル日付が target_day のタスクだけを返す。"""
    due_min, due_max = due_bounds_wide_rfc3339(target_day, tz)
    try:
        lists = list_tasklists(service)
    except HttpError as e:
        return [], fetch_tasks_http_error_message(e)
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for tl in lists:
        try:
            batch = list_tasks_in_due_range(
                service, tl["id"], due_min, due_max
            )
        except HttpError as e:
            return [], fetch_tasks_http_error_message(e)
        for raw in batch:
            dloc = task_due_local_date(raw.get("due"), tz)
            if dloc != target_day:
                continue
            tid = raw.get("id") or ""
            key = (tl["id"], tid)
            if key in seen:
                continue
            seen.add(key)
            t = dict(raw)
            t["tasklist_title"] = tl["title"]
            merged.append(t)
    merged.sort(
        key=lambda x: (
            x.get("status") == "completed",
            x.get("tasklist_title") or "",
            x.get("parent") or "",
            x.get("title") or "",
        )
    )
    return merged, None


def fetch_tasks_http_error_message(exc: HttpError) -> str:
    if exc.resp.status == 403:
        return (
            "Tasks API が無効か、スコープ不足です。GCP で Tasks API を有効化し、"
            "古い `token.json` を削除してから再度「Google と接続」で認証し直してください。"
        )
    if exc.resp.status == 401:
        return "認証の有効期限が切れています。再度接続してください。"
    return (
        f"Tasks API エラー ({exc.resp.status}): "
        f"{exc.reason or exc.content.decode(errors='replace')[:200]}"
    )


def fetch_events_http_error_message(
    exc: HttpError, calendar_id: str | None = None
) -> str:
    who = f" [{calendar_id}]" if calendar_id else ""
    if exc.resp.status == 403:
        return (
            "Calendar API が無効か、スコープ不足の可能性があります。GCP で Calendar API を有効化し、"
            f"readonly スコープで再認証してください。{who}"
        )
    if exc.resp.status == 404:
        return (
            "カレンダー ID が見つからないか、このアカウントで参照できません。"
            f"GOOGLE_CALENDAR_ID を確認してください。{who}"
        )
    return (
        f"Google API エラー ({exc.resp.status}): "
        f"{exc.reason or exc.content.decode(errors='replace')[:200]}{who}"
    )


if __name__ == "__main__":
    print("google_calendar_client: 開始（初回はライブラリ読み込みで数十秒かかることがあります）", flush=True)

    tz_name = os.environ.get("GOOGLE_CALENDAR_TZ", "Asia/Tokyo")
    tz = ZoneInfo(tz_name)
    cal_ids = parse_calendar_ids(os.environ.get("GOOGLE_CALENDAR_ID"))
    today = date.today()
    start = datetime(today.year, today.month, 1, 0, 0, 0, tzinfo=tz)
    if today.month == 12:
        end = datetime(today.year + 1, 1, 1, 0, 0, 0, tzinfo=tz)
    else:
        end = datetime(today.year, today.month + 1, 1, 0, 0, 0, tzinfo=tz)

    tmin = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    tmax = end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    cred_path = default_credentials_path()
    if not os.path.isfile(cred_path):
        print(
            f"エラー: {cred_path} がありません。このフォルダに credentials.json を置いてください。",
            flush=True,
        )
        print(f"現在の作業フォルダ: {os.getcwd()}", flush=True)
        sys.exit(1)

    if os.path.isfile(default_token_path()):
        print("token.json があります。有効ならそのまま予定を取得します…", flush=True)

    c = get_credentials(interactive=True)
    if not c:
        print("認証に失敗しました。credentials.json をプロジェクトルートに置いて再実行してください。")
        sys.exit(1)
    svc = build_calendar_service(c)

    def _dedup_cli(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        nfb = 0
        for ev in a + b:
            iu = ev.get("iCalUID")
            if iu:
                k = f"ical:{iu}"
            else:
                cid = ev.get("calendar_id") or ""
                eid = ev.get("id")
                if eid:
                    k = f"ev:{cid}:{eid}"
                else:
                    k = f"nf:{cid}:{nfb}"
                    nfb += 1
            merged.setdefault(k, ev)
        return list(merged.values())

    all_evs: list[dict[str, Any]] = []
    any_trunc = False
    for cid in cal_ids:
        try:
            evs, truncated = list_events(svc, cid, tmin, tmax)
        except HttpError as e:
            print(fetch_events_http_error_message(e, cid))
            sys.exit(1)
        all_evs = _dedup_cli(all_evs, evs)
        if truncated:
            any_trunc = True
        print(
            f"calendarId={cid} 件数={len(evs)}"
            + ("（当月・このIDで取得上限の可能性）" if truncated else "")
        )
    all_evs.sort(key=lambda x: (x.get("start") or "", x.get("summary") or ""))
    print(
        f"マージ後 合計={len(all_evs)}"
        + ("（いずれかのカレンダーで打ち切りの可能性あり）" if any_trunc else "")
    )
    for e in all_evs[:20]:
        print(f"  - {e['start']} … {e['summary']}")
    if len(all_evs) > 20:
        print(f"  ... 他 {len(all_evs) - 20} 件")

    tsvc = build_tasks_service(c)
    ttasks, terr = fetch_tasks_for_calendar_day_all_lists(tsvc, today, tz)
    if terr:
        print(f"Tasks: {terr}")
    else:
        print(f"Tasks（期限が今日）: {len(ttasks)} 件")
        for x in ttasks[:15]:
            print(f"  - {x.get('title', '')}")
        if len(ttasks) > 15:
            print(f"  ... 他 {len(ttasks) - 15} 件")
