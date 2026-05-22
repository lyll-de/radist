# Radist dialogs downloader

Python CLI for exporting dialogs from the Radist Messaging API.

The script now uses the documented v2 endpoints:

- `GET /companies/{company_id}/messaging/chats/with_contacts/`
- `GET /companies/{company_id}/messaging/messages/`

Auth defaults match the OpenAPI schema:

- header: `X-Api-Key`
- token prefix: empty

If your API gateway expects another auth shape, the CLI can also send the token
with a different header, a `Bearer`-style prefix, or a query parameter.

## One-time setup

Save your token and `company_id` locally:

```bash
python radist_dialogs.py --token "$RADIST_TOKEN" --company-id 163146 --save-config
```

By default the config is stored in:

```text
~/.radist_dialogs.json
```

After that you can run the downloader without repeating auth args.

## Examples

Latest dialogs:

```bash
python radist_dialogs.py --latest 10 --output dialogs.json
```

Dialogs active in the last 10 UTC calendar days, including today:

```bash
python radist_dialogs.py --last-days 10 --format json --output dialogs_last_10_days.json
```

Dialogs by list position:

```bash
python radist_dialogs.py --index-range --from-index 500 --to-index 1000 --output dialogs_500_1000.json
```

Dialogs active in a UTC date range:

```bash
python radist_dialogs.py \
  --date-range --from-date 2026-03-01 --to-date 2026-03-10 \
  --output dialogs.json
```

Save config and download in one command:

```bash
python radist_dialogs.py \
  --token "$RADIST_TOKEN" \
  --company-id 163146 \
  --save-config \
  --latest 10
```

Alternative auth header:

```bash
python radist_dialogs.py \
  --latest 10 \
  --token "$RADIST_TOKEN" \
  --auth-header X-API-Key \
  --auth-prefix ""
```

Token in a query parameter:

```bash
python radist_dialogs.py \
  --latest 10 \
  --token "$RADIST_TOKEN" \
  --token-query-param api_key
```

## Output shape

Each exported item contains:

- `company_id`
- `contact`
- `chat`
- `messages`

## Useful flags

- `--config PATH` to use a custom local config file
- `--format jsonl|json`
- `--limit` to control page size for chats and messages
- `--timeout` to change HTTP timeout
- `--retry-count` and `--retry-backoff` to be more resilient to `429 Too Many Requests`
- `--last-days N` to export dialogs active in the last N UTC calendar days
- `--index-range --from-index N --to-index M` to export a slice of the dialogs list
- `--auth-header`, `--auth-prefix`, and `--token-query-param` to adapt auth
- `--chats-endpoint` and `--messages-endpoint` if Radist changes path templates later

The CLI probes the configured chat and message endpoints before downloading. If
the configured path is not accepted, it tries the built-in Radist endpoint
candidates and uses the first response with the expected shape.
