"""gbp_report.py のユニットテスト"""

import sys
import os
from unittest.mock import MagicMock

# google.auth 系モジュールをモックしてインポートエラーを回避
for mod in [
    "google", "google.oauth2", "google.oauth2.credentials",
    "google_auth_oauthlib", "google_auth_oauthlib.flow",
    "google.auth", "google.auth.transport", "google.auth.transport.requests",
]:
    sys.modules.setdefault(mod, MagicMock())

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from gbp_report import parse_performance, summarize_reviews, METRIC_LABELS, DAILY_METRICS


class TestParsePerformance:
    def test_empty_data(self):
        assert parse_performance({}) == {}

    def test_single_metric(self):
        data = {
            "multiDailyMetricTimeSeries": [
                {
                    "dailyMetric": "CALL_CLICKS",
                    "dailyMetricTimeSeries": {
                        "datedValues": [
                            {"date": {"year": 2024, "month": 1, "day": 15}, "value": 5},
                        ]
                    },
                }
            ]
        }
        result = parse_performance(data)
        assert "2024-01-15" in result
        assert result["2024-01-15"]["電話タップ数"] == 5

    def test_multiple_dates(self):
        data = {
            "multiDailyMetricTimeSeries": [
                {
                    "dailyMetric": "WEBSITE_CLICKS",
                    "dailyMetricTimeSeries": {
                        "datedValues": [
                            {"date": {"year": 2024, "month": 3, "day": 1}, "value": 10},
                            {"date": {"year": 2024, "month": 3, "day": 2}, "value": 20},
                        ]
                    },
                }
            ]
        }
        result = parse_performance(data)
        assert len(result) == 2
        assert result["2024-03-01"]["ウェブサイトクリック数"] == 10
        assert result["2024-03-02"]["ウェブサイトクリック数"] == 20

    def test_missing_value_defaults_to_zero(self):
        data = {
            "multiDailyMetricTimeSeries": [
                {
                    "dailyMetric": "CALL_CLICKS",
                    "dailyMetricTimeSeries": {
                        "datedValues": [
                            {"date": {"year": 2024, "month": 1, "day": 1}},
                        ]
                    },
                }
            ]
        }
        result = parse_performance(data)
        assert result["2024-01-01"]["電話タップ数"] == 0

    def test_unknown_metric_uses_raw_name(self):
        data = {
            "multiDailyMetricTimeSeries": [
                {
                    "dailyMetric": "UNKNOWN_METRIC",
                    "dailyMetricTimeSeries": {
                        "datedValues": [
                            {"date": {"year": 2024, "month": 6, "day": 1}, "value": 3},
                        ]
                    },
                }
            ]
        }
        result = parse_performance(data)
        assert result["2024-06-01"]["UNKNOWN_METRIC"] == 3

    def test_date_zero_padding(self):
        data = {
            "multiDailyMetricTimeSeries": [
                {
                    "dailyMetric": "CALL_CLICKS",
                    "dailyMetricTimeSeries": {
                        "datedValues": [
                            {"date": {"year": 2024, "month": 5, "day": 9}, "value": 1},
                        ]
                    },
                }
            ]
        }
        result = parse_performance(data)
        assert "2024-05-09" in result


class TestSummarizeReviews:
    def test_empty_reviews(self):
        result = summarize_reviews([])
        assert result["クチコミ総数"] == 0
        assert result["平均評価"] == "N/A"
        assert result["返信済み数"] == 0

    def test_single_five_star_no_reply(self):
        reviews = [{"starRating": "FIVE"}]
        result = summarize_reviews(reviews)
        assert result["クチコミ総数"] == 1
        assert result["平均評価"] == 5.0
        assert result["返信済み数"] == 0
        assert result["未返信数"] == 1
        assert result["★5"] == 1

    def test_average_calculation(self):
        reviews = [
            {"starRating": "FIVE"},
            {"starRating": "THREE"},
        ]
        result = summarize_reviews(reviews)
        assert result["平均評価"] == 4.0

    def test_reply_counting(self):
        reviews = [
            {"starRating": "FOUR", "reviewReply": {"comment": "ありがとうございます"}},
            {"starRating": "TWO"},
        ]
        result = summarize_reviews(reviews)
        assert result["返信済み数"] == 1
        assert result["未返信数"] == 1

    def test_all_star_counts(self):
        reviews = [
            {"starRating": "ONE"},
            {"starRating": "TWO"},
            {"starRating": "THREE"},
            {"starRating": "FOUR"},
            {"starRating": "FIVE"},
        ]
        result = summarize_reviews(reviews)
        for star in ["★1", "★2", "★3", "★4", "★5"]:
            assert result[star] == 1
        assert result["平均評価"] == 3.0

    def test_invalid_rating_treated_as_zero(self):
        reviews = [{"starRating": "INVALID"}]
        result = summarize_reviews(reviews)
        assert result["クチコミ総数"] == 1
        assert result["平均評価"] == 0.0


class TestMetricLabels:
    def test_all_daily_metrics_have_labels(self):
        for metric in DAILY_METRICS:
            assert metric in METRIC_LABELS, f"{metric} のラベルが未定義"
