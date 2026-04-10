"""
THE PERSON 前日リマインダー（LINE Notify + Google カレンダー）

Googleカレンダーから翌日の「THE PERSON」予定を取得し、
LINE Notify でリマインドを送信します。

使い方:
  1. Google Cloud Console で Calendar API を有効化し、
     OAuth 2.0 クライアント ID を credentials.json として保存
  2. LINE Notify トークンを取得:
       https://notify-bot.line.me/ja/
  3. 環境変数に設定:
       export LINE_NOTIFY_TOKEN="your_token_here"
  4. 初回実行（ブラウザで Google 認証）:
       python line_sender.py
  5. cron で毎晩21時に自動実行する例:
       0 21 * * * LINE_NOTIFY_TOKEN="your_token" /usr/bin/python3 /path/to/line_sender.py
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone

import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ---- 設定 ----------------------------------------------------------------

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
TOKEN_FILE = "token_calendar.json"
CREDENTIALS_FILE = "credentials.json"

# 検索キーワード（予定タイトルに含まれていればリマインド対象）
KEYWORD = "THE PERSON"

LINE_NOTIFY_URL = "https://notify-api.line.me/api/notify"

JST = timezone(timedelta(hours=9))

# ---- Google 認証 ---------------------------------------------------------

def get_credentials() -> Credentials:
    """OAuth2 認証を行い Credentials を返す。"""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                sys.exit(
                    f"[ERROR] {CREDENTIALS_FILE} が見つかりません。\n"
                    "Google Cloud Console から OAuth 2.0 クライアント ID をダウンロードして配置してください。"
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


# ---- Google カレンダー取得 ------------------------------------------------

def fetch_tomorrow_events(creds: Credentials) -> list[dict]:
    """翌日の予定を全カレンダーから取得する。"""
    service = build("calendar", "v3", credentials=creds)

    tomorrow = date.today() + timedelta(days=1)
    time_min = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0, tzinfo=JST).isoformat()
    time_max = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 23, 59, 59, tzinfo=JST).isoformat()

    # 全カレンダー一覧を取得
    calendars = service.calendarList().list().execute().get("items", [])

    events = []
    for cal in calendars:
        result = service.events().list(
            calendarId=cal["id"],
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        for event in result.get("items", []):
            event["_calendarName"] = cal.get("summary", "")
            events.append(event)

    return events


def filter_the_person_events(events: list[dict]) -> list[dict]:
    """KEYWORD を含む予定だけを返す。"""
    matched = []
    for event in events:
        summary = event.get("summary", "")
        description = event.get("description", "")
        calendar_name = event.get("_calendarName", "")
        if KEYWORD.lower() in (summary + description + calendar_name).lower():
            matched.append(event)
    return matched


def format_event_time(event: dict) -> str:
    """予定の時刻を「HH:MM〜HH:MM」形式で返す。終日の場合は「終日」。"""
    start = event.get("start", {})
    end = event.get("end", {})

    if "dateTime" in start:
        start_dt = datetime.fromisoformat(start["dateTime"]).astimezone(JST)
        end_dt = datetime.fromisoformat(end["dateTime"]).astimezone(JST)
        return f"{start_dt.strftime('%H:%M')}〜{end_dt.strftime('%H:%M')}"
    else:
        return "終日"


# ---- LINE Notify 送信 ----------------------------------------------------

def send_line_notify(token: str, message: str) -> bool:
    """LINE Notify でメッセージを送信する。"""
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": message}

    try:
        response = requests.post(LINE_NOTIFY_URL, headers=headers, data=payload, timeout=10)
        response.raise_for_status()
        print(f"[OK] LINE 送信成功")
        return True
    except requests.exceptions.HTTPError as e:
        print(f"[ERROR] HTTPエラー: {e} (ステータス: {response.status_code})", file=sys.stderr)
    except requests.exceptions.ConnectionError:
        print("[ERROR] 接続エラー: ネットワークを確認してください", file=sys.stderr)
    except requests.exceptions.Timeout:
        print("[ERROR] タイムアウト: LINE Notify への接続がタイムアウトしました", file=sys.stderr)
    return False


def build_reminder_message(events: list[dict]) -> str:
    """リマインドメッセージを組み立てる。"""
    tomorrow = date.today() + timedelta(days=1)
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    weekday = weekdays[tomorrow.weekday()]
    date_str = tomorrow.strftime(f"%m月%d日（{weekday}）")

    lines = [
        f"\n【明日の {KEYWORD} リマインド】",
        f"日付: {date_str}",
        "",
    ]
    for event in events:
        title = event.get("summary", "（タイトルなし）")
        time_str = format_event_time(event)
        location = event.get("location", "")
        lines.append(f"▶ {title}")
        lines.append(f"   時間: {time_str}")
        if location:
            lines.append(f"   場所: {location}")
        lines.append("")

    lines.append("忘れずに！")
    return "\n".join(lines)


# ---- メイン --------------------------------------------------------------

def main():
    token = os.environ.get("LINE_NOTIFY_TOKEN")
    if not token:
        sys.exit(
            "[ERROR] 環境変数 LINE_NOTIFY_TOKEN が設定されていません。\n"
            "  export LINE_NOTIFY_TOKEN='your_token_here'"
        )

    print("Google カレンダーに接続中...")
    creds = get_credentials()

    print("翌日の予定を取得中...")
    all_events = fetch_tomorrow_events(creds)

    matched = filter_the_person_events(all_events)

    if not matched:
        print(f"[INFO] 翌日に「{KEYWORD}」の予定はありません。送信をスキップします。")
        return

    print(f"[INFO] {len(matched)} 件の予定が見つかりました。")
    message = build_reminder_message(matched)
    print(f"送信メッセージ:\n{message}\n")

    send_line_notify(token, message)


if __name__ == "__main__":
    main()
