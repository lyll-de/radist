# Radist dialogs downloader (CLI)

Простой Python-скрипт для скачивания диалогов из Radist API в двух режимах:

- последние `N` диалогов;
- диалоги за диапазон дат (`UTC`, включительно).

## Быстрый старт

```bash
python radist_dialogs.py --token "$RADIST_TOKEN" --latest 50 --output dialogs.jsonl
```

```bash
python radist_dialogs.py \
  --token "$RADIST_TOKEN" \
  --date-range --from-date 2026-01-01 --to-date 2026-01-31 \
  --output january_dialogs.jsonl
```

## Форматы вывода

- `jsonl` (по умолчанию): по одному диалогу на строку — удобно читать и обрабатывать частями.
- `json`: один массив JSON — удобно открыть целиком в редакторе.

Выбор через `--format jsonl|json`.

## Параметры

- `--token` (обязательный): API токен.
- Режимы (взаимоисключающие):
  - `--latest N`
  - `--date-range --from-date YYYY-MM-DD --to-date YYYY-MM-DD`
- `--output` путь к файлу (по умолчанию `dialogs.jsonl`)
- `--endpoint` путь endpoint (по умолчанию `/chats`)
- `--base-url` базовый URL API (по умолчанию `https://api.radist.online/v2`)
- `--limit` размер страницы (по умолчанию `100`)
- `--timeout` таймаут HTTP (по умолчанию `30`)

### Кастомизация query-параметров

Если в API отличаются имена параметров пагинации/дат, можно задать:

- `--page-param` (по умолчанию `page`)
- `--limit-param` (по умолчанию `limit`)
- `--from-param` (по умолчанию `date_from`)
- `--to-param` (по умолчанию `date_to`)

## Допущения

Так как API может возвращать данные в разных обертках, скрипт пытается извлекать диалоги из ключей:
`data`, `items`, `results`, `dialogs`, `chats`.

