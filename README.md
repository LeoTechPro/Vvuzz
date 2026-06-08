# Vvuzz Online

Локальный прототип для изучения архива списков Госуслуг 2025.

## Состав

- `import_zip_to_sqlite.py` - импортирует `../2025-07-24 13-40-58.zip` в SQLite.
- `app.py` - локальный web-интерфейс с простой авторизацией.
- `gosuslugi_parser.py` - безопасный parser scaffold для fixture/dry-run/live-probe; live/default путь пишет сразу в SQLite.
- `vuzonline_2025_full.sqlite` - основная проверенная БД полного архива.
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

Логин по умолчанию: `admin`  
Пароль по умолчанию: `admin`

Пользователи хранятся в таблице `users`:

```sql
INSERT INTO users(username, password_text, created_at)
VALUES ('demo', 'demo', datetime('now'));
```

Пароль намеренно хранится обычным текстом: это локальный интерфейс для публичных данных.

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

Live probe по умолчанию ограничен `55` запросами в минуту, но массовый обход Госуслуг до появления списков не запускать.

Production-правило: парсер не создает промежуточные `.xlsx`. Live/probe payload хранится в SQLite в `parser_raw_responses`, нормализованные данные должны попадать в `competitions`/`entries`. `.xlsx` здесь используется только как исторический fixture-источник архива 2025.

## Текущая полная БД

Файл: `vuzonline_2025_full.sqlite`

Проверено:

- `PRAGMA integrity_check`: `ok`
- конкурсов: `116093`
- строк абитуриентов: `11376040`
- уникальных ID абитуриентов: `1167801`
- пример ID для тестов: `4150096`

Важно: первый полный импорт напрямую в Google Drive/DriveFS дал поврежденный SQLite-файл. Для больших повторных импортов безопаснее собирать БД на локальном диске, проверять `integrity_check`, затем копировать готовый файл в проект.
