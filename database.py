import sqlite3
import json
import os
from datetime import datetime, date

DB_PATH = os.getenv("DATABASE_PATH", "goal_tracker.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        -- ライフミッション・年間目標（静的）
        CREATE TABLE IF NOT EXISTS life_mission (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        );

        -- 四半期目標
        CREATE TABLE IF NOT EXISTS quarterly_goals (
            year INTEGER NOT NULL,
            quarter INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT,
            updated_at TEXT,
            PRIMARY KEY (year, quarter, key)
        );

        -- 四半期KGI/KPIテーブル
        CREATE TABLE IF NOT EXISTS quarterly_kpi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL,
            quarter INTEGER NOT NULL,
            type TEXT NOT NULL,   -- 'KGI' or 'KPI'
            label TEXT,
            month1_goal TEXT,
            month1_result TEXT,
            month2_goal TEXT,
            month2_result TEXT,
            month3_goal TEXT,
            month3_result TEXT,
            updated_at TEXT
        );

        -- デイリーエントリー
        CREATE TABLE IF NOT EXISTS daily_entries (
            date TEXT PRIMARY KEY,
            schedule TEXT,           -- JSON: [{time, task, goal_image, give_value}]
            image_q1 TEXT,
            image_q2 TEXT,
            image_q3 TEXT,
            image_q4 TEXT,
            image_q5 TEXT,
            image_q6 TEXT,
            actions TEXT,            -- JSON: [{time, action, result, next_learning}]
            problem TEXT,
            problem_source TEXT,
            problem_solution TEXT,
            problem_absolute TEXT,
            problem_principle TEXT,
            problem_blind_spot TEXT,
            problem_root TEXT,
            problem_research_internal TEXT,
            problem_research_same TEXT,
            problem_research_other TEXT,
            problem_premise TEXT,
            problem_prevention TEXT,
            message TEXT,
            created_at TEXT,
            updated_at TEXT
        );
    """)
    conn.commit()
    conn.close()


# ─── ライフミッション ───────────────────────────────────────

LIFE_MISSION_KEYS = [
    "mission",
    "legacy",
    "values",
    "assets_now",
    "assets_at_60",
    "assets_this_year",
    "work_purpose",
    "goal_5years",
    "goal_1year",
    "goal_1year_why",
    "goal_1year_who",
    "goal_1year_without",
]

def get_life_mission() -> dict:
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM life_mission").fetchall()
    conn.close()
    return {r["key"]: r["value"] or "" for r in rows}


def save_life_mission(data: dict):
    conn = get_connection()
    now = datetime.now().isoformat()
    for key, value in data.items():
        conn.execute(
            "INSERT OR REPLACE INTO life_mission (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now),
        )
    conn.commit()
    conn.close()


# ─── 四半期目標 ─────────────────────────────────────────────

QUARTERLY_KEYS = ["intention", "month1_theme", "month2_theme", "month3_theme"]

def get_quarterly_goals(year: int, quarter: int) -> dict:
    conn = get_connection()
    rows = conn.execute(
        "SELECT key, value FROM quarterly_goals WHERE year=? AND quarter=?",
        (year, quarter),
    ).fetchall()
    conn.close()
    return {r["key"]: r["value"] or "" for r in rows}


def save_quarterly_goals(year: int, quarter: int, data: dict):
    conn = get_connection()
    now = datetime.now().isoformat()
    for key, value in data.items():
        conn.execute(
            """INSERT OR REPLACE INTO quarterly_goals (year, quarter, key, value, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (year, quarter, key, value, now),
        )
    conn.commit()
    conn.close()


def get_quarterly_kpi(year: int, quarter: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM quarterly_kpi WHERE year=? AND quarter=? ORDER BY type, id",
        (year, quarter),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_quarterly_kpi(year: int, quarter: int, rows: list[dict]):
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "DELETE FROM quarterly_kpi WHERE year=? AND quarter=?", (year, quarter)
    )
    for r in rows:
        conn.execute(
            """INSERT INTO quarterly_kpi
               (year, quarter, type, label, month1_goal, month1_result,
                month2_goal, month2_result, month3_goal, month3_result, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (year, quarter, r.get("type","KPI"), r.get("label",""),
             r.get("month1_goal",""), r.get("month1_result",""),
             r.get("month2_goal",""), r.get("month2_result",""),
             r.get("month3_goal",""), r.get("month3_result",""), now),
        )
    conn.commit()
    conn.close()


# ─── デイリーエントリー ─────────────────────────────────────

def get_daily_entry(date_str: str) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM daily_entries WHERE date=?", (date_str,)
    ).fetchone()
    conn.close()
    if row:
        d = dict(row)
        d["schedule"] = json.loads(d["schedule"]) if d.get("schedule") else _default_schedule()
        d["actions"] = json.loads(d["actions"]) if d.get("actions") else _default_actions()
        return d
    return _empty_entry(date_str)


def _default_schedule():
    times = ["7:00","8:00","9:00","10:00","11:00","12:00","13:00",
             "14:00","15:00","16:00","17:00","18:00","19:00","20:00","21:00","22:00","23:00","0:00"]
    return [{"time": t, "task": "", "goal_image": "", "give_value": ""} for t in times]


def _default_actions():
    return [{"time": "", "action": "", "result": "", "next_learning": ""} for _ in range(7)]


def _empty_entry(date_str: str) -> dict:
    return {
        "date": date_str,
        "schedule": _default_schedule(),
        "image_q1": "", "image_q2": "", "image_q3": "",
        "image_q4": "", "image_q5": "", "image_q6": "",
        "actions": _default_actions(),
        "problem": "", "problem_source": "", "problem_solution": "",
        "problem_absolute": "", "problem_principle": "", "problem_blind_spot": "",
        "problem_root": "", "problem_research_internal": "",
        "problem_research_same": "", "problem_research_other": "",
        "problem_premise": "", "problem_prevention": "",
        "message": "",
        "created_at": None, "updated_at": None,
    }


def save_daily_entry(entry: dict):
    conn = get_connection()
    now = datetime.now().isoformat()
    existing = conn.execute(
        "SELECT created_at FROM daily_entries WHERE date=?", (entry["date"],)
    ).fetchone()
    created_at = existing["created_at"] if existing else now

    schedule_json = json.dumps(entry.get("schedule", []), ensure_ascii=False)
    actions_json = json.dumps(entry.get("actions", []), ensure_ascii=False)

    conn.execute(
        """INSERT OR REPLACE INTO daily_entries
           (date, schedule, image_q1, image_q2, image_q3, image_q4, image_q5, image_q6,
            actions, problem, problem_source, problem_solution, problem_absolute,
            problem_principle, problem_blind_spot, problem_root, problem_research_internal,
            problem_research_same, problem_research_other, problem_premise,
            problem_prevention, message, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (entry["date"], schedule_json,
         entry.get("image_q1",""), entry.get("image_q2",""),
         entry.get("image_q3",""), entry.get("image_q4",""),
         entry.get("image_q5",""), entry.get("image_q6",""),
         actions_json,
         entry.get("problem",""), entry.get("problem_source",""),
         entry.get("problem_solution",""), entry.get("problem_absolute",""),
         entry.get("problem_principle",""), entry.get("problem_blind_spot",""),
         entry.get("problem_root",""), entry.get("problem_research_internal",""),
         entry.get("problem_research_same",""), entry.get("problem_research_other",""),
         entry.get("problem_premise",""), entry.get("problem_prevention",""),
         entry.get("message",""), created_at, now),
    )
    conn.commit()
    conn.close()


def get_all_entry_dates() -> list[str]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT date FROM daily_entries ORDER BY date DESC"
    ).fetchall()
    conn.close()
    return [r["date"] for r in rows]


def get_entries_range(start: str, end: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM daily_entries WHERE date BETWEEN ? AND ? ORDER BY date",
        (start, end),
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d["schedule"] = json.loads(d["schedule"]) if d.get("schedule") else []
        d["actions"] = json.loads(d["actions"]) if d.get("actions") else []
        result.append(d)
    return result
