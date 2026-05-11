# Google Business Profile レポートツール

Google Business Profile (GBP) のデータを取得し、CSV レポートを生成するツールです。

## 取得できるデータ

| カテゴリ | 内容 |
|---|---|
| パフォーマンス指標 | インプレッション・クリック数・電話タップ・経路案内リクエスト |
| クチコミ | 評価・コメント・返信状況の一覧と集計 |
| 写真・投稿 | 写真総数・投稿件数 |

## セットアップ

### 1. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

### 2. Google Cloud Console での設定

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成
2. **APIs & Services > Enabled APIs** から以下を有効化:
   - My Business Business Information API
   - My Business Performance API
3. **APIs & Services > Credentials** で OAuth 2.0 クライアント ID を作成
   - アプリケーションの種類: **デスクトップアプリ**
4. ダウンロードした JSON を `credentials.json` としてこのディレクトリに配置

`credentials.example.json` を参考にしてください。

### 3. アカウント ID・ロケーション ID の確認

```bash
# 認証後、アカウント一覧を確認する場合は GBP API を直接呼び出すか、
# Google Business Profile 管理画面の URL から取得してください
# 例: https://business.google.com/n/123456789/profile
```

## 使い方

```bash
python gbp_report.py \
  --account accounts/123456789 \
  --location locations/987654321 \
  --days 30 \
  --output-dir ./output
```

### オプション

| オプション | 説明 | デフォルト |
|---|---|---|
| `--account` | アカウント ID (`accounts/XXXXXXXXX`) | 必須 |
| `--location` | ロケーション ID (`locations/XXXXXXXXX`) | 必須 |
| `--days` | 過去何日分を取得するか | `30` |
| `--output-dir` | CSV 出力先ディレクトリ | `.` (カレント) |

### 初回実行時

ブラウザが自動的に開き、Google アカウントへのアクセス許可を求めます。  
承認すると `token.json` が生成され、次回以降は自動的に認証されます。

## 出力ファイル

| ファイル名 | 内容 |
|---|---|
| `gbp_performance.csv` | 日別パフォーマンス指標 |
| `gbp_reviews.csv` | クチコミ一覧 |
| `gbp_summary.csv` | クチコミ集計・写真・投稿のサマリー |

## テスト

```bash
pip install pytest
pytest tests/
```

## 注意事項

- `credentials.json` と `token.json` は `.gitignore` で除外されています。リポジトリにコミットしないでください。
- Google Business Profile Performance API の無料枠には呼び出し制限があります。
- `mybusiness.googleapis.com/v4` エンドポイントは一部の機能で非推奨になっています。新しい API バージョンへの移行を検討してください。
