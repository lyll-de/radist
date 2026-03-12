#!/usr/bin/env python3
"""CLI utility for downloading Radist chat dialogs."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://api.radist.online/v2"
DEFAULT_ENDPOINT = "/chats"
ENDPOINT_CANDIDATES = (
    "/chats",
    "/chat",
    "/dialogs",
    "/chats/dialogs",
    "/chat/dialogs",
)


class ApiError(RuntimeError):
    """Raised when API request fails."""


@dataclass
class HttpStatusError(ApiError):
    status_code: int
    url: str
    body: str

    def __str__(self) -> str:
        details = f"HTTP {self.status_code} for {self.url}"
        if self.body:
            return f"{details}: {self.body[:200]}"
        return details


@dataclass
class CliConfig:
    token: str
    mode: str
    latest: Optional[int]
    date_from: Optional[str]
    date_to: Optional[str]
    base_url: str
    endpoint: str
    limit: int
    timeout: int
    output: Path
    output_format: str
    page_param: str
    limit_param: str
    from_param: str
    to_param: str


def parse_args(argv: Optional[List[str]] = None) -> CliConfig:
    parser = argparse.ArgumentParser(
        description="Download Radist dialogs by latest N or date range."
    )
    parser.add_argument("--token", required=True, help="Radist API token")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--latest", type=int, help="Download latest N dialogs")
    group.add_argument(
        "--date-range",
        action="store_true",
        help="Download dialogs between --from-date and --to-date",
    )

    parser.add_argument("--from-date", dest="date_from", help="UTC start date: YYYY-MM-DD")
    parser.add_argument("--to-date", dest="date_to", help="UTC end date: YYYY-MM-DD")

    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help="API endpoint path for chats (default: /chats)",
    )
    parser.add_argument("--limit", type=int, default=100, help="Page size")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds")
    parser.add_argument("--output", default="dialogs.jsonl", help="Output file path")
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("jsonl", "json"),
        default="jsonl",
        help="Output format",
    )

    parser.add_argument("--page-param", default="page", help="Page query parameter name")
    parser.add_argument("--limit-param", default="limit", help="Limit query parameter name")
    parser.add_argument("--from-param", default="date_from", help="From date parameter name")
    parser.add_argument("--to-param", default="date_to", help="To date parameter name")

    ns = parser.parse_args(argv)

    if ns.latest is not None and ns.latest <= 0:
        parser.error("--latest must be > 0")
    if ns.limit <= 0:
        parser.error("--limit must be > 0")

    if ns.date_range:
        if not ns.date_from or not ns.date_to:
            parser.error("--date-range requires both --from-date and --to-date")
        validate_date(ns.date_from)
        validate_date(ns.date_to)
        if ns.date_from > ns.date_to:
            parser.error("--from-date must be <= --to-date")
    else:
        if ns.date_from or ns.date_to:
            parser.error("--from-date/--to-date are only valid with --date-range")

    mode = "latest" if ns.latest is not None else "date_range"

    return CliConfig(
        token=ns.token,
        mode=mode,
        latest=ns.latest,
        date_from=ns.date_from,
        date_to=ns.date_to,
        base_url=ns.base_url.rstrip("/"),
        endpoint=normalize_endpoint(ns.endpoint),
        limit=ns.limit,
        timeout=ns.timeout,
        output=Path(ns.output),
        output_format=ns.output_format,
        page_param=ns.page_param,
        limit_param=ns.limit_param,
        from_param=ns.from_param,
        to_param=ns.to_param,
    )


def normalize_endpoint(endpoint: str) -> str:
    return endpoint if endpoint.startswith("/") else f"/{endpoint}"


def validate_date(value: str) -> None:
    datetime.strptime(value, "%Y-%m-%d")


def utc_range_inclusive(start: str, end: str) -> Tuple[str, str]:
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc
    )
    return start_dt.isoformat().replace("+00:00", "Z"), end_dt.isoformat().replace("+00:00", "Z")


def build_url(base_url: str, endpoint: str, params: Dict[str, Any]) -> str:
    query = urlencode({k: v for k, v in params.items() if v is not None})
    return f"{base_url}{endpoint}?{query}" if query else f"{base_url}{endpoint}"


def fetch_page(url: str, token: str, timeout: int) -> Any:
    req = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload)
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body = ""
        raise HttpStatusError(status_code=exc.code, url=url, body=body) from exc
    except Exception as exc:  # noqa: BLE001
        raise ApiError(f"Request failed for {url}: {exc}") from exc


def resolve_endpoint(config: CliConfig) -> str:
    if config.endpoint != DEFAULT_ENDPOINT:
        return config.endpoint

    params = {config.page_param: 1, config.limit_param: 1}
    if config.mode == "date_range":
        range_start, range_end = utc_range_inclusive(config.date_from or "", config.date_to or "")
        params[config.from_param] = range_start
        params[config.to_param] = range_end

    for endpoint in ENDPOINT_CANDIDATES:
        url = build_url(config.base_url, endpoint, params)
        try:
            fetch_page(url, config.token, config.timeout)
            return endpoint
        except HttpStatusError as exc:
            if exc.status_code == 404:
                continue
            raise

    candidates = ", ".join(ENDPOINT_CANDIDATES)
    raise ApiError(
        "Could not auto-detect chats endpoint. "
        f"Tried: {candidates}. Pass explicit --endpoint <path>."
    )


def extract_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "dialogs", "chats"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def has_next_page(payload: Any) -> Optional[bool]:
    if not isinstance(payload, dict):
        return None

    for key in ("has_next", "hasNext", "next"):
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return bool(value)

    for container_key in ("meta", "pagination"):
        container = payload.get(container_key)
        if isinstance(container, dict):
            for key in ("has_next", "hasNext"):
                value = container.get(key)
                if isinstance(value, bool):
                    return value
    return None


def download_dialogs(config: CliConfig) -> List[Dict[str, Any]]:
    dialogs: List[Dict[str, Any]] = []
    endpoint = resolve_endpoint(config)
    page = 1
    range_start = range_end = None
    if config.mode == "date_range":
        range_start, range_end = utc_range_inclusive(config.date_from or "", config.date_to or "")

    while True:
        remaining = None
        if config.mode == "latest":
            remaining = (config.latest or 0) - len(dialogs)
            if remaining <= 0:
                break

        page_limit = min(config.limit, remaining) if remaining is not None else config.limit
        params: Dict[str, Any] = {
            config.page_param: page,
            config.limit_param: page_limit,
        }
        if config.mode == "date_range":
            params[config.from_param] = range_start
            params[config.to_param] = range_end

        url = build_url(config.base_url, endpoint, params)
        payload = fetch_page(url, config.token, config.timeout)
        items = extract_items(payload)
        if not items:
            break

        dialogs.extend(items)

        known_has_next = has_next_page(payload)
        if known_has_next is False:
            break
        if len(items) < page_limit:
            break

        page += 1

    if config.mode == "latest":
        return dialogs[: config.latest]
    return dialogs


def save_dialogs(items: Iterable[Dict[str, Any]], destination: Path, output_format: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    items_list = list(items)
    with destination.open("w", encoding="utf-8") as f:
        if output_format == "json":
            json.dump(items_list, f, ensure_ascii=False, indent=2)
            f.write("\n")
            return

        for item in items_list:
            f.write(json.dumps(item, ensure_ascii=False))
            f.write("\n")


def main(argv: Optional[List[str]] = None) -> int:
    try:
        config = parse_args(argv)
        dialogs = download_dialogs(config)
        save_dialogs(dialogs, config.output, config.output_format)
        print(f"Downloaded {len(dialogs)} dialogs -> {config.output}")
        return 0
    except ApiError as exc:
        print(f"API error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
