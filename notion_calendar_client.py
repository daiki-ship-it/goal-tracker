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
from datetime import date
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
