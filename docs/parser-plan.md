# План парсера Госуслуг

## Задача

Подготовить парсер, который сможет наполнять локальную SQLite теми же сущностями, что уже используются для архива 2025:

- `competitions`;
- `entries`;
- `parser_runs`;
- `parser_queue`;
- `parser_fetch_log`;
- `parser_raw_responses`.

До появления данных 2026 парсер должен проверяться на fixture-режиме из архива 2025 и на одиночных безопасных HTTP probe-запросах.

Production-правило: парсер не создает промежуточные `.xlsx`. Live-данные пишутся сразу в SQLite. Экспорт в `.xlsx` возможен только отдельной будущей командой из SQLite, если он понадобится.

Live-парсер обязан поддерживать три режима:

- `raw-only` - только локальный raw-архив на ПК;
- `norm-only` - только нормализованные цели: локальная SQLite и/или VDS ingest API;
- `both` - raw локально плюс нормализованные цели.

Raw на VDS не пишется. Удаленная SQLite на VDS пополняется через `POST /api/ingest/batch`, а не прямой записью в sqlite-файл по сети.

## Ограничения из переписки

- Исторически было около `143000` запросов.
- При `>60` запросов в минуту на IP возникал бан примерно на 3 часа.
- Поэтому дефолтный лимит парсера: `55` запросов в минуту.
- Массовый live-обход до появления данных не запускать.
- Получение данных не обещать как real-time: для продукта показывать время последнего обновления.

## Календарь

По публичным сведениям 2026:

- 20 июня начинается прием документов;
- с 20 июня появляются/обновляются списки подавших документы;
- 27 июля публикуются конкурсные списки.

Практический вывод: до 20 июня live-парсер можно проверять только как discovery/probe, а полноценную загрузку списков нужно тестировать после появления реальных списков.

## Архитектура

1. `parser_queue`
   - очередь источников;
   - сейчас: `fixture_xlsx` из zip;
   - позже: реальные URL/API endpoints Госуслуг.

2. `parser_runs`
   - история запусков;
   - режимы: `fixture-run`, `http-probe`, позже `live-crawl`.

3. `parser_fetch_log`
   - результат каждого fetch/import;
   - статус, bytes, sha256, ошибка, опциональный путь к debug raw-файлу.

4. Live raw archive
   - `raw/<run_id>/manifest.jsonl`;
   - `raw/<run_id>/errors.jsonl`;
   - `raw/<run_id>/payloads/<sha-prefix>/<sha256>.bin.gz`;
   - используется для `raw-only` и `both`.

5. `parser_raw_responses`
   - legacy/probe storage внутри SQLite;
   - не является production-raw для VDS.

## Команды

Инициализация parser-таблиц в основной БД:

```powershell
python .\gosuslugi_parser.py init-db --db .\vuzonline_2025_full.sqlite
```

Создать sandbox-очередь из архива 2025. Это fixture-режим, не production-путь:

```powershell
python .\gosuslugi_parser.py seed-fixtures --db .\parser_sandbox.sqlite --limit 100
```

Прогнать импорт fixture-данных в sandbox:

```powershell
python .\gosuslugi_parser.py fixture-run --db .\parser_sandbox.sqlite --limit 20
```

Безопасный dry-run HTTP probe:

```powershell
python .\gosuslugi_parser.py http-probe "https://www.gosuslugi.ru/" --dry-run
```

Одиночный live probe с лимитом:

```powershell
python .\gosuslugi_parser.py http-probe "https://www.gosuslugi.ru/" --rpm 55
```

Live crawl dry-run:

```powershell
python .\gosuslugi_parser.py live-crawl --mode raw-only --raw-dir I:\VvuzzRaw --url "https://www.gosuslugi.ru/" --dry-run
```

Live crawl с локальным raw и удаленным ingest:

```powershell
python .\gosuslugi_parser.py live-crawl --mode both `
  --raw-dir I:\VvuzzRaw `
  --target ingest+https://vvuzz.intdata.pro/api/ingest/batch `
  --ingest-token <token> `
  --url "https://actual-gosuslugi-endpoint"
```

## Что еще нужно сделать после появления данных

- Найти реальные URL/API списков через браузерный Network.
- Зафиксировать реальный формат ответа в `docs/facts.md`.
- Написать live-discovery адаптер, который заполняет `parser_queue`.
- Написать live-fetch адаптер, который сохраняет raw response и вызывает нормализацию.
- Проверить, отличается ли нормализуемая структура 2026 от fixture-архива 2025.
- Добавить staleness-поля в UI: когда конкретный конкурс обновлялся последний раз.

## Stop Conditions

Парсер должен останавливаться или уходить в backoff при:

- HTTP `403`;
- HTTP `429`;
- капче/странице блокировки;
- серии timeout-ов;
- изменении схемы ответа;
- превышении установленного лимита rpm.
