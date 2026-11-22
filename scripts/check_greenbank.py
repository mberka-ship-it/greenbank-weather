#!/usr/bin/env python3
"""Read-only health check of a published Greenbank rainfall dashboard snapshot.

This checks publication *content* and freshness. It cannot establish Ecowitt
hardware uptime, verify Weather Underground's upstream reports, or refresh a Site.
Only Python's standard library is used; no deployment or write APIs are called.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SITE_URL = "https://greenbank-rainfall-outlook.mberka.chatgpt.site/?view=1&tab=outlook"
STATION_ID = "IBRISB6274"
AEST = ZoneInfo("Australia/Brisbane")
EXPECTED_MODELS = {"ECMWF IFS", "ECMWF AIFS", "GFS seamless"}
MAX_BYTES = 8 * 1024 * 1024
SNAPSHOT_TAG = re.compile(
    r"<script\b(?=[^>]*\bid\s*=\s*['\"]data-app-reviewed-snapshot['\"])[^>]*>"
    r"(.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)


class Unverifiable(ValueError):
    """The target could not be checked; this is not proof of station failure."""


def parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise Unverifiable(f"{field}: missing timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Unverifiable(f"{field}: invalid timestamp") from exc
    if result.tzinfo is None:
        raise Unverifiable(f"{field}: timezone offset missing")
    return result.astimezone(AEST)


def parse_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise Unverifiable(f"{field}: missing date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise Unverifiable(f"{field}: invalid date") from exc


def rows(snapshot: dict, name: str) -> list[dict]:
    query = snapshot.get("queries", {}).get(name, {})
    result = query.get("rows") if isinstance(query, dict) else None
    if not isinstance(result, list) or any(not isinstance(r, dict) for r in result):
        raise Unverifiable(f"queries.{name}.rows: unavailable or malformed")
    return result


def extract_snapshot(html: str) -> dict:
    # An external-v1 snapshot may supersede the reviewed fallback in the browser.
    # Do not assert the *live* data's health by inspecting that fallback alone.
    if re.search(
        r"<meta\b(?=[^>]*\bname\s*=\s*['\"]data-app-snapshot-storage['\"])(?=[^>]*\bcontent\s*=\s*['\"]external-v1['\"])[^>]*>",
        html, re.IGNORECASE,
    ):
        raise Unverifiable("External snapshot storage detected; this checker only validates embedded snapshots")
    match = SNAPSHOT_TAG.search(html)
    if not match:
        raise Unverifiable(
            "No data-app-reviewed-snapshot found; hosted site may use a different "
            "publication format or require authentication"
        )
    try:
        snapshot = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise Unverifiable("Embedded snapshot is not valid JSON") from exc
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("queries"), dict):
        raise Unverifiable("Embedded snapshot does not have the expected query structure")
    return snapshot


def evaluate(html: str, now: datetime, expected_forecast_id: str | None = None) -> dict:
    """Evaluate one published snapshot without network or filesystem writes."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(AEST)
    snapshot = extract_snapshot(html)
    status_rows = rows(snapshot, "status")
    if len(status_rows) != 1:
        raise Unverifiable("Expected exactly one dashboard status row")
    status = status_rows[0]
    capture = parse_timestamp(status.get("captured_at"), "status.captured_at")
    horizon = parse_timestamp(status.get("end"), "status.end")
    observed_date = parse_date(status.get("last_observation"), "status.last_observation")
    forecast_id = status.get("latest_id")
    if not isinstance(forecast_id, str) or not forecast_id:
        raise Unverifiable("status.latest_id: missing forecast ID")

    observations = rows(snapshot, "observations")
    model_rows = rows(snapshot, "models")
    archive = rows(snapshot, "archive")
    if not observations or not model_rows or not archive:
        raise Unverifiable("Observation, model, or archive dataset is empty")

    dates: list[date] = []
    values_valid = True
    for record in observations:
        dates.append(parse_date(record.get("date"), "observations.date"))
        rain = record.get("rain_mm")
        if type(rain) not in (int, float) or not math.isfinite(rain) or rain < 0:
            values_valid = False

    model_names = {r.get("model") for r in model_rows if isinstance(r.get("model"), str)}
    model_members_valid = all(
        isinstance(r.get("members"), int) and r["members"] > 0
        for r in model_rows if r.get("model") in EXPECTED_MODELS
    )
    days_old = (now.date() - observed_date).days
    capture_age_hours = (now - capture).total_seconds() / 3600
    horizon_days = (horizon - now).total_seconds() / 86400
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    check("expected_station", status.get("station") == STATION_ID,
          f"station={status.get('station')!r}")
    check("capture_freshness", -0.25 <= capture_age_hours <= 36,
          f"capture_age_hours={capture_age_hours:.1f}; limit=36")
    check("forecast_horizon", horizon_days >= 13,
          f"days_ahead={horizon_days:.1f}; minimum=13")
    check("observation_freshness", 0 <= days_old <= 2,
          f"last_date={observed_date.isoformat()}; age_calendar_days={days_old}; limit=2")
    check("observation_values", values_valid,
          f"rows={len(observations)}; requires finite, nonnegative mm")
    check("observation_dates", len(dates) == len(set(dates)) and max(dates) == observed_date
          and all(d <= now.date() for d in dates),
          "dates must be unique, not in the future and agree with last_observation")
    check("model_coverage", EXPECTED_MODELS.issubset(model_names) and model_members_valid,
          "expected=ECMWF IFS, ECMWF AIFS, GFS seamless; positive member counts")
    check("forecast_archive", forecast_id in {r.get("forecast_id") for r in archive},
          "latest forecast ID must appear in archive")
    if expected_forecast_id is not None:
        check("expected_publication", forecast_id == expected_forecast_id,
              f"expected={expected_forecast_id}; found={forecast_id}")

    return {
        "result": "PASS" if all(c["ok"] for c in checks) else "FAIL",
        "checked_at_aest": now.isoformat(timespec="seconds"),
        "forecast_id": forecast_id,
        "forecast_captured_at_aest": capture.isoformat(timespec="seconds"),
        "last_observation": observed_date.isoformat(),
        "checks": checks,
        "limitations": "Snapshot content only; not direct sensor or upstream verification",
    }


def read_html(url: str, timeout: int) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise Unverifiable("Site URL must use HTTPS")
    request = Request(url, headers={
        "User-Agent": "Greenbank-Rainfall-ReadOnly-Monitor/0.1",
        "Accept": "text/html",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise Unverifiable(f"Unexpected HTTP status: {response.status}")
            final = urlparse(response.geturl())
            if final.scheme != "https" or final.hostname != parsed.hostname:
                raise Unverifiable("Site redirected to a different host or an insecure URL")
            body = response.read(MAX_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise Unverifiable(f"Site fetch unavailable: {type(exc).__name__}") from exc
    if len(body) > MAX_BYTES:
        raise Unverifiable("Published HTML exceeds the 8 MiB read limit")
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--url", default=SITE_URL, help="Published HTTPS dashboard")
    source.add_argument("--file", type=Path, help="Offline HTML fixture; no network access")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--expected-forecast-id", help="Optional post-publication verification")
    parser.add_argument("--now", help="ISO8601 timestamp for offline reproducible tests")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    if args.now:
        try:
            now = parse_timestamp(args.now, "--now")
        except Unverifiable as exc:
            parser.error(str(exc))

    try:
        raw = args.file.read_bytes() if args.file else read_html(args.url, args.timeout)
        if len(raw) > MAX_BYTES:
            raise Unverifiable("HTML exceeds the 8 MiB read limit")
        try:
            html = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise Unverifiable("HTML is not UTF-8") from exc
        report = evaluate(html, now, args.expected_forecast_id)
        report["published_html_sha256"] = hashlib.sha256(raw).hexdigest()
        code = 0 if report["result"] == "PASS" else 1
    except (Unverifiable, OSError) as exc:
        report = {"result": "UNVERIFIABLE", "checked_at_aest": now.astimezone(AEST).isoformat(timespec="seconds"),
                  "reason": str(exc), "limitations": "Not evidence of Ecowitt station failure"}
        code = 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
