# AGENTS.md для `vvuzz-online`

## Scope

Этот репозиторий содержит локальный прототип Vvuzz Online: интерфейс и parser scaffold для анализа публичных списков поступления в вуз.

## Source Of Truth

- ТЗ: `docs/tz.md`
- Изученные факты и ограничения: `docs/facts.md`
- План парсера: `docs/parser-plan.md`
- Спецификация live parser / ingest / raw: `docs/live-parser-spec.md`
- UI/backend: `app.py`
- Импорт исторического архива: `import_zip_to_sqlite.py`
- Parser scaffold: `gosuslugi_parser.py`

Исходные большие материалы лежат вне git:

- `I:\Мой диск\Vvuzz\2025-07-24 13-40-58.zip`
- `I:\Мой диск\Vvuzz\2026-04-16 13-15-49.mp4`
- локальные SQLite-файлы `*.sqlite`

## Rules

- Production/live-парсер должен наполнять только SQLite, без промежуточной генерации `.xlsx`.
- `.xlsx` допустимы только как исторический fixture-источник из архива 2025 или как отдельный будущий экспорт из SQLite по явной задаче.
- Live parser обязан иметь режимы `raw-only`, `norm-only`, `both`.
- Raw payload хранить только на личном ПК/локальном пути. На VDS raw не писать.
- Удаленную SQLite на VDS наполнять только через основной ingest v2 API (`POST /api/ingest/v2/batch`), а не прямой записью в sqlite-файл по сети. Legacy `POST /api/ingest/batch` сохраняется только как переходный rollback-контур.
- Большие данные, SQLite, zip, mp4, логи и raw dumps не коммитить.
- Для больших повторных импортов SQLite сначала собирать БД на локальном диске, проверять `PRAGMA integrity_check`, затем копировать готовый файл в рабочую папку.
- Не удалять старые БД, smoke-файлы, журналы или поврежденные артефакты без явного разрешения пользователя.
- Пароли в `users.password_text` намеренно хранятся открытым текстом: это защита от случайного входа, не production security.
- Роли: `admin`, `manager`, `applicant`.
- `admin` управляет пользователями через `/admin/users`; `manager` видит аналитические таблицы; `applicant` видит привязанный `applicant_id`.
- Публичный интерфейс `/public` доступен без логина, но не дает переходить по страницам соперников.

## Run

```powershell
python .\app.py --host 127.0.0.1 --port 8765
```

Открыть:

```text
http://127.0.0.1:8765
```

Логин по умолчанию: `prointdata@ya.ru`
Пароль по умолчанию: `admin`

## Parser Smoke

```powershell
python .\gosuslugi_parser.py seed-fixtures --db .\parser_sandbox.sqlite --limit 50
python .\gosuslugi_parser.py fixture-run --db .\parser_sandbox.sqlite --limit 20
python .\gosuslugi_parser.py http-probe "https://www.gosuslugi.ru/" --db .\parser_sandbox.sqlite --dry-run
python .\gosuslugi_parser.py live-crawl --mode raw-only --raw-dir I:\VvuzzRaw --url "https://www.gosuslugi.ru/" --dry-run
```

## Интерфейс и разметка

Избегайте ненужных вторичных меток и описаний. Пусть разметка сама всё объясняет.
