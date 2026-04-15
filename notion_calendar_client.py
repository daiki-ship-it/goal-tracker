"""
Notion Calendar client

Notion データベースからカレンダーイベントとタスクを取得します。
標準ライブラリのみ使用（追加依存なし）。

必要な環境変数:
  NOTION_TOKEN                    - Notion インテグレーショントークン（secret_xxx...）
  NOTION_CALENDAR_DATABASE_ID     - カレンダーイベント用データベース ID
  NOTION_CALENDAR_DATE_PROPERTY   - 日付プロパティ名（デフォルト: "Date"）
  NOTION_TASKS_DATABASE_ID        - タスク用データベース ID（任意）
  NOTION_TASKS_DATE_PROPERTY      - タスクの期限プロパティ名（デフォルト: "Due"）
  NOTION_TASKS_STATUS_PROPERTY    - タスクのステータスプロパティ名（任意）
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Any


def get_token() -> str | None:
    """NOTION_TOKEN または NOTION_API_KEY 環境変数からトークンを取得。
    Notion の新 UI が付ける 'secret_' プレフィックスは自動的に除去する。
    """
    raw = os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_KEY")
    if not raw:
        return None
    # 新形式: "secret_ntn_xxx" → "ntn_xxx"（secret_ は表示上のプレフィックス）
    if raw.startswith("secret_"):
        return raw[len("secret_"):]
    return raw


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def _extract_title(page: dict) -> str:
    """ページのタイトルプロパティからテキストを取得。"""
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            arr = prop.get("title", [])
            text = "".join(t.get("plain_text", "") for t in arr).strip()
            return text or "（タイトルなし）"
    return "（タイトルなし）"


def _extract_date_prop(page: dict, prop_name: str) -> dict | None:
    """指定名（大文字小文字無視）の date プロパティ値を返す。"""
    props = page.get("properties", {})
    prop = props.get(prop_name)
    if prop is None:
        for k, v in props.items():
            if k.lower() == prop_name.lower():
                prop = v
                break
    if prop is None or prop.get("type") != "date":
        return None
    return prop.get("date")


def _extract_rich_text_prop(page: dict, prop_name: str) -> str | None:
    """指定名（大文字小文字無視）の rich_text プロパティからプレーンテキストを取得。"""
    props = page.get("properties", {})
    prop = props.get(prop_name)
    if prop is None:
        for k, v in props.items():
            if k.lower() == prop_name.lower():
                prop = v
                break
    if prop is None or prop.get("type") != "rich_text":
        return None
    texts = prop.get("rich_text", [])
    result = "".join(t.get("plain_text", "") for t in texts).strip()
    return result or None


def _extract_progress_prop(page: dict, prop_name: str) -> float | None:
    """指定名（大文字小文字無視）の rollup / formula / number プロパティから
    0.0〜1.0 の進捗値を取得する。Notion の percent_checked rollup に対応。"""
    props = page.get("properties", {})
    prop = props.get(prop_name)
    if prop is None:
        for k, v in props.items():
            if k.lower() == prop_name.lower():
                prop = v
                break
    if prop is None:
        return None
    t = prop.get("type")
    if t == "rollup":
        rollup = prop.get("rollup", {})
        rt = rollup.get("type")
        if rt == "number":
            val = rollup.get("number")
            if val is not None:
                return float(val)
    elif t == "formula":
        formula = prop.get("formula", {})
        ft = formula.get("type")
        if ft == "number":
            val = formula.get("number")
            if val is not None:
                return float(val)
    elif t == "number":
        val = prop.get("number")
        if val is not None:
            return float(val)
    return None


def _extract_relation_first_id(page: dict, prop_name: str) -> str | None:
    """指定名（大文字小文字無視）の relation プロパティの最初のページ ID を返す。
    Notion のサブアイテム機能では親は 1 件のみ想定。"""
    props = page.get("properties", {})
    prop = props.get(prop_name)
    if prop is None:
        for k, v in props.items():
            if k.lower() == prop_name.lower():
                prop = v
                break
    if prop is None or prop.get("type") != "relation":
        return None
    relations = prop.get("relation", [])
    return relations[0].get("id") if relations else None


def _normalize_event(page: dict, date_prop: str, source_id: str) -> dict | None:
    """Notion ページをアプリ内イベント形式に変換。"""
    date_val = _extract_date_prop(page, date_prop)
    if not date_val:
        return None
    start_s = date_val.get("start")
    if not start_s:
        return None
    end_s = date_val.get("end")
    # 終日判定: "2026-04-01"（10文字・T なし）
    all_day = len(start_s) == 10 and "T" not in start_s
    return {
        "id": page.get("id"),
        "iCalUID": None,
        "calendar_id": source_id,
        "summary": _extract_title(page),
        "start": start_s,
        "end": end_s,
        "all_day": all_day,
        "html_link": page.get("url"),
    }


def _do_post(url: str, body: dict, token: str) -> tuple[dict | None, str | None]:
    """JSON POST して (data, error_message) を返す。"""
    raw = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=raw, headers=_headers(token), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        if e.code == 401:
            return None, "Notion 認証エラー: NOTION_TOKEN が無効です。"
        if e.code == 404:
            return None, (
                "Notion データベースが見つかりません (404): "
                "ID を確認し、インテグレーションを DB に共有してください。"
            )
        if e.code == 400:
            return None, f"Notion API リクエストエラー (400): {body_text[:200]}"
        return None, f"Notion API エラー ({e.code}): {body_text[:200]}"
    except Exception as ex:
        return None, f"Notion 接続エラー: {str(ex)}"


def fetch_events(
    database_id: str,
    date_min: str,
    date_max: str,
    date_prop: str = "Date",
    token: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Notion DB から [date_min, date_max) の期間のイベントを取得。

    date_min / date_max は "YYYY-MM-DD" または ISO8601 文字列。
    戻り値: (events, error_message)
    """
    tok = token or get_token()
    if not tok:
        return [], "NOTION_TOKEN が未設定です。.env に NOTION_TOKEN を追加してください。"

    d_min = date_min[:10]
    d_max = date_max[:10]
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    filter_body = {
        "and": [
            {"property": date_prop, "date": {"on_or_after": d_min}},
            {"property": date_prop, "date": {"before": d_max}},
        ]
    }

    out: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        body: dict[str, Any] = {"filter": filter_body, "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data, err = _do_post(url, body, tok)
        if err:
            return out, err
        for page in (data or {}).get("results", []):
            ev = _normalize_event(page, date_prop, database_id)
            if ev:
                out.append(ev)
        if (data or {}).get("has_more") and (data or {}).get("next_cursor"):
            cursor = data["next_cursor"]  # type: ignore[index]
        else:
            break

    return out, None


def fetch_upcoming_tasks(
    database_id: str,
    date_from: date,
    date_prop: str = "Due",
    status_prop: str | None = None,
    token: str | None = None,
    days_ahead: int = 180,
    memo_prop: str | None = None,
    parent_prop: str | None = None,
    progress_prop: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """タスク DB から date_from 以降（days_ahead 日以内）のタスクを取得。締切日付きで返す。

    memo_prop:     メモ列のプロパティ名（rich_text 型、例: "メモ"）
    parent_prop:   親アイテムのプロパティ名（relation 型、例: "親アイテム"）
    progress_prop: 進捗列のプロパティ名（rollup/formula 型、例: "進捗"）
    """
    tok = token or get_token()
    if not tok:
        return [], "NOTION_TOKEN が未設定です。"

    date_to = (date_from + timedelta(days=days_ahead)).isoformat()
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    filter_body: dict[str, Any] = {
        "and": [
            {"property": date_prop, "date": {"on_or_after": date_from.isoformat()}},
            {"property": date_prop, "date": {"before": date_to}},
        ]
    }
    base_body: dict[str, Any] = {
        "filter": filter_body,
        "page_size": 100,
        "sorts": [{"property": date_prop, "direction": "ascending"}],
    }

    tasks: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        body = dict(base_body)
        if cursor:
            body["start_cursor"] = cursor
        data, err = _do_post(url, body, tok)
        if err:
            return tasks, err
        for page in (data or {}).get("results", []):
            title = _extract_title(page)
            props = page.get("properties", {})
            date_val = _extract_date_prop(page, date_prop)
            deadline_iso = (date_val.get("start") or "")[:10] if date_val else None
            status = "needsAction"
            if status_prop:
                sp = props.get(status_prop)
                if sp:
                    t = sp.get("type")
                    if t == "checkbox":
                        status = "completed" if sp.get("checkbox") else "needsAction"
                    elif t == "status":
                        name = (sp.get("status") or {}).get("name", "").lower()
                        if name in ("done", "完了", "completed", "closed"):
                            status = "completed"
            tasks.append(
                {
                    "id": page.get("id"),
                    "title": title,
                    "deadline_iso": deadline_iso,
                    "status": status,
                    "memo": _extract_rich_text_prop(page, memo_prop) if memo_prop else None,
                    "parent_id": _extract_relation_first_id(page, parent_prop) if parent_prop else None,
                    "progress": _extract_progress_prop(page, progress_prop) if progress_prop else None,
                }
            )
        if (data or {}).get("has_more") and (data or {}).get("next_cursor"):
            cursor = data["next_cursor"]  # type: ignore[index]
        else:
            break

    return tasks, None


def fetch_tasks_for_day(
    database_id: str,
    target_day: date,
    date_prop: str = "Due",
    status_prop: str | None = None,
    token: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """タスク DB から期限が target_day のタスクを取得。"""
    tok = token or get_token()
    if not tok:
        return [], "NOTION_TOKEN が未設定です。"

    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    filter_body: dict[str, Any] = {
        "property": date_prop,
        "date": {"equals": target_day.isoformat()},
    }
    body: dict[str, Any] = {"filter": filter_body, "page_size": 100}
    data, err = _do_post(url, body, tok)
    if err:
        return [], err

    tasks: list[dict[str, Any]] = []
    for page in (data or {}).get("results", []):
        title = _extract_title(page)
        props = page.get("properties", {})
        status = "needsAction"
        if status_prop:
            sp = props.get(status_prop)
            if sp:
                t = sp.get("type")
                if t == "checkbox":
                    status = "completed" if sp.get("checkbox") else "needsAction"
                elif t == "status":
                    name = (sp.get("status") or {}).get("name", "").lower()
                    if name in ("done", "完了", "completed", "closed"):
                        status = "completed"
        tasks.append(
            {
                "id": page.get("id"),
                "title": title,
                "due": None,
                "status": status,
                "parent": None,
                "tasklist_id": database_id,
                "tasklist_title": "Notion",
            }
        )
    return tasks, None
