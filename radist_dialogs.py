#!/usr/bin/env python3
"""CLI utility for downloading Radist chat dialogs via Messaging API."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://api.radist.online/v2"
DEFAULT_CHATS_ENDPOINT = "/companies/{company_id}/messaging/chats/with_contacts/"
DEFAULT_MESSAGES_ENDPOINT = "/companies/{company_id}/messaging/messages/"
DEFAULT_CONFIG_PATH = Path.home() / ".radist_dialogs.json"
DEFAULT_CHATS_ENDPOINT_CANDIDATES = (
    DEFAULT_CHATS_ENDPOINT,
    "/companies/{company_id}/messaging/chats/",
)
DEFAULT_MESSAGES_ENDPOINT_CANDIDATES = (
    DEFAULT_MESSAGES_ENDPOINT,
    "/companies/{company_id}/messaging/messages/",
)
AUTH_ERROR_CODES = {401, 403}


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
    company_id: Optional[int]
    mode: Optional[str]
    latest: Optional[int]
    last_days: Optional[int]
    from_index: Optional[int]
    to_index: Optional[int]
    date_from: Optional[str]
    date_to: Optional[str]
    base_url: str
    chats_endpoint: str
    messages_endpoint: str
    limit: int
    timeout: int
    output: Path
    output_format: str
    auth_header: str
    auth_prefix: str
    token_query_param: Optional[str]
    config_path: Path
    save_config: bool
    setup_only: bool
    retry_count: int
    retry_backoff: float


def get_config_path(argv: Optional[List[str]] = None) -> Path:
    args = list(argv) if argv is not None else sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--config" and i + 1 < len(args):
            return Path(args[i + 1]).expanduser()
        if arg.startswith("--config="):
            return Path(arg.split("=", 1)[1]).expanduser()
    return DEFAULT_CONFIG_PATH


def load_saved_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ApiError(f"Invalid config file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ApiError(f"Invalid config file {path}: expected a JSON object")
    return payload


def maybe_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(value)


def build_parser(defaults: Dict[str, Any], config_path: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download Radist dialogs from the Messaging API."
    )
    parser.add_argument("--config", default=str(config_path), help="Local config file path")
    parser.add_argument(
        "--save-config",
        action="store_true",
        help="Persist token/company/auth settings to the local config file",
    )
    parser.add_argument(
        "--token",
        default=defaults.get("token"),
        help="Radist API token. If omitted, uses the saved config value.",
    )
    parser.add_argument(
        "--company-id",
        type=int,
        default=maybe_int(defaults.get("company_id")),
        help="Radist company ID. If omitted, uses the saved config value.",
    )

    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--latest", type=int, help="Download latest N dialogs")
    group.add_argument(
        "--last-days",
        type=int,
        help="Download dialogs active in the last N UTC calendar days, including today",
    )
    group.add_argument(
        "--index-range",
        action="store_true",
        help="Download dialogs by 1-based index range using --from-index and --to-index",
    )
    group.add_argument(
        "--date-range",
        action="store_true",
        help="Download dialogs between --from-date and --to-date",
    )

    parser.add_argument("--from-index", type=int, help="1-based start index for index-range mode")
    parser.add_argument("--to-index", type=int, help="1-based end index for index-range mode")
    parser.add_argument("--from-date", dest="date_from", help="UTC start date: YYYY-MM-DD")
    parser.add_argument("--to-date", dest="date_to", help="UTC end date: YYYY-MM-DD")

    parser.add_argument("--base-url", default=defaults.get("base_url", DEFAULT_BASE_URL))
    parser.add_argument(
        "--chats-endpoint",
        default=defaults.get("chats_endpoint", DEFAULT_CHATS_ENDPOINT),
        help="Chats endpoint template",
    )
    parser.add_argument(
        "--messages-endpoint",
        default=defaults.get("messages_endpoint", DEFAULT_MESSAGES_ENDPOINT),
        help="Messages endpoint template",
    )
    parser.add_argument("--limit", type=int, default=int(defaults.get("limit", 100)))
    parser.add_argument("--timeout", type=int, default=int(defaults.get("timeout", 30)))
    parser.add_argument(
        "--retry-count",
        type=int,
        default=int(defaults.get("retry_count", 4)),
        help="How many times to retry requests on 429 responses",
    )
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=float(defaults.get("retry_backoff", 2.0)),
        help="Base wait time in seconds for 429 retries",
    )
    parser.add_argument("--output", default="dialogs.jsonl", help="Output file path")
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("jsonl", "json"),
        default="jsonl",
        help="Output format",
    )
    parser.add_argument(
        "--auth-header",
        default=defaults.get("auth_header", "X-Api-Key"),
        help="Header name for API token",
    )
    parser.add_argument(
        "--auth-prefix",
        default=defaults.get("auth_prefix", ""),
        help="Prefix before token in auth header. Empty means raw token.",
    )
    parser.add_argument(
        "--token-query-param",
        default=defaults.get("token_query_param"),
        help="Query parameter name for API token. If set, token is sent in the URL instead of a header.",
    )
    return parser


def parse_args(argv: Optional[List[str]] = None) -> CliConfig:
    config_path = get_config_path(argv)
    defaults = load_saved_config(config_path)
    parser = build_parser(defaults, config_path)
    ns = parser.parse_args(argv)

    if ns.latest is not None and ns.latest <= 0:
        parser.error("--latest must be > 0")
    if ns.last_days is not None and ns.last_days <= 0:
        parser.error("--last-days must be > 0")
    if ns.from_index is not None and ns.from_index <= 0:
        parser.error("--from-index must be > 0")
    if ns.to_index is not None and ns.to_index <= 0:
        parser.error("--to-index must be > 0")
    if ns.limit <= 0:
        parser.error("--limit must be > 0")
    if ns.retry_count < 0:
        parser.error("--retry-count must be >= 0")
    if ns.retry_backoff <= 0:
        parser.error("--retry-backoff must be > 0")

    if not ns.token:
        parser.error("--token is required unless saved in the config file")

    if ns.date_range:
        if not ns.date_from or not ns.date_to:
            parser.error("--date-range requires both --from-date and --to-date")
        validate_date(ns.date_from)
        validate_date(ns.date_to)
        if ns.date_from > ns.date_to:
            parser.error("--from-date must be <= --to-date")
        mode = "date_range"
    elif ns.last_days is not None:
        if ns.from_index is not None or ns.to_index is not None:
            parser.error("--from-index/--to-index are only valid with --index-range")
        if ns.date_from or ns.date_to:
            parser.error("--from-date/--to-date are only valid with --date-range")
        ns.date_from, ns.date_to = last_days_date_range(ns.last_days)
        mode = "date_range"
    elif ns.index_range:
        if ns.from_index is None or ns.to_index is None:
            parser.error("--index-range requires both --from-index and --to-index")
        if ns.from_index > ns.to_index:
            parser.error("--from-index must be <= --to-index")
        if ns.date_from or ns.date_to:
            parser.error("--from-date/--to-date are only valid with --date-range")
        mode = "index_range"
    elif ns.latest is not None:
        if ns.from_index is not None or ns.to_index is not None:
            parser.error("--from-index/--to-index are only valid with --index-range")
        if ns.date_from or ns.date_to:
            parser.error("--from-date/--to-date are only valid with --date-range")
        mode = "latest"
    else:
        if ns.from_index is not None or ns.to_index is not None:
            parser.error("--from-index/--to-index require --index-range")
        if ns.date_from or ns.date_to:
            parser.error("--from-date/--to-date are only valid with --date-range")
        mode = None

    setup_only = bool(ns.save_config and mode is None)
    if mode is None and not setup_only:
        parser.error(
            "Specify either --latest, --last-days, or --date-range, "
            "or use --save-config to store defaults"
        )

    return CliConfig(
        token=ns.token,
        company_id=ns.company_id,
        mode=mode,
        latest=ns.latest,
        last_days=ns.last_days,
        from_index=ns.from_index,
        to_index=ns.to_index,
        date_from=ns.date_from,
        date_to=ns.date_to,
        base_url=ns.base_url.rstrip("/"),
        chats_endpoint=normalize_endpoint(ns.chats_endpoint),
        messages_endpoint=normalize_endpoint(ns.messages_endpoint),
        limit=ns.limit,
        timeout=ns.timeout,
        output=Path(ns.output),
        output_format=ns.output_format,
        auth_header=ns.auth_header,
        auth_prefix=ns.auth_prefix,
        token_query_param=ns.token_query_param,
        config_path=Path(ns.config).expanduser(),
        save_config=ns.save_config,
        setup_only=setup_only,
        retry_count=ns.retry_count,
        retry_backoff=ns.retry_backoff,
    )


def normalize_endpoint(endpoint: str) -> str:
    normalized = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    if "{company_id}" not in normalized:
        raise ValueError("Endpoint template must contain {company_id}")
    return normalized


def validate_date(value: str) -> None:
    datetime.strptime(value, "%Y-%m-%d")


def last_days_date_range(days: int, today: Optional[date] = None) -> Tuple[str, str]:
    end = today or datetime.now(timezone.utc).date()
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def utc_range_inclusive(start: str, end: str) -> Tuple[str, str]:
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc
    )
    return start_dt.isoformat().replace("+00:00", "Z"), end_dt.isoformat().replace("+00:00", "Z")


def parse_iso8601(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def shift_timestamp_back(value: str) -> str:
    shifted = parse_iso8601(value) - timedelta(microseconds=1)
    return shifted.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_url(base_url: str, endpoint: str, params: Dict[str, Any]) -> str:
    query = urlencode({k: v for k, v in params.items() if v is not None and v != ""})
    return f"{base_url}{endpoint}?{query}" if query else f"{base_url}{endpoint}"


def add_query_params(url: str, params: Dict[str, Any]) -> str:
    filtered = {k: v for k, v in params.items() if v is not None and v != ""}
    if not filtered:
        return url

    parts = urlsplit(url)
    query_items = parse_qsl(parts.query, keep_blank_values=True)
    query_items.extend((key, str(value)) for key, value in filtered.items())
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query_items), parts.fragment)
    )


def build_auth_value(prefix: str, token: str) -> str:
    normalized = prefix.strip()
    if not normalized:
        return token
    return f"{normalized} {token}"


def build_headers(config: CliConfig) -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    if not config.token_query_param and config.auth_header:
        headers[config.auth_header] = build_auth_value(config.auth_prefix, config.token)
    return headers


def add_token_query_param(url: str, config: CliConfig) -> str:
    if not config.token_query_param:
        return url
    return add_query_params(url, {config.token_query_param: config.token})


def redact_url(url: str, config: CliConfig) -> str:
    if not config.token_query_param:
        return url

    parts = urlsplit(url)
    redacted = [
        (key, "<redacted>" if key == config.token_query_param else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(redacted), parts.fragment))


def redact_error_body(body: str, config: CliConfig) -> str:
    if not body or not config.token_query_param:
        return body

    redacted = body.replace(config.token, "<redacted>")
    pattern = re.compile(rf"({re.escape(config.token_query_param)}=)[^&\s'\"<>]+")
    return pattern.sub(r"\1<redacted>", redacted)


def fetch_json(url: str, config: CliConfig) -> Any:
    request_url = add_token_query_param(url, config)
    req = Request(request_url, headers=build_headers(config))
    for attempt in range(config.retry_count + 1):
        try:
            with urlopen(req, timeout=config.timeout) as resp:
                payload = resp.read().decode("utf-8")
                return json.loads(payload)
        except HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            if exc.code == 429 and attempt < config.retry_count:
                time.sleep(config.retry_backoff * (2**attempt))
                continue
            raise HttpStatusError(
                status_code=exc.code,
                url=redact_url(request_url, config),
                body=redact_error_body(body, config),
            ) from exc
        except Exception as exc:
            raise ApiError(f"Request failed for {redact_url(request_url, config)}: {exc}") from exc
    raise ApiError(f"Request failed for {redact_url(request_url, config)}: exhausted retries")


def render_endpoint(template: str, company_id: int) -> str:
    return template.format(company_id=company_id)


def save_local_config(config: CliConfig) -> None:
    payload = {
        "token": config.token,
        "company_id": config.company_id,
        "base_url": config.base_url,
        "chats_endpoint": config.chats_endpoint,
        "messages_endpoint": config.messages_endpoint,
        "auth_header": config.auth_header,
        "auth_prefix": config.auth_prefix,
        "token_query_param": config.token_query_param,
        "limit": config.limit,
        "timeout": config.timeout,
        "retry_count": config.retry_count,
        "retry_backoff": config.retry_backoff,
    }
    config.config_path.parent.mkdir(parents=True, exist_ok=True)
    config.config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def endpoint_candidates(primary: str, fallbacks: Tuple[str, ...]) -> List[str]:
    candidates: List[str] = []
    for endpoint in (primary, *fallbacks):
        normalized = normalize_endpoint(endpoint)
        if normalized not in candidates:
            candidates.append(normalized)
    return candidates


def summarize_endpoint_errors(errors: List[str]) -> str:
    if not errors:
        return "no endpoint candidates were checked"
    return "; ".join(errors[:5])


def find_first_chat_id(payload: Dict[str, Any]) -> Optional[int]:
    for dialog in flatten_chats(payload):
        chat = dialog.get("chat", {})
        if isinstance(chat, dict) and chat.get("chat_id") is not None:
            return int(chat["chat_id"])
    return None


def detect_chats_endpoint(config: CliConfig, company_id: int) -> Tuple[str, Dict[str, Any]]:
    errors: List[str] = []
    for endpoint in endpoint_candidates(config.chats_endpoint, DEFAULT_CHATS_ENDPOINT_CANDIDATES):
        url = build_url(config.base_url, render_endpoint(endpoint, company_id), {"limit": 1})
        try:
            payload = fetch_json(url, config)
        except HttpStatusError as exc:
            if exc.status_code in AUTH_ERROR_CODES:
                raise
            errors.append(f"{endpoint} -> HTTP {exc.status_code}")
            continue
        except ApiError as exc:
            errors.append(f"{endpoint} -> {exc}")
            continue

        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            return endpoint, payload
        errors.append(f"{endpoint} -> unexpected response shape")

    raise ApiError(f"Could not auto-detect chats endpoint: {summarize_endpoint_errors(errors)}")


def detect_messages_endpoint(config: CliConfig, company_id: int, chat_id: int) -> str:
    errors: List[str] = []
    for endpoint in endpoint_candidates(
        config.messages_endpoint, DEFAULT_MESSAGES_ENDPOINT_CANDIDATES
    ):
        url = build_url(
            config.base_url,
            render_endpoint(endpoint, company_id),
            {"chat_id": chat_id, "limit": 1},
        )
        try:
            payload = fetch_json(url, config)
        except HttpStatusError as exc:
            if exc.status_code in AUTH_ERROR_CODES:
                raise
            errors.append(f"{endpoint} -> HTTP {exc.status_code}")
            continue
        except ApiError as exc:
            errors.append(f"{endpoint} -> {exc}")
            continue

        if isinstance(payload, list):
            return endpoint
        errors.append(f"{endpoint} -> unexpected response shape")

    raise ApiError(f"Could not auto-detect messages endpoint: {summarize_endpoint_errors(errors)}")


def auto_detect_endpoints(config: CliConfig) -> None:
    if config.company_id is None:
        raise ApiError("Could not auto-detect endpoints without company_id")

    chats_endpoint, chats_payload = detect_chats_endpoint(config, config.company_id)
    config.chats_endpoint = chats_endpoint

    first_chat_id = find_first_chat_id(chats_payload)
    if first_chat_id is not None:
        config.messages_endpoint = detect_messages_endpoint(
            config, config.company_id, first_chat_id
        )


def resolve_company_id(config: CliConfig) -> int:
    if config.company_id is not None:
        return config.company_id

    url = build_url(f"{config.base_url}", "/companies/", {"limit": 100, "offset": 0})
    payload = fetch_json(url, config)
    if not isinstance(payload, dict):
        raise ApiError("Could not auto-detect company_id: unexpected /companies/ response")

    companies = payload.get("companies")
    if not isinstance(companies, list):
        raise ApiError("Could not auto-detect company_id: /companies/ response has no companies[]")

    valid_companies = [item for item in companies if isinstance(item, dict) and "id" in item]
    if len(valid_companies) == 1:
        return int(valid_companies[0]["id"])

    if not valid_companies:
        raise ApiError("Could not auto-detect company_id: no accessible companies found")

    available = ", ".join(
        f"{item.get('id')}:{item.get('name', 'unknown')}" for item in valid_companies[:10]
    )
    raise ApiError(
        "Multiple companies available. Pass --company-id explicitly or save it to config. "
        f"Examples: {available}"
    )


def fetch_chats_page(config: CliConfig, company_id: int, cursor: Optional[str]) -> Dict[str, Any]:
    endpoint = render_endpoint(config.chats_endpoint, company_id)
    params: Dict[str, Any] = {"limit": config.limit}
    if cursor:
        params["cursor"] = cursor
    payload = fetch_json(build_url(config.base_url, endpoint, params), config)
    if not isinstance(payload, dict):
        raise ApiError("Unexpected chats response: expected an object")
    return payload


def flatten_chats(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []

    dialogs: List[Dict[str, Any]] = []
    for contact in data:
        if not isinstance(contact, dict):
            continue
        chats = contact.get("chats")
        if not isinstance(chats, list):
            continue
        contact_info = {
            "contact_id": contact.get("contact_id"),
            "contact_name": contact.get("contact_name"),
            "avatar_url": contact.get("avatar_url"),
            "is_group": contact.get("is_group"),
            "unanswered_count": contact.get("unanswered_count"),
            "last_chat_updated_at": contact.get("last_chat_updated_at"),
        }
        for chat in chats:
            if not isinstance(chat, dict):
                continue
            dialogs.append({"contact": contact_info, "chat": chat})
    return dialogs


def dialog_sort_key(dialog: Dict[str, Any]) -> str:
    chat = dialog.get("chat", {})
    if isinstance(chat, dict):
        last_message = chat.get("last_message")
        if isinstance(last_message, dict) and isinstance(last_message.get("created_at"), str):
            return last_message["created_at"]
    contact = dialog.get("contact", {})
    if isinstance(contact, dict) and isinstance(contact.get("last_chat_updated_at"), str):
        return contact["last_chat_updated_at"]
    return ""


def select_dialog_slice(config: CliConfig, dialogs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if config.mode == "latest":
        return dialogs[: config.latest]
    if config.mode == "index_range":
        start = (config.from_index or 1) - 1
        end = config.to_index or len(dialogs)
        return dialogs[start:end]
    return dialogs


def target_dialog_count(config: CliConfig) -> Optional[int]:
    if config.mode == "latest":
        return config.latest
    if config.mode == "index_range":
        return config.to_index
    return None


def list_dialogs(config: CliConfig, company_id: int) -> List[Dict[str, Any]]:
    dialogs: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    range_start = None
    target_count = target_dialog_count(config)
    if config.mode == "date_range" and config.date_from and config.date_to:
        range_start, _ = utc_range_inclusive(config.date_from, config.date_to)

    while True:
        payload = fetch_chats_page(config, company_id, cursor)
        page_dialogs = flatten_chats(payload)
        if not page_dialogs:
            break

        dialogs.extend(page_dialogs)

        if target_count is not None and len(dialogs) >= target_count:
            break

        metadata = payload.get("response_metadata")
        next_cursor = metadata.get("next_cursor") if isinstance(metadata, dict) else None
        if not next_cursor:
            break

        if range_start:
            newest = max((dialog_sort_key(item) for item in page_dialogs), default="")
            oldest = min((dialog_sort_key(item) for item in page_dialogs), default="")
            if newest and oldest and oldest < range_start:
                break

        cursor = str(next_cursor)

    dialogs.sort(key=dialog_sort_key, reverse=True)
    return select_dialog_slice(config, dialogs)


def fetch_chat_messages(
    config: CliConfig,
    company_id: int,
    chat_id: int,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    endpoint = render_endpoint(config.messages_endpoint, company_id)
    messages: List[Dict[str, Any]] = []
    seen_ids: Set[Any] = set()
    until = date_to

    while True:
        params: Dict[str, Any] = {"chat_id": chat_id, "limit": config.limit}
        if until:
            params["until"] = until

        payload = fetch_json(build_url(config.base_url, endpoint, params), config)
        if not isinstance(payload, list):
            raise ApiError(f"Unexpected messages response for chat {chat_id}: expected a list")
        page = [item for item in payload if isinstance(item, dict)]
        if not page:
            break

        oldest_created_at = None
        reached_lower_bound = False
        added_from_page = 0

        for message in page:
            message_id = message.get("message_id")
            if message_id in seen_ids:
                continue
            seen_ids.add(message_id)

            created_at = message.get("created_at")
            if isinstance(created_at, str):
                if oldest_created_at is None or created_at < oldest_created_at:
                    oldest_created_at = created_at
                if date_from and created_at < date_from:
                    reached_lower_bound = True
                    continue
                if date_to and created_at > date_to:
                    continue

            messages.append(message)
            added_from_page += 1

        if len(page) < config.limit:
            break
        if reached_lower_bound:
            break
        if not oldest_created_at:
            break

        next_until = shift_timestamp_back(oldest_created_at)
        if next_until == until:
            break
        until = next_until

        if added_from_page == 0 and date_from is None and date_to is None:
            break

    messages.sort(key=lambda item: str(item.get("created_at", "")))
    return messages


def dialog_in_range(dialog: Dict[str, Any], date_from: str, date_to: str) -> bool:
    timestamp = dialog_sort_key(dialog)
    return bool(timestamp) and date_from <= timestamp <= date_to


def download_dialogs(config: CliConfig) -> List[Dict[str, Any]]:
    company_id = config.company_id if config.company_id is not None else resolve_company_id(config)
    dialogs = list_dialogs(config, company_id)

    range_start = range_end = None
    if config.mode == "date_range" and config.date_from and config.date_to:
        range_start, range_end = utc_range_inclusive(config.date_from, config.date_to)
        dialogs = [item for item in dialogs if dialog_in_range(item, range_start, range_end)]

    result: List[Dict[str, Any]] = []
    for dialog in dialogs:
        chat = dialog.get("chat", {})
        chat_id = chat.get("chat_id") if isinstance(chat, dict) else None
        if chat_id is None:
            continue
        messages = fetch_chat_messages(
            config,
            company_id,
            int(chat_id),
            date_from=range_start,
            date_to=range_end,
        )
        if config.mode == "date_range" and not messages:
            continue
        result.append(
            {
                "company_id": company_id,
                "contact": dialog.get("contact"),
                "chat": chat,
                "messages": messages,
            }
        )
    return result


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
        config.company_id = resolve_company_id(config)

        if config.save_config and config.setup_only:
            save_local_config(config)
            print(f"Saved config -> {config.config_path}")
            return 0

        auto_detect_endpoints(config)

        if config.save_config:
            save_local_config(config)

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
