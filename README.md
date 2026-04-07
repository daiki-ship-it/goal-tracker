# 目標管理シート 履歴・分析アプリ

Google Sheetsの目標管理シートを毎日自動取得し、履歴を蓄積して分析できるWebアプリです。

## 機能

- **毎日自動同期**: 新しいシートタブを自動で取得・保存
- **履歴管理**: SQLiteに全データを蓄積（過去データも閲覧可能）
- **デイリービュー**: 各日のQ&A形式でデータを表示
- **比較分析**: 2日間のデータを並べて比較
- **時系列分析**: 特定の質問への回答変遷を追跡
- **ワード頻度分析**: 期間内の頻出ワードをグラフ表示
- **全文検索**: キーワードで過去の記録を横断検索

## セットアップ

### 1. パッケージインストール

```bash
cd "goal-tracker"
pip install -r requirements.txt
```

### 2. 環境変数の設定

```bash
cp .env.example .env
# .env を編集して設定を記入
```

### 3. Google認証の設定

#### 方法A: サービスアカウント（推奨・自動化向け）

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクト作成
2. **APIとサービス > ライブラリ** から「Google Sheets API」を有効化
3. **APIとサービス > 認証情報** でサービスアカウントを作成
4. サービスアカウントのJSONキーをダウンロードして `service_account.json` として保存
5. Google Sheetsを開き、サービスアカウントのメールアドレス（〜@〜.iam.gserviceaccount.com）と共有（閲覧者権限）

```env
AUTH_METHOD=service_account
GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json
```

#### 方法B: OAuth2（個人アカウントで手軽に）

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクト作成
2. **APIとサービス > ライブラリ** から「Google Sheets API」を有効化
3. **APIとサービス > 認証情報** でOAuth 2.0クライアントID（デスクトップアプリ）を作成
4. JSONをダウンロードして `credentials.json` として保存
5. 初回起動時にブラウザが開くので認証する

```env
AUTH_METHOD=oauth
GOOGLE_CREDENTIALS_FILE=credentials.json
```

### 4. アプリ起動

```bash
streamlit run app.py
```

ブラウザで http://localhost:8501 が開きます。

### 5. 初回同期

アプリを開いて「🔄 同期設定」ページから「差分同期」ボタンをクリック。

---

## 自動同期の設定（macOS）

毎日指定時刻に自動同期するには、アプリ内「同期設定」ページで生成されるplistをインストールしてください。

または手動でcronを設定:

```bash
# 毎朝7時に同期
crontab -e
# 以下を追加:
0 7 * * * cd "/Users/daikisato/My tool/goal-tracker" && python run_scheduler.py >> sync.log 2>&1
```

---

## ファイル構成

```
goal-tracker/
├── app.py              # Streamlit Webアプリ
├── database.py         # SQLiteデータベース操作
├── sheets_client.py    # Google Sheets API接続
├── sync.py             # 同期ロジック
├── run_scheduler.py    # 自動同期スクリプト
├── requirements.txt    # 依存パッケージ
├── .env.example        # 環境変数テンプレート
└── goal_tracker.db     # SQLiteデータベース（自動生成）
```
