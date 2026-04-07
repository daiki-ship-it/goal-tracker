import os
from datetime import datetime, date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
import database as db

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
        ["📝 今日の記録", "📖 過去の記録", "📊 四半期目標", "🏆 ライフミッション", "📈 分析"],
        label_visibility="collapsed",
    )
    st.markdown("---")
    today_str = date.today().isoformat()
    all_dates = db.get_all_entry_dates()
    st.caption(f"記録日数: {len(all_dates)} 日")
    if all_dates:
        st.caption(f"最終記録: {all_dates[0]}")


# ══════════════════════════════════════════════════════════
# 📝 今日の記録
# ══════════════════════════════════════════════════════════
if page == "📝 今日の記録":
    st.title(f"📝 {fmt_date(today_str)}")

    # 日付切り替え
    col_date, col_nav = st.columns([3, 1])
    with col_date:
        selected_date = st.date_input("日付", value=date.today(), label_visibility="collapsed")
    date_str = selected_date.isoformat()

    entry = db.get_daily_entry(date_str)

    # ──────────────────────────────────────────────────────
    st.markdown('<div class="section-header">■ 今日の仕事は何か？</div>', unsafe_allow_html=True)

    schedule = entry["schedule"]
    has_tasks = any(s.get("task") for s in schedule)

    # スケジュールテーブル
    st.markdown("**TIME / 今日の予定 / ゴールイメージ / GIVEできる価値**")

    updated_schedule = []
    # ヘッダー行
    h1, h2, h3, h4 = st.columns([1, 3, 3, 2])
    h1.markdown("**TIME**")
    h2.markdown("**今日、予定されている仕事**")
    h3.markdown("**ゴールイメージ**")
    h4.markdown("**GIVEできる価値**")

    for i, row in enumerate(schedule):
        c1, c2, c3, c4 = st.columns([1, 3, 3, 2])
        with c1:
            st.markdown(f"**{row['time']}**")
        with c2:
            task = st.text_input("task", value=row.get("task", ""), key=f"task_{i}", label_visibility="collapsed")
        with c3:
            goal = st.text_input("goal", value=row.get("goal_image", ""), key=f"goal_{i}", label_visibility="collapsed")
        with c4:
            give = st.text_input("give", value=row.get("give_value", ""), key=f"give_{i}", label_visibility="collapsed")
        updated_schedule.append({"time": row["time"], "task": task, "goal_image": goal, "give_value": give})

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
                height=90, key=f"img_{key}",
                label_visibility="collapsed"
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
            t = st.text_input("time", value=row.get("time", ""), key=f"at_{i}", label_visibility="collapsed")
        with a2:
            a = st.text_input("action", value=row.get("action", ""), key=f"aa_{i}", label_visibility="collapsed")
        with a3:
            r = st.text_input("result", value=row.get("result", ""), key=f"ar_{i}", label_visibility="collapsed")
        with a4:
            n = st.text_input("next", value=row.get("next_learning", ""), key=f"an_{i}", label_visibility="collapsed")
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
                entry[key] = st.text_area(label, value=entry.get(key, ""), height=80,
                                          key=f"prob_{key}", label_visibility="collapsed")

    # ──────────────────────────────────────────────────────
    with st.expander("■ 誰にどのようなメッセージを送るか？（任意）"):
        entry["message"] = st.text_area(
            "メッセージ", value=entry.get("message", ""),
            height=120, label_visibility="collapsed"
        )

    # 保存ボタン
    st.markdown("---")
    if st.button("💾 保存する", type="primary", use_container_width=True):
        db.save_daily_entry(entry)
        st.success("✅ 保存しました！")
        st.rerun()


# ══════════════════════════════════════════════════════════
# 📖 過去の記録
# ══════════════════════════════════════════════════════════
elif page == "📖 過去の記録":
    st.title("📖 過去の記録")

    all_dates = db.get_all_entry_dates()
    if not all_dates:
        st.info("まだ記録がありません。「今日の記録」から入力してください。")
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
        st.info("「今日の記録」ページに移動して、日付をこの日に変更してください。")


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

    c1, c2, c3 = st.columns(3)
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
    header = st.columns([1, 1, 2, 2, 2, 2, 2, 2])
    for col, label in zip(header, ["種別", "項目", f"{months[0]}月 目標", f"{months[0]}月 結果",
                                    f"{months[1]}月 目標", f"{months[1]}月 結果",
                                    f"{months[2]}月 目標", f"{months[2]}月 結果"]):
        col.markdown(f"**{label}**")

    for i, row in enumerate(kpi_rows):
        cols = st.columns([1, 1, 2, 2, 2, 2, 2, 2])
        r_type = cols[0].selectbox("type", ["KGI", "KPI"], index=0 if row.get("type") == "KGI" else 1,
                                   key=f"ktype_{i}", label_visibility="collapsed")
        label = cols[1].text_input("label", value=row.get("label", ""), key=f"klabel_{i}", label_visibility="collapsed")
        m1g = cols[2].text_input("m1g", value=row.get("month1_goal", ""), key=f"m1g_{i}", label_visibility="collapsed")
        m1r = cols[3].text_input("m1r", value=row.get("month1_result", ""), key=f"m1r_{i}", label_visibility="collapsed")
        m2g = cols[4].text_input("m2g", value=row.get("month2_goal", ""), key=f"m2g_{i}", label_visibility="collapsed")
        m2r = cols[5].text_input("m2r", value=row.get("month2_result", ""), key=f"m2r_{i}", label_visibility="collapsed")
        m3g = cols[6].text_input("m3g", value=row.get("month3_goal", ""), key=f"m3g_{i}", label_visibility="collapsed")
        m3r = cols[7].text_input("m3r", value=row.get("month3_result", ""), key=f"m3r_{i}", label_visibility="collapsed")
        updated_kpi.append({
            "type": r_type, "label": label,
            "month1_goal": m1g, "month1_result": m1r,
            "month2_goal": m2g, "month2_result": m2r,
            "month3_goal": m3g, "month3_result": m3r,
        })

    if st.button("行を追加"):
        updated_kpi.append({"type": "KPI", "label": "", "month1_goal": "", "month1_result": "",
                             "month2_goal": "", "month2_result": "", "month3_goal": "", "month3_result": ""})

    st.markdown("---")
    if st.button("💾 保存する", type="primary", use_container_width=True):
        db.save_quarterly_goals(year, quarter, goals)
        db.save_quarterly_kpi(year, quarter, updated_kpi)
        st.success("✅ 保存しました！")
        st.rerun()


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
        ("goal_1year",       "この1年間の目標は何か？（締切：2027.3.31）"),
        ("goal_1year_why",   "この1年間あなたはなぜこの目標に到達する必要があるのか？目的は何か？"),
        ("goal_1year_who",   "あなたが目標に到達することは、誰のどのような役に立つことができるか？"),
        ("goal_1year_without","その目標が手に入らないと、どのような社絶な人生を想像することができるか？"),
    ]

    col_l, col_r = st.columns(2)
    for key, label in fields_left:
        with col_l:
            st.markdown(f'<div class="static-label">{label}</div>', unsafe_allow_html=True)
            mission[key] = st.text_area(label, value=mission.get(key, ""), height=80,
                                        key=f"lm_{key}", label_visibility="collapsed")
    for key, label in fields_right:
        with col_r:
            st.markdown(f'<div class="static-label">{label}</div>', unsafe_allow_html=True)
            mission[key] = st.text_area(label, value=mission.get(key, ""), height=80,
                                        key=f"lm_{key}", label_visibility="collapsed")

    st.markdown("---")
    if st.button("💾 保存する", type="primary", use_container_width=True):
        db.save_life_mission(mission)
        st.success("✅ 保存しました！")
        st.rerun()


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
