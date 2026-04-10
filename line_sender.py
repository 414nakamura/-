"""
自動LINE送信スクリプト（LINE Notify 使用）

使い方:
  1. LINE Notify トークンを取得: https://notify-bot.line.me/ja/
  2. 環境変数に設定:
       export LINE_NOTIFY_TOKEN="your_token_here"
  3. 実行:
       python line_sender.py
  4. cron で定期実行する場合の例（毎朝9時）:
       0 9 * * * /usr/bin/python3 /path/to/line_sender.py
"""

import os
import sys
import requests
from datetime import datetime


LINE_NOTIFY_URL = "https://notify-api.line.me/api/notify"


def send_line_notify(token: str, message: str) -> bool:
    """LINE Notify でメッセージを送信する。"""
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": message}

    try:
        response = requests.post(LINE_NOTIFY_URL, headers=headers, data=payload, timeout=10)
        response.raise_for_status()
        print(f"[OK] 送信成功: {response.status_code}")
        return True
    except requests.exceptions.HTTPError as e:
        print(f"[ERROR] HTTPエラー: {e} (ステータス: {response.status_code})", file=sys.stderr)
    except requests.exceptions.ConnectionError:
        print("[ERROR] 接続エラー: ネットワークを確認してください", file=sys.stderr)
    except requests.exceptions.Timeout:
        print("[ERROR] タイムアウト: LINE Notify への接続がタイムアウトしました", file=sys.stderr)
    return False


def build_message() -> str:
    """送信する動的メッセージを組み立てる。"""
    now = datetime.now()
    date_str = now.strftime("%Y年%m月%d日 %H:%M")
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    weekday = weekdays[now.weekday()]

    message = (
        f"\n【定期通知】\n"
        f"日時: {date_str}（{weekday}）\n"
        f"\n"
        f"本日もよろしくお願いします。\n"
        f"何か変更があればこちらに通知します。"
    )
    return message


def main():
    token = os.environ.get("LINE_NOTIFY_TOKEN")
    if not token:
        print(
            "[ERROR] 環境変数 LINE_NOTIFY_TOKEN が設定されていません。\n"
            "  export LINE_NOTIFY_TOKEN='your_token_here'",
            file=sys.stderr,
        )
        sys.exit(1)

    message = build_message()
    print(f"送信メッセージ:\n{message}\n")

    success = send_line_notify(token, message)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
