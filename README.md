# Radist dialogs downloader

Python CLI for exporting dialogs from the Radist Messaging API.

The script now uses the documented v2 endpoints:

- `GET /companies/{company_id}/messaging/chats/with_contacts/`
- `GET /companies/{company_id}/messaging/messages/`

Auth defaults match the OpenAPI schema:

- header: `X-Api-Key`
- token prefix: empty

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
- `--chats-endpoint` and `--messages-endpoint` if Radist changes path templates later
