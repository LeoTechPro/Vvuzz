# Vvuzz Public: migration, beta and rollback

Issue: https://github.com/LeoTechPro/int/issues/808

Этот runbook описывает команды для отдельного owner-approved окна. Наличие
команд в репозитории не разрешает применять их к live DB или runtime.

## 1. Read-only preflight

```bash
cd /int/vvuzz
python3 migrations/migrate.py --db /int/vvuzz/vuzonline_2025_norm.vds.sqlite
sqlite3 /int/vvuzz/vuzonline_2025_norm.vds.sqlite 'PRAGMA integrity_check;'
df -B1 /int/vvuzz
free -b
```

Операция останавливается, если integrity check не равен `ok` или прогноз
оставляет менее 5% диска. Перед apply нужно записать размеры DB/WAL и счётчики
`competitions_core`, `applications`, `applicants`.

## 2. Additive migration

Только после отдельного approval на точный путь:

```bash
python3 migrations/migrate.py \
  --db /int/vvuzz/vuzonline_2025_norm.vds.sqlite \
  --apply \
  --with-stats
```

Миграция добавляет таблицы и nullable `competitions_core.campaign_id`. Она не
удаляет и не переименовывает старые таблицы. Команда завершится ошибкой при
изменении трёх контрольных счётчиков.

## 3. Secrets and first management user

`.env.public` должен быть создан оператором вне Git:

```dotenv
VVUZZ_CACHE_HMAC_KEY=<random-at-least-32-bytes>
VVUZZ_INGEST_TOKEN=<separate-random-token>
```

После миграции:

```bash
VVUZZ_ENV=production \
VVUZZ_DB_PATH=/int/vvuzz/vuzonline_2025_norm.vds.sqlite \
VVUZZ_CACHE_HMAC_KEY='<key>' \
python3 -m vvuzz_public.cli create-admin --username '<operator>'
```

Дефолтной учётной записи нет. Старая таблица `users` новым `/manage` не
используется.

## 4. Beta

После approval:

1. Создать отдельное окружение runtime и установить зафиксированные
   зависимости:

   ```bash
   python3 -m venv /int/.runtime/vvuzz-public/venv
   /int/.runtime/vvuzz-public/venv/bin/pip install \
     --require-hashes \
     --requirement /int/vvuzz/requirements.lock
   ```

2. Установить `deploy/systemd/vvuzz-public-beta.service`.
3. Добавить директивы из
   `deploy/nginx/vvuzz-public-http.conf.example` внутрь `http{}`, а locations
   из `deploy/nginx/vvuzz-public-beta.conf.example` — внутрь TLS `server{}`.
4. Выполнить `nginx -t`, затем разрешённые reload/start.
5. Проверить `/beta/healthz`, `/beta`, `/beta/archive` и `/beta/manage`.
6. Убедиться, что формат access log записывает `$uri`, а не `$request_uri`, и
   не содержит тела запроса.

Старый процесс на `127.0.0.1:9176` не останавливается.

## 5. Ingest v2 transition

Новый endpoint требует:

- `campaign_year`;
- `run_id`;
- `source_key`;
- `snapshot_at`;
- `competitions`.

Повтор `(campaign_year, run_id, source_key)` возвращает исходный snapshot и не
пишет данные повторно. `refresh_stats=true` используется на завершении
согласованного снимка; `finalize_campaign=true` фиксирует финальный снимок.

Legacy `/api/ingest/batch` остаётся направлен на старый процесс до отдельного
обновления и проверки парсера.

## 6. Production gate

До переключения обязательны:

- паритет 2025/2026 и тестовых ID;
- performance/security acceptance;
- разрешённая browser QA;
- независимый review полного commit range;
- owner-решение о публичности псевдонимного ID;
- точное approval на DB apply, systemd и Nginx.

## 7. Rollback

Rollback runtime не требует отката DB:

1. Вернуть корневой `location /` на `127.0.0.1:9176`.
2. Проверить `nginx -t`.
3. Выполнить разрешённый reload Nginx.
4. Проверить старую главную, публичный поиск и legacy ingest.

Новые таблицы остаются на месте; старый reader их не использует.
