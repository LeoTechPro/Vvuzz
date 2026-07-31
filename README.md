# Vvuzz Online

Локальный прототип для изучения архива списков Госуслуг 2025.

## Состав

- `import_zip_to_sqlite.py` - импортирует `../2025-07-24 13-40-58.zip` в SQLite.
- `import_zip_to_norm_sqlite.py` - импортирует zip сразу в нормализованную SQLite без промежуточной денормализованной БД.
- `app.py` - локальный web-интерфейс с простой авторизацией.
- `chance.py` - helper формулы шанса поступления.
- `gosuslugi_parser.py` - безопасный parser scaffold для fixture/dry-run/live-probe; live/default путь пишет сразу в SQLite.
- `normalize_sqlite.py` - строит нормализованную SQLite без дублей `applicant_id`/вузов/программ и с compatibility views для текущего UI.
- `vuzonline_2025_norm.sqlite` - основная нормализованная БД полного архива для интерфейса.
- `vuzonline_2025_full.sqlite` - старая полная денормализованная БД полного архива; локальный артефакт, не тащить на VDS.
- `vuzonline_2025.sqlite` - старый поврежденный артефакт первого импорта напрямую в DriveFS; не использовать.

## Запуск импорта

```powershell
python .\import_zip_to_sqlite.py --db .\vuzonline_2025_full.sqlite
```

Для быстрой проверки:

```powershell
python .\import_zip_to_sqlite.py --limit 20
```

## Web-интерфейс

```powershell
python .\app.py --host 127.0.0.1 --port 8765
```

Открыть: <http://127.0.0.1:8765>

Логин по умолчанию: `prointdata@ya.ru`
Пароль по умолчанию: `admin`

Пользователи хранятся в таблице `users`:

```sql
INSERT INTO users(username, password_text, role, applicant_id, is_active, created_at, updated_at)
VALUES ('demo', 'demo', 'manager', NULL, 1, datetime('now'), datetime('now'));
```

Пароль намеренно хранится обычным текстом: это локальный интерфейс для публичных данных.

Основные маршруты:

- `/public` - публичный поиск по ID без логина.
- `/universities`, `/competitions`, `/applicants` - закрытые таблицы для `admin`/`manager`.
- `/admin/users` - управление пользователями для `admin`.

## Parser scaffold

Инициализировать parser-таблицы в основной БД:

```powershell
python .\gosuslugi_parser.py init-db --db .\vuzonline_2025_full.sqlite
```

Проверить без live-данных на sandbox-БД:

```powershell
python .\gosuslugi_parser.py seed-fixtures --db .\parser_sandbox.sqlite --limit 50
python .\gosuslugi_parser.py fixture-run --db .\parser_sandbox.sqlite --limit 20
python .\gosuslugi_parser.py http-probe "https://www.gosuslugi.ru/" --db .\parser_sandbox.sqlite --dry-run
```

Live probe/crawl по умолчанию ограничен `55` запросами в минуту, но массовый обход Госуслуг до появления списков не запускать.

Production-правило: парсер не создает промежуточные `.xlsx`. Live parser имеет режимы `raw-only`, `norm-only`, `both`. Raw хранится локально в `manifest.jsonl + payloads/*.bin.gz`, на VDS raw не пишется. Удаленная SQLite на VDS пополняется только через `POST /api/ingest/v2/batch`.

Пример:

```powershell
python .\gosuslugi_parser.py live-crawl --mode both `
  --raw-dir I:\VvuzzRaw `
  --target ingest+https://vvuzz.intdata.pro/api/ingest/v2/batch `
  --ingest-token <token> `
  --url "https://actual-gosuslugi-endpoint"
```

## Текущая полная БД

Файл для интерфейса: `vuzonline_2025_norm.sqlite`

Проверено:

- `PRAGMA integrity_check`: `ok`
- конкурсов: `116093`
- строк абитуриентов: `11376040`
- уникальных ID абитуриентов: `1167801`
- пример ID для тестов: `4150096`
- размер нормализованной БД: около `2.74 ГБ`.

Важно: первый полный импорт напрямую в Google Drive/DriveFS дал поврежденный SQLite-файл. Для больших повторных импортов безопаснее собирать БД на локальном диске, проверять `integrity_check`, затем копировать готовый файл в проект.

## Нормализованная БД

Собрать нормализованную копию:

```powershell
python .\normalize_sqlite.py --source .\vuzonline_2025_full.sqlite --target .\vuzonline_2025_norm.sqlite
```

Схема:

- `applicants` - поступающие, один ряд на `applicant_id`;
- `universities` - вузы;
- `programs` - направления/программы;
- `competitions_core` - конкурсы;
- `applications` - связь поступающий/конкурс и конкурсные данные по этой связи;
- `competitions` и `entries` - compatibility views для текущего интерфейса.
