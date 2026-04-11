"""
THE PERSON リマインダー & 予約管理スクリプト

機能:
  1. GmailからTHE PERSONの予約確定メールを読み取り、Googleカレンダーに自動登録
  2. カレンダーとGmailの予約情報を突合し、不一致があればLINEで急ぎ通知
  3. 翌日にTHE PERSONの予定があれば、LINEでリマインド送信

実行方法:
  export LINE_CHANNEL_TOKEN="チャネルアクセストークン"
  export LINE_USER_ID="あなたのユーザーID"
  python3 line_sender.py

cron で毎晩20時に自動実行する例:
  0 20 * * * cd ~/line-reminder && LINE_CHANNEL_TOKEN="..." LINE_USER_ID="..." python3 line_sender.py
"""

import base64
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ---- 設定 ----------------------------------------------------------------

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
]
TOKEN_FILE = "token.json"
CREDENTIALS_FILE = "credentials.json"

SENDER_EMAIL = "noreply@the-person.com"
KEYWORD = "THE PERSON"
CALENDAR_EVENT_PREFIX = "[THE PERSON]"

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
JST = timezone(timedelta(hours=9))


# ---- Google 認証 ---------------------------------------------------------

def get_credentials() -> Credentials:
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                sys.exit(f"[ERROR] {CREDENTIALS_FILE} が見つかりません。")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


# ---- Gmail：予約メールを読み取る -----------------------------------------

def fetch_booking_emails(gmail_service, days: int = 90) -> list[dict]:
    """THE PERSONからの予約確定メールを取得する。"""
    after = (date.today() - timedelta(days=days)).strftime("%Y/%m/%d")
    query = f"from:{SENDER_EMAIL} after:{after}"
    result = gmail_service.users().messages().list(userId="me", q=query).execute()
    messages = result.get("messages", [])

    bookings = []
    for msg in messages:
        detail = gmail_service.users().messages().get(
            userId="me", messageId=msg["id"], format="full"
        ).execute()
        booking = parse_booking_email(detail)
        if booking:
            bookings.append(booking)
    return bookings


def get_email_body(payload: dict) -> str:
    """メールのテキスト本文を取得する。"""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore") if data else ""

    for part in payload.get("parts", []):
        body = get_email_body(part)
        if body:
            return body
    return ""


def parse_booking_email(message: dict) -> dict | None:
    """メールから予約情報（日時・場所・種別・時間）を抽出する。"""
    body = get_email_body(message.get("payload", {}))
    if not body:
        return None

    # 日程：2026年04月11日 09:00
    date_match = re.search(r"日程[：:]\s*(\d{4})年(\d{2})月(\d{2})日\s+(\d{2}):(\d{2})", body)
    if not date_match:
        return None

    year, month, day, hour, minute = [int(x) for x in date_match.groups()]
    start_dt = datetime(year, month, day, hour, minute, tzinfo=JST)

    # セッション時間：1時間
    duration_match = re.search(r"セッション時間[：:]\s*(\d+)時間", body)
    duration_hours = int(duration_match.group(1)) if duration_match else 1
    end_dt = start_dt + timedelta(hours=duration_hours)

    # 店舗名
    location_match = re.search(r"店舗名[：:]\s*(.+)", body)
    location = location_match.group(1).strip() if location_match else ""

    # セッション種別
    type_match = re.search(r"セッション種別[：:]\s*(.+)", body)
    session_type = type_match.group(1).strip() if type_match else ""

    return {
        "start": start_dt,
        "end": end_dt,
        "location": location,
        "session_type": session_type,
        "title": f"{CALENDAR_EVENT_PREFIX} {session_type} @ {location}".strip(),
    }


# ---- Google カレンダー操作 -----------------------------------------------

def fetch_calendar_events(cal_service, days_ahead: int = 90) -> list[dict]:
    """今後のTHE PERSON関連のカレンダー予定を取得する。"""
    now = datetime.now(tz=JST)
    time_max = (now + timedelta(days=days_ahead)).isoformat()

    calendars = cal_service.calendarList().list().execute().get("items", [])
    events = []
    for cal in calendars:
        result = cal_service.events().list(
            calendarId=cal["id"],
            timeMin=now.isoformat(),
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            q=KEYWORD,
        ).execute()
        for event in result.get("items", []):
            event["_calendarId"] = cal["id"]
            events.append(event)
    return events


def event_exists_in_calendar(cal_events: list[dict], booking: dict) -> bool:
    """同じ日時のカレンダー予定が存在するか確認する。"""
    for event in cal_events:
        start_str = event.get("start", {}).get("dateTime", "")
        if not start_str:
            continue
        event_dt = datetime.fromisoformat(start_str).astimezone(JST)
        if event_dt.date() == booking["start"].date() and event_dt.hour == booking["start"].hour:
            return True
    return False


def add_event_to_calendar(cal_service, booking: dict) -> bool:
    """予約をGoogleカレンダーに追加する（前日20時のリマインダー付き）。"""
    event_body = {
        "summary": booking["title"],
        "location": booking["location"],
        "start": {"dateTime": booking["start"].isoformat(), "timeZone": "Asia/Tokyo"},
        "end": {"dateTime": booking["end"].isoformat(), "timeZone": "Asia/Tokyo"},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 60 * 12},   # 当日朝（12時間前）
            ],
        },
    }
    try:
        cal_service.events().insert(calendarId="primary", body=event_body).execute()
        print(f"[OK] カレンダーに追加: {booking['title']} {booking['start'].strftime('%m/%d %H:%M')}")
        return True
    except Exception as e:
        print(f"[ERROR] カレンダー追加失敗: {e}", file=sys.stderr)
        return False


# ---- LINE 送信 -----------------------------------------------------------

def send_line_message(token: str, user_id: str, message: str) -> bool:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(
            LINE_PUSH_URL,
            headers=headers,
            json={"to": user_id, "messages": [{"type": "text", "text": message}]},
            timeout=10,
        )
        resp.raise_for_status()
        print("[OK] LINE 送信成功")
        return True
    except Exception as e:
        print(f"[ERROR] LINE 送信失敗: {e}", file=sys.stderr)
        return False


# ---- メッセージ組み立て --------------------------------------------------

def build_reminder_message(events: list[dict]) -> str:
    tomorrow = date.today() + timedelta(days=1)
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    weekday = weekdays[tomorrow.weekday()]
    date_str = tomorrow.strftime(f"%m月%d日（{weekday}）")

    lines = [f"【明日の {KEYWORD} リマインド】", f"日付: {date_str}", ""]
    for event in events:
        title = event.get("summary", "（タイトルなし）")
        start = event.get("start", {})
        if "dateTime" in start:
            start_dt = datetime.fromisoformat(start["dateTime"]).astimezone(JST)
            end_dt = datetime.fromisoformat(event["end"]["dateTime"]).astimezone(JST)
            time_str = f"{start_dt.strftime('%H:%M')}〜{end_dt.strftime('%H:%M')}"
        else:
            time_str = "終日"
        location = event.get("location", "")
        lines.append(f"▶ {title}")
        lines.append(f"   時間: {time_str}")
        if location:
            lines.append(f"   場所: {location}")
        lines.append("")
    lines.append("忘れずに！")
    return "\n".join(lines)


def build_mismatch_message(missing_in_calendar: list[dict], missing_in_gmail: list[dict]) -> str:
    lines = ["【⚠️ 要確認】THE PERSON 予約の不一致を検出しました", ""]
    if missing_in_calendar:
        lines.append("📧 メールにあるがカレンダーにない予約:")
        for b in missing_in_calendar:
            lines.append(f"  {b['start'].strftime('%m/%d(%a) %H:%M')} {b['location']}")
        lines.append("")
    if missing_in_gmail:
        lines.append("📅 カレンダーにあるがメール確認が取れない予約:")
        for e in missing_in_gmail:
            start_str = e.get("start", {}).get("dateTime", "")
            if start_str:
                dt = datetime.fromisoformat(start_str).astimezone(JST)
                lines.append(f"  {dt.strftime('%m/%d(%a) %H:%M')} {e.get('summary', '')}")
        lines.append("")
    lines.append("確認してください！")
    return "\n".join(lines)


# ---- メイン --------------------------------------------------------------

def main():
    token = os.environ.get("LINE_CHANNEL_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not token:
        sys.exit("[ERROR] 環境変数 LINE_CHANNEL_TOKEN が設定されていません。")
    if not user_id:
        sys.exit("[ERROR] 環境変数 LINE_USER_ID が設定されていません。")

    print("Google に接続中...")
    creds = get_credentials()
    cal_service = build("calendar", "v3", credentials=creds)
    gmail_service = build("gmail", "v1", credentials=creds)

    # ---- 1. Gmailから予約を取得 ------------------------------------------
    print("Gmail から THE PERSON の予約メールを取得中...")
    gmail_bookings = fetch_booking_emails(gmail_service, days=90)
    print(f"  → {len(gmail_bookings)} 件の予約メールを検出")

    # ---- 2. カレンダーの予定を取得 ----------------------------------------
    print("Google カレンダーから予定を取得中...")
    cal_events = fetch_calendar_events(cal_service, days_ahead=90)
    print(f"  → {len(cal_events)} 件の THE PERSON 予定を検出")

    # ---- 3. 突合：Gmailにあってカレンダーにない予約を追加 -----------------
    missing_in_calendar = []
    for booking in gmail_bookings:
        if booking["start"].date() < date.today():
            continue  # 過去の予定はスキップ
        if not event_exists_in_calendar(cal_events, booking):
            missing_in_calendar.append(booking)
            print(f"[INFO] カレンダーにない予約を発見: {booking['title']} {booking['start'].strftime('%m/%d %H:%M')}")
            add_event_to_calendar(cal_service, booking)

    # カレンダーを再取得（追加分を反映）
    if missing_in_calendar:
        cal_events = fetch_calendar_events(cal_service, days_ahead=90)

    # ---- 4. 突合：カレンダーにあってGmailにない予約を警告 -----------------
    gmail_dates = {b["start"].date() for b in gmail_bookings}
    missing_in_gmail = []
    for event in cal_events:
        start_str = event.get("start", {}).get("dateTime", "")
        if not start_str:
            continue
        event_date = datetime.fromisoformat(start_str).astimezone(JST).date()
        if event_date >= date.today() and event_date not in gmail_dates:
            missing_in_gmail.append(event)

    # ---- 5. 不一致があればLINEで急ぎ通知 ---------------------------------
    if missing_in_calendar or missing_in_gmail:
        msg = build_mismatch_message(missing_in_calendar, missing_in_gmail)
        print(f"\n[WARN] 不一致を検出。LINEで通知します。")
        send_line_message(token, user_id, msg)

    # ---- 6. 翌日のリマインド送信 -----------------------------------------
    tomorrow = date.today() + timedelta(days=1)
    time_min = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, tzinfo=JST).isoformat()
    time_max = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 23, 59, tzinfo=JST).isoformat()

    tomorrow_events = []
    calendars = cal_service.calendarList().list().execute().get("items", [])
    for cal in calendars:
        result = cal_service.events().list(
            calendarId=cal["id"],
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        for event in result.get("items", []):
            summary = event.get("summary", "")
            if KEYWORD.lower() in summary.lower():
                tomorrow_events.append(event)

    if tomorrow_events:
        print(f"\n[INFO] 翌日に {len(tomorrow_events)} 件の予定。リマインドを送信します。")
        msg = build_reminder_message(tomorrow_events)
        print(f"送信メッセージ:\n{msg}")
        send_line_message(token, user_id, msg)
    else:
        print(f"\n[INFO] 翌日に「{KEYWORD}」の予定はありません。リマインドをスキップします。")


if __name__ == "__main__":
    main()
