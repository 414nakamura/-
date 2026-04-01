"""
Google Business Profile レポート生成スクリプト
取得データ:
  - インプレッション・クリック数
  - クチコミ・評価
  - 写真・投稿
  - 電話・経路案内

必要なパッケージ:
  pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client requests

使い方:
  1. Google Cloud Console でプロジェクトを作成し OAuth 2.0 クライアント ID を取得
  2. credentials.json をこのスクリプトと同じディレクトリに配置
  3. python gbp_report.py --account <アカウントID> --location <ロケーションID> --days 30
"""

import argparse
import csv
import json
import os
import sys
from datetime import date, timedelta

import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# ---- 設定 ----------------------------------------------------------------

SCOPES = [
    "https://www.googleapis.com/auth/business.manage",
]

TOKEN_FILE = "token.json"
CREDENTIALS_FILE = "credentials.json"

PERF_API = "https://businessprofileperformance.googleapis.com/v1"
MYB_API = "https://mybusiness.googleapis.com/v4"

# ---- 認証 ----------------------------------------------------------------

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


def auth_header(creds: Credentials) -> dict:
    return {"Authorization": f"Bearer {creds.token}"}


# ---- パフォーマンス指標 (インプレッション・クリック・電話・経路案内) --------

DAILY_METRICS = [
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
    "CALL_CLICKS",
    "WEBSITE_CLICKS",
    "BUSINESS_DIRECTION_REQUESTS",
    "BUSINESS_CONVERSATIONS",
]

METRIC_LABELS = {
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS": "インプレッション(PCマップ)",
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH": "インプレッション(PC検索)",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS": "インプレッション(スマホマップ)",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH": "インプレッション(スマホ検索)",
    "CALL_CLICKS": "電話タップ数",
    "WEBSITE_CLICKS": "ウェブサイトクリック数",
    "BUSINESS_DIRECTION_REQUESTS": "経路案内リクエスト数",
    "BUSINESS_CONVERSATIONS": "メッセージ数",
}


def fetch_performance(location_name: str, start: date, end: date, headers: dict) -> dict:
    """businessprofileperformance API から日別指標を一括取得する。"""
    params_list = []
    for m in DAILY_METRICS:
        params_list.append(f"dailyMetrics={m}")
    params_list += [
        f"dailyRange.start_date.year={start.year}",
        f"dailyRange.start_date.month={start.month}",
        f"dailyRange.start_date.day={start.day}",
        f"dailyRange.end_date.year={end.year}",
        f"dailyRange.end_date.month={end.month}",
        f"dailyRange.end_date.day={end.day}",
    ]
    url = f"{PERF_API}/{location_name}/dailyMetrics:batchGet?{'&'.join(params_list)}"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


def parse_performance(data: dict) -> dict:
    """日別データを {date_str: {metric: value}} の辞書に変換する。"""
    daily: dict = {}
    for item in data.get("multiDailyMetricTimeSeries", []):
        metric = item.get("dailyMetric", "")
        label = METRIC_LABELS.get(metric, metric)
        for ts in item.get("dailyMetricTimeSeries", {}).get("datedValues", []):
            d = ts["date"]
            key = f"{d['year']}-{d['month']:02d}-{d['day']:02d}"
            daily.setdefault(key, {})[label] = ts.get("value", 0)
    return daily


# ---- クチコミ ------------------------------------------------------------

def fetch_reviews(location_name: str, headers: dict) -> list:
    """クチコミ一覧を取得する。"""
    reviews = []
    page_token = None
    while True:
        params = {"pageSize": 50}
        if page_token:
            params["pageToken"] = page_token
        url = f"{MYB_API}/{location_name}/reviews"
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        body = resp.json()
        reviews.extend(body.get("reviews", []))
        page_token = body.get("nextPageToken")
        if not page_token:
            break
    return reviews


def summarize_reviews(reviews: list) -> dict:
    """クチコミをレーティング別に集計する。"""
    rating_map = {
        "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
    }
    total = len(reviews)
    if total == 0:
        return {"クチコミ総数": 0, "平均評価": "N/A", "返信済み数": 0}
    scores = [rating_map.get(r.get("starRating", ""), 0) for r in reviews]
    replied = sum(1 for r in reviews if r.get("reviewReply"))
    return {
        "クチコミ総数": total,
        "平均評価": round(sum(scores) / total, 2),
        "返信済み数": replied,
        "未返信数": total - replied,
        "★5": scores.count(5),
        "★4": scores.count(4),
        "★3": scores.count(3),
        "★2": scores.count(2),
        "★1": scores.count(1),
    }


# ---- 写真・投稿 ----------------------------------------------------------

def fetch_media_count(location_name: str, headers: dict) -> dict:
    """メディア(写真)の枚数を取得する。"""
    url = f"{MYB_API}/{location_name}/media"
    resp = requests.get(url, headers=headers, params={"pageSize": 1})
    resp.raise_for_status()
    body = resp.json()
    return {"写真総数": body.get("totalMediaItemCount", 0)}


def fetch_local_posts(location_name: str, headers: dict) -> dict:
    """投稿(ローカルポスト)の件数を取得する。"""
    url = f"{MYB_API}/{location_name}/localPosts"
    resp = requests.get(url, headers=headers, params={"pageSize": 1})
    resp.raise_for_status()
    body = resp.json()
    posts = body.get("localPosts", [])
    return {"投稿数(直近取得)": len(posts)}


# ---- CSV 出力 ------------------------------------------------------------

def write_performance_csv(daily: dict, output_file: str):
    """日別パフォーマンス指標を CSV に書き出す。"""
    all_labels = list(METRIC_LABELS.values())
    rows = sorted(daily.items())
    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["日付"] + all_labels, extrasaction="ignore")
        writer.writeheader()
        for date_str, metrics in rows:
            row = {"日付": date_str}
            for label in all_labels:
                row[label] = metrics.get(label, 0)
            writer.writerow(row)
    print(f"[OK] パフォーマンス指標 → {output_file}")


def write_reviews_csv(reviews: list, output_file: str):
    """クチコミ一覧を CSV に書き出す。"""
    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["投稿日時", "評価(星)", "コメント", "返信有無", "返信内容"],
            extrasaction="ignore",
        )
        writer.writeheader()
        rating_map = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
        for r in reviews:
            comment = r.get("comment", "")
            reply = r.get("reviewReply", {})
            writer.writerow({
                "投稿日時": r.get("createTime", ""),
                "評価(星)": rating_map.get(r.get("starRating", ""), ""),
                "コメント": comment,
                "返信有無": "有" if reply else "無",
                "返信内容": reply.get("comment", "") if reply else "",
            })
    print(f"[OK] クチコミ一覧 → {output_file}")


def write_summary_csv(review_summary: dict, media: dict, posts: dict, output_file: str):
    """サマリー(クチコミ集計・写真・投稿)を CSV に書き出す。"""
    summary = {**review_summary, **media, **posts}
    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["項目", "値"])
        for k, v in summary.items():
            writer.writerow([k, v])
    print(f"[OK] サマリー → {output_file}")


# ---- メイン --------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Google Business Profile レポート生成")
    parser.add_argument(
        "--account", required=True,
        help="アカウントID (例: accounts/123456789)",
    )
    parser.add_argument(
        "--location", required=True,
        help="ロケーションID (例: locations/987654321)",
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="過去何日分を取得するか (デフォルト: 30)",
    )
    parser.add_argument(
        "--output-dir", default=".",
        help="CSV 出力先ディレクトリ (デフォルト: カレント)",
    )
    args = parser.parse_args()

    location_name = f"{args.account}/{args.location}"
    end_date = date.today() - timedelta(days=1)   # 昨日まで
    start_date = end_date - timedelta(days=args.days - 1)

    print(f"対象期間: {start_date} 〜 {end_date}")
    print("認証中...")
    creds = get_credentials()
    headers = auth_header(creds)

    os.makedirs(args.output_dir, exist_ok=True)

    # パフォーマンス指標
    print("パフォーマンス指標を取得中...")
    perf_raw = fetch_performance(location_name, start_date, end_date, headers)
    daily = parse_performance(perf_raw)
    write_performance_csv(
        daily,
        os.path.join(args.output_dir, "gbp_performance.csv"),
    )

    # クチコミ
    print("クチコミを取得中...")
    reviews = fetch_reviews(location_name, headers)
    review_summary = summarize_reviews(reviews)
    write_reviews_csv(
        reviews,
        os.path.join(args.output_dir, "gbp_reviews.csv"),
    )

    # 写真・投稿
    print("写真・投稿情報を取得中...")
    media = fetch_media_count(location_name, headers)
    posts = fetch_local_posts(location_name, headers)

    # サマリー
    write_summary_csv(
        review_summary,
        media,
        posts,
        os.path.join(args.output_dir, "gbp_summary.csv"),
    )

    print("\n=== クチコミ集計 ===")
    for k, v in review_summary.items():
        print(f"  {k}: {v}")
    print("\n=== 写真・投稿 ===")
    for k, v in {**media, **posts}.items():
        print(f"  {k}: {v}")
    print("\n完了しました。")


if __name__ == "__main__":
    main()
