#!/usr/bin/env python3
"""CLI utility for downloading Radist chat dialogs via Messaging API."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://api.radist.online/v2"
DEFAULT_CHATS_ENDPOINT = "/companies/{company_id}/messaging/chats/with_contacts/"
DEFAULT_MESSAGES_ENDPOINT = "/companies/{company_id}/messaging/messages/"
DEFAULT_CONFIG_PATH = Path.home() / ".radist_dialogs.json"


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
    config_path: Path
    save_config: bool
    setup_only: bool


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
        "--date-range",
        action="store_true",
        help="Download dialogs between --from-date and --to-date",
    )

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
    return parser


def parse_args(argv: Optional[List[str]] = None) -> CliConfig:
    config_path = get_config_path(argv)
    defaults = load_saved_config(config_path)
    parser = build_parser(defaults, config_path)
    ns = parser.parse_args(argv)

    if ns.latest is not None and ns.latest <= 0:
        parser.error("--latest must be > 0")
    if ns.limit <= 0:
        parser.error("--limit must be > 0")

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
    elif ns.latest is not None:
        mode = "latest"
    else:
        mode = None

    setup_only = bool(ns.save_config and mode is None)
    if mode is None and not setup_only:
        parser.error("Specify either --latest or --date-range, or use --save-config to store defaults")

    return CliConfig(
        token=ns.token,
        company_id=ns.company_id,
        mode=mode,
        latest=ns.latest,
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
        config_path=Path(ns.config).expanduser(),
        save_config=ns.save_config,
        setup_only=setup_only,
    )


def normalize_endpoint(endpoint: str) -> str:
    normalized = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    if "{company_id}" not in normalized:
        raise ValueError("Endpoint template must contain {company_id}")
    return normalized


def validate_date(value: str) -> None:
    datetime.strptime(value, "%Y-%m-%d")


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


def build_auth_value(prefix: str, token: str) -> str:
    normalized = prefix.strip()
    if not normalized:
        return token
    return f"{normalized} {token}"


def fetch_json(url: str, config: CliConfig) -> Any:
    headers = {
        config.auth_header: build_auth_value(config.auth_prefix, config.token),
        "Accept": "application/json",
    }
    req = Request(url, headers=headers)
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
        raise HttpStatusError(status_code=exc.code, url=url, body=body) from exc
    except Exception as exc:
        raise ApiError(f"Request failed for {url}: {exc}") from exc


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
        "limit": config.limit,
        "timeout": config.timeout,
    }
    config.config_path.parent.mkdir(parents=True, exist_ok=True)
    config.config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
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


def list_dialogs(config: CliConfig, company_id: int) -> List[Dict[str, Any]]:
    dialogs: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    range_start = None
    if config.mode == "date_range" and config.date_from and config.date_to:
        range_start, _ = utc_range_inclusive(config.date_from, config.date_to)

    while True:
        payload = fetch_chats_page(config, company_id, cursor)
        page_dialogs = flatten_chats(payload)
        if not page_dialogs:
            break

        dialogs.extend(page_dialogs)

        if config.mode == "latest" and len(dialogs) >= (config.latest or 0):
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
    if config.mode == "latest":
        return dialogs[: config.latest]
    return dialogs


def fetch_chat_messages(
    config: CliConfig,
    company_id: int,
    chat_id: int,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    endpoint = render_endpoint(config.messages_endpoint, company_id)
    messages: List[Dict[str, Any]] = []
    seen_ids = set()
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

        if config.save_config:
            save_local_config(config)
            if config.setup_only:
                print(f"Saved config -> {config.config_path}")
                return 0

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
