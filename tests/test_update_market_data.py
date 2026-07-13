import json
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import update_market_data as updater


def monthly_records(end_year=2026, end_month=7, count=140):
    end_index = end_year * 12 + (end_month - 1)
    records = []
    for offset in range(count - 1, -1, -1):
        month_index = end_index - offset
        year, zero_based_month = divmod(month_index, 12)
        value = 18 + ((count - offset) % 17) * 0.7
        records.append(
            {
                "date": date(year, zero_based_month + 1, 1).isoformat(),
                "pe": round(value, 4),
            }
        )
    return records


def source_html(records):
    points = []
    for record in records:
        point_date = date.fromisoformat(record["date"])
        points.append(
            f"[Date.UTC({point_date.year}, {point_date.month - 1}, "
            f"{point_date.day}),{record['pe']}]"
        )
    return "<script>detailPE_data = [" + ",".join(points) + "];</script>"


def daily_records(end=date(2026, 7, 10), count=260, start_value=1000.0):
    dates = []
    cursor = end
    while len(dates) < count:
        if cursor.weekday() < 5:
            dates.append(cursor)
        cursor -= timedelta(days=1)
    dates.reverse()
    return [
        {"date": point_date.isoformat(), "close": start_value + index * 0.5}
        for index, point_date in enumerate(dates)
    ]


def nasdaq_json(records):
    rows = [
        {
            "date": date.fromisoformat(point["date"]).strftime("%m/%d/%Y"),
            "close": f"{point['close']:,.2f}",
        }
        for point in reversed(records)
    ]
    return json.dumps({"data": {"tradesTable": {"rows": rows}}})


def cboe_csv(records, close_field):
    if close_field == "SPX":
        lines = ["DATE,SPX"]
        lines.extend(
            f"{date.fromisoformat(point['date']).strftime('%m/%d/%Y')},{point['close']:.6f}"
            for point in records
        )
        return "\n".join(lines)
    lines = ["DATE,OPEN,HIGH,LOW,CLOSE"]
    lines.extend(
        f"{date.fromisoformat(point['date']).strftime('%m/%d/%Y')},"
        f"{point['close']:.6f},{point['close']:.6f},"
        f"{point['close']:.6f},{point['close']:.6f}"
        for point in records
    )
    return "\n".join(lines)


def all_sources_fetcher(url, _timeout):
    if "worldperatio.com" in url:
        return source_html(monthly_records())
    if "api.nasdaq.com" in url:
        return nasdaq_json(daily_records(start_value=25000.0))
    if "SPX_History.csv" in url:
        return cboe_csv(daily_records(start_value=5500.0), "SPX")
    if "VIX_History.csv" in url:
        return cboe_csv(daily_records(count=30, start_value=15.0), "CLOSE")
    if "VXN_History.csv" in url:
        return cboe_csv(daily_records(count=30, start_value=24.0), "CLOSE")
    raise AssertionError(f"unexpected source URL: {url}")


class UpdateMarketDataTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.cache_dir = self.root / "data" / "cache"
        self.market_data_path = self.root / "data" / "market-data.json"
        self.market_data_script_path = self.root / "data" / "market-data.js"
        self.patches = (
            patch.object(updater, "CACHE_DIR", self.cache_dir),
            patch.object(updater, "MARKET_DATA_PATH", self.market_data_path),
            patch.object(
                updater, "MARKET_DATA_SCRIPT_PATH", self.market_data_script_path
            ),
        )
        for current_patch in self.patches:
            current_patch.start()

    def tearDown(self):
        for current_patch in reversed(self.patches):
            current_patch.stop()
        self.temporary_directory.cleanup()

    def test_parser_and_percentile_use_latest_120_months(self):
        records = monthly_records()
        parsed = updater.parse_world_pe_history(source_html(records))

        updater.validate_history(
            parsed, today=date(2026, 7, 13), require_fresh=True
        )
        summary = updater.calculate_pe_summary(parsed)

        self.assertEqual(len(parsed), 140)
        self.assertEqual(summary["data_date"], "2026-07-01")
        self.assertEqual(summary["sample_count"], 120)
        expected_rank = sum(
            point["pe"] <= parsed[-1]["pe"] for point in parsed[-120:]
        )
        self.assertEqual(
            summary["ten_year_percentile"], round(expected_rank / 120 * 100, 1)
        )

    def test_validation_rejects_bad_values_and_date_order(self):
        records = monthly_records()
        records[20]["pe"] = -1
        with self.assertRaisesRegex(updater.MarketDataError, "PE must be positive"):
            updater.validate_history(
                records, today=date(2026, 7, 13), require_fresh=True
            )

        records = monthly_records()
        records[20], records[21] = records[21], records[20]
        with self.assertRaisesRegex(updater.MarketDataError, "strictly increasing"):
            updater.validate_history(
                records, today=date(2026, 7, 13), require_fresh=True
            )

    def test_successful_refresh_writes_valid_raw_cache_atomically(self):
        records = monthly_records()
        now = datetime(2026, 7, 13, 2, 0, tzinfo=timezone.utc)
        config = updater.INDEX_CONFIGS[0]

        entry = updater.refresh_index(
            config,
            now=now,
            max_cache_age=timedelta(hours=24),
            timeout=5,
            force=True,
            fetcher=lambda _url, _timeout: source_html(records),
        )

        cache = json.loads(config.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(entry["status"], "source-refreshed")
        self.assertFalse(entry["is_cached"])
        self.assertEqual(cache["record_count"], 140)
        self.assertEqual(cache["latest_data_date"], "2026-07-01")
        self.assertEqual(list(config.cache_path.parent.glob("*.tmp")), [])

    def test_refresh_failure_uses_last_valid_cache_without_overwriting_it(self):
        records = monthly_records()
        now = datetime(2026, 7, 13, 2, 0, tzinfo=timezone.utc)
        config = updater.INDEX_CONFIGS[0]
        cached = updater.build_cache_payload(config, records, now - timedelta(days=2))
        updater.atomic_write_json(config.cache_path, cached)
        original = config.cache_path.read_text(encoding="utf-8")

        def failing_fetcher(_url, _timeout):
            raise OSError("simulated timeout")

        entry = updater.refresh_index(
            config,
            now=now,
            max_cache_age=timedelta(hours=24),
            timeout=5,
            force=True,
            fetcher=failing_fetcher,
        )

        self.assertEqual(entry["status"], "cache-fallback")
        self.assertTrue(entry["is_cached"])
        self.assertTrue(entry["is_stale"])
        self.assertIn("simulated timeout", entry["warning"])
        self.assertEqual(config.cache_path.read_text(encoding="utf-8"), original)

    def test_update_publishes_json_and_direct_file_compatibility_script(self):
        now = datetime(2026, 7, 13, 2, 0, tzinfo=timezone.utc)

        payload, missing = updater.update_market_data(
            now=now,
            max_cache_age=timedelta(hours=24),
            timeout=5,
            force=True,
            fetcher=all_sources_fetcher,
        )

        self.assertEqual(missing, [])
        self.assertEqual(set(payload["indices"]), {"ndx", "spx"})
        self.assertEqual(payload["indices"]["ndx"]["market"]["vxn"], 38.5)
        self.assertEqual(payload["indices"]["spx"]["market"]["vix"], 29.5)
        self.assertEqual(
            payload["indices"]["ndx"]["market"]["ma200_sample_count"], 200
        )
        published = json.loads(self.market_data_path.read_text(encoding="utf-8"))
        self.assertEqual(published, payload)
        script = self.market_data_script_path.read_text(encoding="utf-8")
        self.assertTrue(script.startswith("window.MARKET_DATA = "))
        self.assertTrue(script.endswith(";\n"))

    def test_existing_application_data_survives_when_source_and_raw_cache_fail(self):
        now = datetime(2026, 7, 13, 2, 0, tzinfo=timezone.utc)
        first_payload, first_missing = updater.update_market_data(
            now=now - timedelta(days=2),
            max_cache_age=timedelta(hours=24),
            timeout=5,
            force=True,
            fetcher=all_sources_fetcher,
        )
        self.assertEqual(first_missing, [])
        self.assertIn("market", first_payload["indices"]["ndx"])
        shutil.rmtree(self.cache_dir)

        def failing_fetcher(_url, _timeout):
            raise OSError("source unavailable")

        payload, missing = updater.update_market_data(
            now=now,
            max_cache_age=timedelta(hours=24),
            timeout=5,
            force=True,
            fetcher=failing_fetcher,
        )

        self.assertEqual(missing, [])
        for entry in payload["indices"].values():
            self.assertEqual(entry["status"], "application-cache-fallback")
            self.assertTrue(entry["is_cached"])
            self.assertTrue(entry["is_stale"])
            self.assertIn("source unavailable", entry["warning"])
            self.assertEqual(entry["pe"]["sample_count"], 120)
            self.assertEqual(entry["market"]["status"], "application-cache-fallback")
            self.assertTrue(entry["market"]["is_stale"])

    def test_daily_parsers_validate_and_calculate_ma200(self):
        records = daily_records(start_value=25000.0)
        config = updater.DAILY_SERIES_CONFIGS[0]
        parsed = updater.parse_nasdaq_daily_history(nasdaq_json(records))
        updater.validate_daily_history(
            parsed, config, today=date(2026, 7, 13), require_fresh=True
        )
        cache = updater.build_daily_cache_payload(
            config,
            parsed,
            datetime(2026, 7, 13, 2, 0, tzinfo=timezone.utc),
        )
        entry = updater.build_daily_application_entry(
            config,
            cache,
            status="source-refreshed",
            is_cached=False,
            is_stale=False,
            warning=None,
        )

        expected_ma200 = sum(point["close"] for point in records[-200:]) / 200
        self.assertEqual(entry["data"]["ma200"], expected_ma200)
        self.assertEqual(entry["data"]["current"], records[-1]["close"])

        cboe_records = updater.parse_cboe_daily_history(
            cboe_csv(daily_records(count=30, start_value=15.0), "CLOSE"),
            "CLOSE",
        )
        self.assertEqual(len(cboe_records), 30)
        self.assertEqual(cboe_records[-1]["close"], 29.5)


if __name__ == "__main__":
    unittest.main()
