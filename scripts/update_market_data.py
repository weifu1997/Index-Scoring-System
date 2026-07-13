#!/usr/bin/env python3
"""Fetch valuation and daily market histories, cache them, and publish frontend data."""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache"
MARKET_DATA_PATH = ROOT / "data" / "market-data.json"
MARKET_DATA_SCRIPT_PATH = ROOT / "data" / "market-data.js"

SCHEMA_VERSION = 1
PERCENTILE_SAMPLE_COUNT = 120
MIN_HISTORY_SAMPLES = 120
MAX_DATA_AGE_DAYS = 62
MAX_DAILY_DATA_AGE_DAYS = 7
MAX_MONTHLY_GAP_DAYS = 62
MIN_TEN_YEAR_SPAN_DAYS = 9 * 365
DEFAULT_CACHE_MAX_AGE_HOURS = 24
DEFAULT_TIMEOUT_SECONDS = 45
CHINA_TIMEZONE = timezone(timedelta(hours=8))

HISTORY_BLOCK_RE = re.compile(
    r"detailPE_data\s*=\s*\[(.*?)\];", re.IGNORECASE | re.DOTALL
)
HISTORY_POINT_RE = re.compile(
    r"Date\.UTC\(\s*(\d{4})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})\s*\)"
    r"\s*,\s*([0-9]+(?:\.[0-9]+)?)"
)


@dataclass(frozen=True)
class IndexConfig:
    key: str
    symbol: str
    name: str
    source_name: str
    source_url: str

    @property
    def cache_path(self) -> Path:
        return CACHE_DIR / f"{self.key}_pe_history.json"


@dataclass(frozen=True)
class DailySeriesConfig:
    key: str
    symbol: str
    name: str
    source_name: str
    source_url: str
    parser: str
    close_field: str
    minimum_samples: int
    cache_samples: int

    @property
    def cache_path(self) -> Path:
        return CACHE_DIR / f"{self.key}_history.json"


INDEX_CONFIGS = (
    IndexConfig(
        key="ndx",
        symbol="NDX",
        name="Nasdaq-100",
        source_name="World PE Ratio",
        source_url="https://worldperatio.com/index/nasdaq-100/",
    ),
    IndexConfig(
        key="spx",
        symbol="SPX",
        name="S&P 500",
        source_name="World PE Ratio",
        source_url="https://worldperatio.com/index/sp-500/",
    ),
)

DAILY_SERIES_CONFIGS = (
    DailySeriesConfig(
        key="ndx_price",
        symbol="NDX",
        name="Nasdaq-100 close",
        source_name="Nasdaq",
        source_url=(
            "https://api.nasdaq.com/api/quote/NDX/historical?assetclass=index"
            "&fromdate={fromdate}&todate={todate}&limit=400"
        ),
        parser="nasdaq",
        close_field="close",
        minimum_samples=200,
        cache_samples=260,
    ),
    DailySeriesConfig(
        key="spx_price",
        symbol="SPX",
        name="S&P 500 close",
        source_name="Cboe Global Markets",
        source_url="https://cdn.cboe.com/api/global/us_indices/daily_prices/SPX_History.csv",
        parser="cboe",
        close_field="SPX",
        minimum_samples=200,
        cache_samples=260,
    ),
    DailySeriesConfig(
        key="vix",
        symbol="VIX",
        name="Cboe Volatility Index",
        source_name="Cboe Global Markets",
        source_url="https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
        parser="cboe",
        close_field="CLOSE",
        minimum_samples=20,
        cache_samples=30,
    ),
    DailySeriesConfig(
        key="vxn",
        symbol="VXN",
        name="Cboe Nasdaq-100 Volatility Index",
        source_name="Cboe Global Markets",
        source_url="https://cdn.cboe.com/api/global/us_indices/daily_prices/VXN_History.csv",
        parser="cboe",
        close_field="CLOSE",
        minimum_samples=20,
        cache_samples=30,
    ),
)

MARKET_REQUIREMENTS = {
    "ndx": ("ndx_price", "vix", "vxn"),
    "spx": ("spx_price", "vix"),
}


class MarketDataError(RuntimeError):
    """Raised when fetched or cached market data is unusable."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def china_calendar_date(value: datetime) -> date:
    return value.astimezone(CHINA_TIMEZONE).date()


def china_verification_window(value: datetime) -> tuple[date, int]:
    local_value = value.astimezone(CHINA_TIMEZONE)
    # U.S. regular trading is complete by 05:00 China time in both DST seasons.
    return local_value.date(), 1 if local_value.hour >= 5 else 0


def fetch_text(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/json,text/csv,*/*",
            "Accept-Encoding": "gzip",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = response.read()
        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            payload = gzip.decompress(payload)
        encoding = response.headers.get_content_charset() or "utf-8"
        return payload.decode(encoding, errors="replace")


# Backward-compatible name used by the PE-specific refresh path and tests.
fetch_html = fetch_text


def parse_world_pe_history(html: str) -> list[dict[str, Any]]:
    block_match = HISTORY_BLOCK_RE.search(html)
    if not block_match:
        raise MarketDataError("source page does not contain detailPE_data")

    records: list[dict[str, Any]] = []
    for match in HISTORY_POINT_RE.finditer(block_match.group(1)):
        year, zero_based_month, day, pe = match.groups()
        try:
            point_date = date(int(year), int(zero_based_month) + 1, int(day))
        except ValueError as exc:
            raise MarketDataError(f"invalid source date: {match.group(0)}") from exc
        records.append({"date": point_date.isoformat(), "pe": float(pe)})

    if not records:
        raise MarketDataError("source page contains no PE history points")
    return records


def daily_source_url(config: DailySeriesConfig, today: date) -> str:
    if config.parser != "nasdaq":
        return config.source_url
    return config.source_url.format(
        fromdate=(today - timedelta(days=450)).isoformat(),
        todate=today.isoformat(),
    )


def parse_nasdaq_daily_history(text: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(text)
        rows = payload["data"]["tradesTable"]["rows"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise MarketDataError("Nasdaq response does not contain historical rows") from exc
    if not isinstance(rows, list):
        raise MarketDataError("Nasdaq historical rows must be a list")

    records: list[dict[str, Any]] = []
    for row in rows:
        try:
            point_date = datetime.strptime(str(row["date"]), "%m/%d/%Y").date()
            close = float(str(row["close"]).replace(",", ""))
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError("invalid Nasdaq historical row") from exc
        records.append({"date": point_date.isoformat(), "close": close})
    records.sort(key=lambda point: point["date"])
    return records


def parse_cboe_daily_history(
    text: str, close_field: str
) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise MarketDataError("Cboe CSV is missing headers")
    normalized_fields = {field.strip().upper(): field for field in reader.fieldnames}
    date_key = normalized_fields.get("DATE")
    close_key = normalized_fields.get(close_field.upper())
    if not date_key or not close_key:
        raise MarketDataError(
            f"Cboe CSV is missing DATE or {close_field} column"
        )

    records: list[dict[str, Any]] = []
    for row in reader:
        raw_date = str(row.get(date_key, "")).strip()
        raw_close = str(row.get(close_key, "")).strip()
        if not raw_date and not raw_close:
            continue
        try:
            point_date = datetime.strptime(raw_date, "%m/%d/%Y").date()
            close = float(raw_close.replace(",", ""))
        except ValueError as exc:
            raise MarketDataError("invalid Cboe historical row") from exc
        records.append({"date": point_date.isoformat(), "close": close})
    records.sort(key=lambda point: point["date"])
    return records


def parse_daily_history(config: DailySeriesConfig, text: str) -> list[dict[str, Any]]:
    if config.parser == "nasdaq":
        return parse_nasdaq_daily_history(text)
    if config.parser == "cboe":
        return parse_cboe_daily_history(text, config.close_field)
    raise MarketDataError(f"unsupported daily parser: {config.parser}")


def validate_daily_history(
    records: list[dict[str, Any]],
    config: DailySeriesConfig,
    *,
    today: date,
    require_fresh: bool,
) -> None:
    if len(records) < config.minimum_samples:
        raise MarketDataError(
            f"{config.symbol} history has {len(records)} samples; "
            f"at least {config.minimum_samples} required"
        )

    previous_date: date | None = None
    latest_date: date | None = None
    for position, record in enumerate(records):
        try:
            point_date = date.fromisoformat(str(record["date"]))
            close = float(record["close"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError(
                f"invalid {config.symbol} record at position {position}"
            ) from exc
        if not math.isfinite(close) or close <= 0:
            raise MarketDataError(
                f"{config.symbol} close must be positive at {point_date.isoformat()}"
            )
        if previous_date is not None and point_date <= previous_date:
            raise MarketDataError(
                f"{config.symbol} dates must be unique and strictly increasing"
            )
        if point_date > today + timedelta(days=1):
            raise MarketDataError(f"{config.symbol} history contains a future date")
        previous_date = point_date
        latest_date = point_date

    if require_fresh and latest_date is not None:
        age_days = (today - latest_date).days
        if age_days > MAX_DAILY_DATA_AGE_DAYS:
            raise MarketDataError(
                f"latest {config.symbol} data is {age_days} days old; "
                f"limit is {MAX_DAILY_DATA_AGE_DAYS}"
            )


def validate_history(
    records: list[dict[str, Any]],
    *,
    today: date,
    require_fresh: bool,
) -> None:
    if len(records) < MIN_HISTORY_SAMPLES:
        raise MarketDataError(
            f"history has {len(records)} samples; at least {MIN_HISTORY_SAMPLES} required"
        )

    parsed_dates: list[date] = []
    previous_date: date | None = None
    for position, record in enumerate(records):
        try:
            point_date = date.fromisoformat(str(record["date"]))
            pe = float(record["pe"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError(f"invalid record at position {position}") from exc

        if not math.isfinite(pe) or pe <= 0:
            raise MarketDataError(f"PE must be positive at {point_date.isoformat()}")
        if previous_date is not None and point_date <= previous_date:
            raise MarketDataError("history dates must be unique and strictly increasing")
        if point_date > today + timedelta(days=1):
            raise MarketDataError("history contains a future data date")

        parsed_dates.append(point_date)
        previous_date = point_date

    window_dates = parsed_dates[-PERCENTILE_SAMPLE_COUNT:]
    if (window_dates[-1] - window_dates[0]).days < MIN_TEN_YEAR_SPAN_DAYS:
        raise MarketDataError("latest 120 samples do not span approximately ten years")
    for earlier, later in zip(window_dates, window_dates[1:]):
        if (later - earlier).days > MAX_MONTHLY_GAP_DAYS:
            raise MarketDataError(
                f"monthly history gap exceeds {MAX_MONTHLY_GAP_DAYS} days"
            )

    age_days = (today - parsed_dates[-1]).days
    if require_fresh and age_days > MAX_DATA_AGE_DAYS:
        raise MarketDataError(
            f"latest PE data is {age_days} days old; limit is {MAX_DATA_AGE_DAYS}"
        )


def calculate_pe_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    window = records[-PERCENTILE_SAMPLE_COUNT:]
    current_pe = float(window[-1]["pe"])
    rank = sum(float(point["pe"]) <= current_pe for point in window)
    return {
        "data_date": window[-1]["date"],
        "current": round(current_pe, 4),
        "ten_year_percentile": round(rank / len(window) * 100, 1),
        "sample_count": len(window),
        "sampling_frequency": "monthly",
        "percentile_method": "inclusive_empirical_cdf",
    }


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, serialized)


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as source_file:
            payload = json.load(source_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketDataError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MarketDataError(f"{path} must contain a JSON object")
    return payload


def validate_cache_metadata(
    payload: dict[str, Any], records: list[dict[str, Any]], path: Path
) -> None:
    try:
        parse_iso_datetime(str(payload["fetched_at"]))
        latest_data_date = date.fromisoformat(str(payload["latest_data_date"]))
        record_count = int(payload["record_count"])
        actual_latest_date = date.fromisoformat(str(records[-1]["date"]))
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise MarketDataError(f"invalid cache metadata in {path}") from exc
    if record_count != len(records):
        raise MarketDataError(f"cache record count mismatch in {path}")
    if latest_data_date != actual_latest_date:
        raise MarketDataError(f"cache latest date mismatch in {path}")


def load_history_cache(config: IndexConfig, today: date) -> dict[str, Any]:
    payload = load_json(config.cache_path)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise MarketDataError(f"unsupported cache schema in {config.cache_path}")
    if payload.get("index") != config.symbol:
        raise MarketDataError(f"cache index mismatch in {config.cache_path}")
    records = payload.get("records")
    if not isinstance(records, list):
        raise MarketDataError(f"cache records missing in {config.cache_path}")
    validate_history(records, today=today, require_fresh=False)
    validate_cache_metadata(payload, records, config.cache_path)
    return payload


def cache_age(cache: dict[str, Any], now: datetime) -> timedelta:
    return now - parse_iso_datetime(str(cache["fetched_at"]))


def build_cache_payload(
    config: IndexConfig, records: list[dict[str, Any]], fetched_at: datetime
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "index": config.symbol,
        "index_name": config.name,
        "source": {"name": config.source_name, "url": config.source_url},
        "fetched_at": iso_utc(fetched_at),
        "latest_data_date": records[-1]["date"],
        "record_count": len(records),
        "records": records,
    }


def build_application_entry(
    config: IndexConfig,
    cache: dict[str, Any],
    *,
    status: str,
    is_cached: bool,
    is_stale: bool,
    warning: str | None,
) -> dict[str, Any]:
    return {
        "symbol": config.symbol,
        "name": config.name,
        "pe": calculate_pe_summary(cache["records"]),
        "source": cache["source"],
        "fetched_at": cache["fetched_at"],
        "status": status,
        "is_cached": is_cached,
        "is_stale": is_stale,
        "warning": warning,
    }


def load_daily_cache(
    config: DailySeriesConfig, today: date
) -> dict[str, Any]:
    payload = load_json(config.cache_path)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise MarketDataError(f"unsupported cache schema in {config.cache_path}")
    if payload.get("series") != config.key or payload.get("symbol") != config.symbol:
        raise MarketDataError(f"daily cache identity mismatch in {config.cache_path}")
    records = payload.get("records")
    if not isinstance(records, list):
        raise MarketDataError(f"cache records missing in {config.cache_path}")
    validate_daily_history(records, config, today=today, require_fresh=False)
    validate_cache_metadata(payload, records, config.cache_path)
    return payload


def build_daily_cache_payload(
    config: DailySeriesConfig,
    records: list[dict[str, Any]],
    fetched_at: datetime,
) -> dict[str, Any]:
    cached_records = records[-config.cache_samples :]
    return {
        "schema_version": SCHEMA_VERSION,
        "series": config.key,
        "symbol": config.symbol,
        "name": config.name,
        "source": {
            "name": config.source_name,
            "url": daily_source_url(config, fetched_at.date()),
        },
        "fetched_at": iso_utc(fetched_at),
        "latest_data_date": cached_records[-1]["date"],
        "record_count": len(cached_records),
        "records": cached_records,
    }


def build_daily_application_entry(
    config: DailySeriesConfig,
    cache: dict[str, Any],
    *,
    status: str,
    is_cached: bool,
    is_stale: bool,
    warning: str | None,
) -> dict[str, Any]:
    records = cache["records"]
    data: dict[str, Any] = {
        "data_date": records[-1]["date"],
        "current": round(float(records[-1]["close"]), 4),
    }
    if config.key.endswith("_price"):
        closes = [float(point["close"]) for point in records[-200:]]
        if len(closes) < 200:
            raise MarketDataError(f"{config.symbol} has fewer than 200 closes")
        data["ma200"] = round(sum(closes) / len(closes), 4)
        data["ma200_sample_count"] = len(closes)
    return {
        "series": config.key,
        "symbol": config.symbol,
        "data": data,
        "source": cache["source"],
        "fetched_at": cache["fetched_at"],
        "status": status,
        "is_cached": is_cached,
        "is_stale": is_stale,
        "warning": warning,
    }


def refresh_daily_series(
    config: DailySeriesConfig,
    *,
    now: datetime,
    max_cache_age: timedelta,
    timeout: int,
    force: bool,
    fetcher: Callable[[str, int], str] = fetch_text,
) -> dict[str, Any]:
    cached: dict[str, Any] | None = None
    cache_problem: str | None = None
    if config.cache_path.exists():
        try:
            cached = load_daily_cache(config, now.date())
        except MarketDataError as exc:
            cache_problem = str(exc)

    cache_verified_today = (
        cached is not None
        and china_verification_window(parse_iso_datetime(str(cached["fetched_at"])))
        == china_verification_window(now)
    )
    if (
        cached is not None
        and not force
        and cache_verified_today
        and cache_age(cached, now) <= max_cache_age
    ):
        data_age = (now.date() - date.fromisoformat(cached["latest_data_date"])).days
        return build_daily_application_entry(
            config,
            cached,
            status="cache-fresh",
            is_cached=True,
            is_stale=data_age > MAX_DAILY_DATA_AGE_DAYS,
            warning=None,
        )

    try:
        source_url = daily_source_url(config, now.date())
        text = fetcher(source_url, timeout)
        records = parse_daily_history(config, text)
        validate_daily_history(records, config, today=now.date(), require_fresh=True)
        new_cache = build_daily_cache_payload(config, records, now)
        validate_daily_history(
            new_cache["records"], config, today=now.date(), require_fresh=True
        )
        atomic_write_json(config.cache_path, new_cache)
        return build_daily_application_entry(
            config,
            new_cache,
            status="source-refreshed",
            is_cached=False,
            is_stale=False,
            warning=cache_problem,
        )
    except Exception as exc:
        if cached is None:
            detail = f"; unusable cache: {cache_problem}" if cache_problem else ""
            raise MarketDataError(
                f"{config.symbol} daily refresh failed: {exc}{detail}"
            ) from exc
        return build_daily_application_entry(
            config,
            cached,
            status="cache-fallback",
            is_cached=True,
            is_stale=True,
            warning=f"refresh failed; using last valid cache: {exc}",
        )


def refresh_index(
    config: IndexConfig,
    *,
    now: datetime,
    max_cache_age: timedelta,
    timeout: int,
    force: bool,
    fetcher: Callable[[str, int], str] = fetch_html,
) -> dict[str, Any]:
    cached: dict[str, Any] | None = None
    cache_problem: str | None = None
    if config.cache_path.exists():
        try:
            cached = load_history_cache(config, now.date())
        except MarketDataError as exc:
            cache_problem = str(exc)

    if cached is not None and not force and cache_age(cached, now) <= max_cache_age:
        data_age = (now.date() - date.fromisoformat(cached["latest_data_date"])).days
        return build_application_entry(
            config,
            cached,
            status="cache-fresh",
            is_cached=True,
            is_stale=data_age > MAX_DATA_AGE_DAYS,
            warning=None,
        )

    try:
        html = fetcher(config.source_url, timeout)
        records = parse_world_pe_history(html)
        validate_history(records, today=now.date(), require_fresh=True)
        new_cache = build_cache_payload(config, records, now)
        atomic_write_json(config.cache_path, new_cache)
        return build_application_entry(
            config,
            new_cache,
            status="source-refreshed",
            is_cached=False,
            is_stale=False,
            warning=cache_problem,
        )
    except Exception as exc:  # Network, parsing, and validation all degrade to cache.
        if cached is None:
            detail = f"; unusable cache: {cache_problem}" if cache_problem else ""
            raise MarketDataError(f"{config.symbol} refresh failed: {exc}{detail}") from exc
        return build_application_entry(
            config,
            cached,
            status="cache-fallback",
            is_cached=True,
            is_stale=True,
            warning=f"refresh failed; using last valid cache: {exc}",
        )


def load_existing_market_data() -> dict[str, Any]:
    if not MARKET_DATA_PATH.exists():
        return {}
    try:
        return load_json(MARKET_DATA_PATH)
    except MarketDataError:
        return {}


def stale_application_fallback(
    existing_entry: dict[str, Any], error: MarketDataError
) -> dict[str, Any]:
    fallback = dict(existing_entry)
    fallback.update(
        {
            "status": "application-cache-fallback",
            "is_cached": True,
            "is_stale": True,
            "warning": str(error),
        }
    )
    return fallback


def build_market_summary(
    index_key: str, daily: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    requirement_keys = MARKET_REQUIREMENTS[index_key]
    components = [daily[key] for key in requirement_keys]
    price = daily[f"{index_key}_price"]["data"]
    vix = daily["vix"]["data"]

    sources: list[dict[str, str]] = []
    seen_sources: set[tuple[str, str]] = set()
    for component in components:
        source = component["source"]
        identity = (str(source["name"]), str(source["url"]))
        if identity not in seen_sources:
            sources.append({"name": identity[0], "url": identity[1]})
            seen_sources.add(identity)

    is_stale = any(component["is_stale"] for component in components)
    all_cached = all(component["is_cached"] for component in components)
    any_cached = any(component["is_cached"] for component in components)
    if is_stale:
        status = "cache-fallback"
    elif all_cached:
        status = "cache-fresh"
    else:
        status = "source-refreshed"

    warnings = [component["warning"] for component in components if component["warning"]]
    component_fetched_at = {
        component["series"]: component["fetched_at"] for component in components
    }
    verification_dates = {
        china_calendar_date(parse_iso_datetime(component["fetched_at"]))
        for component in components
    }
    verified_for_date = (
        next(iter(verification_dates)).isoformat()
        if len(verification_dates) == 1 and not is_stale
        else None
    )
    summary: dict[str, Any] = {
        "data_date": price["data_date"],
        "current_price": price["current"],
        "ma200": price["ma200"],
        "ma200_sample_count": price["ma200_sample_count"],
        "vix": vix["current"],
        "vix_data_date": vix["data_date"],
        "sources": sources,
        "fetched_at": min(component["fetched_at"] for component in components),
        "component_fetched_at": component_fetched_at,
        "verified_for_date": verified_for_date,
        "status": status,
        "is_cached": all_cached,
        "has_cached_components": any_cached,
        "is_stale": is_stale,
        "warning": "; ".join(warnings) if warnings else None,
    }
    if index_key == "ndx":
        vxn = daily["vxn"]["data"]
        summary["vxn"] = vxn["current"]
        summary["vxn_data_date"] = vxn["data_date"]
    return summary


def stale_market_fallback(
    existing_market: dict[str, Any], errors: list[str]
) -> dict[str, Any]:
    fallback = dict(existing_market)
    fallback.update(
        {
            "status": "application-cache-fallback",
            "is_cached": True,
            "has_cached_components": True,
            "is_stale": True,
            "verified_for_date": None,
            "warning": "; ".join(errors),
        }
    )
    return fallback


def publish_market_data(payload: dict[str, Any]) -> None:
    json_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    script_text = "window.MARKET_DATA = " + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ) + ";\n"
    publications = (
        (MARKET_DATA_PATH, json_text),
        (MARKET_DATA_SCRIPT_PATH, script_text),
    )
    previous_contents = {
        path: path.read_text(encoding="utf-8") if path.exists() else None
        for path, _content in publications
    }
    try:
        for path, content in publications:
            atomic_write_text(path, content)
    except Exception:
        for path, _content in publications:
            previous = previous_contents[path]
            try:
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write_text(path, previous)
            except OSError:
                pass
        raise


def update_market_data(
    *,
    now: datetime,
    max_cache_age: timedelta,
    timeout: int,
    force: bool,
    force_daily: bool | None = None,
    fetcher: Callable[[str, int], str] = fetch_html,
) -> tuple[dict[str, Any], list[str]]:
    existing = load_existing_market_data()
    existing_indices = existing.get("indices", {})
    if not isinstance(existing_indices, dict):
        existing_indices = {}

    indices: dict[str, Any] = {}
    errors: list[str] = []
    missing: list[str] = []
    for config in INDEX_CONFIGS:
        try:
            indices[config.key] = refresh_index(
                config,
                now=now,
                max_cache_age=max_cache_age,
                timeout=timeout,
                force=force,
                fetcher=fetcher,
            )
        except MarketDataError as exc:
            errors.append(str(exc))
            previous = existing_indices.get(config.key)
            if isinstance(previous, dict):
                indices[config.key] = stale_application_fallback(previous, exc)
            else:
                missing.append(config.symbol)

    daily: dict[str, dict[str, Any]] = {}
    daily_errors: dict[str, str] = {}
    daily_force = force if force_daily is None else (force or force_daily)
    for config in DAILY_SERIES_CONFIGS:
        try:
            daily[config.key] = refresh_daily_series(
                config,
                now=now,
                max_cache_age=max_cache_age,
                timeout=timeout,
                force=daily_force,
                fetcher=fetcher,
            )
        except MarketDataError as exc:
            message = str(exc)
            errors.append(message)
            daily_errors[config.key] = message

    for config in INDEX_CONFIGS:
        if config.key not in indices:
            continue
        required = MARKET_REQUIREMENTS[config.key]
        missing_series = [key for key in required if key not in daily]
        if not missing_series:
            indices[config.key]["market"] = build_market_summary(config.key, daily)
            continue

        previous = existing_indices.get(config.key)
        previous_market = previous.get("market") if isinstance(previous, dict) else None
        if isinstance(previous_market, dict):
            relevant_errors = [
                daily_errors[key] for key in missing_series if key in daily_errors
            ]
            indices[config.key]["market"] = stale_market_fallback(
                previous_market, relevant_errors
            )
        else:
            missing.append(f"{config.symbol} market")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso_utc(now),
        "indices": indices,
        "errors": errors,
    }
    missing = list(dict.fromkeys(missing))
    if not missing:
        publish_market_data(payload)
    return payload, missing


def offline_fetcher(_url: str, _timeout: int) -> str:
    raise MarketDataError("offline mode enabled")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="ignore cache age and refresh sources"
    )
    parser.add_argument(
        "--force-daily",
        action="store_true",
        help="refresh daily price and volatility sources while allowing monthly PE cache",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="do not use the network; validate and publish cached data",
    )
    parser.add_argument(
        "--max-cache-age-hours",
        type=float,
        default=DEFAULT_CACHE_MAX_AGE_HOURS,
        help="reuse a valid raw cache newer than this many hours (default: 24)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="per-request timeout in seconds (default: 45)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.max_cache_age_hours < 0:
        print("--max-cache-age-hours must be non-negative", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("--timeout must be positive", file=sys.stderr)
        return 2

    fetcher = offline_fetcher if args.offline else fetch_html
    payload, missing = update_market_data(
        now=utc_now(),
        max_cache_age=timedelta(hours=args.max_cache_age_hours),
        timeout=args.timeout,
        force=args.force,
        force_daily=args.force_daily,
        fetcher=fetcher,
    )

    for key, entry in payload["indices"].items():
        pe = entry["pe"]
        market = entry.get("market")
        market_text = ""
        if isinstance(market, dict):
            market_text = (
                f", close {market['current_price']:.2f}, "
                f"MA200 {market['ma200']:.2f}, VIX {market['vix']:.2f}"
            )
            if "vxn" in market:
                market_text += f", VXN {market['vxn']:.2f}"
        print(
            f"{key.upper()}: PE {pe['current']:.4f}, "
            f"10Y percentile {pe['ten_year_percentile']:.1f}%, "
            f"status={entry['status']}{market_text}"
        )
    for error in payload["errors"]:
        print(f"warning: {error}", file=sys.stderr)
    if missing:
        print(
            "market data was not published; no valid data for " + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
