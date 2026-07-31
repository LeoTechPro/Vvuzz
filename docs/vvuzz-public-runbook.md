# Vvuzz Public: migration, beta and rollback

Issue: https://github.com/LeoTechPro/int/issues/808

Этот runbook описывает команды для отдельного owner-approved окна. Наличие
команд в репозитории не разрешает применять их к live DB или runtime.

## 1. Read-only preflight

```bash
cd /int/vvuzz
python3 migrations/migrate.py --db /int/vvuzz/vuzonline.vds.sqlite
sqlite3 /int/vvuzz/vuzonline.vds.sqlite 'PRAGMA integrity_check;'
df -B1 /int/vvuzz
free -b
```

Операция останавливается, если integrity check не равен `ok` или прогноз
оставляет менее 5% диска. Перед apply нужно записать размеры DB/WAL и счётчики
`competitions_core`, `applications`, `applicants`.

## 2. Owner-approved runtime data path

Публичный процесс не получает запись в `/int/vvuzz`: иначе он может создать
новый Python-модуль рядом с source и закрепиться после restart. Legacy и beta
поэтому должны читать одну SQLite из отдельного writable-каталога:

`/int/.runtime/vvuzz-public/data/vuzonline.vds.sqlite`.

Перемещение выполняется только в отдельное owner-approved окно с остановленным
legacy writer. Это path rewrite, поэтому approval должен называть все команды
и оба точных пути. На текущем диске используется rename в пределах filesystem,
а не полная копия DB, чтобы не нарушить 5% резерв. До approval команды ниже
не выполняются:

```bash
systemctl stop vvuzz-online.service
sqlite3 /int/vvuzz/vuzonline.vds.sqlite \
  'PRAGMA wal_checkpoint(TRUNCATE);'
install -d -o agents -g intdata -m 0750 \
  /int/.runtime/vvuzz-public/data
mv -- /int/vvuzz/vuzonline.vds.sqlite \
  /int/.runtime/vvuzz-public/data/vuzonline.vds.sqlite
```

Если после остановки существуют `-wal` или `-shm`, оператор сначала показывает
их через `ls -l` и получает approval на отдельный точный `mv` каждого файла.
При ошибке до установки unit DB возвращается обратным `mv` в исходный путь.
Ожидаемое дополнительное место для rename на том же filesystem — практически
0 байт. Основной риск — downtime или запуск unit с несовпадающим путём.
Проверка сразу после rename:

```bash
stat /int/.runtime/vvuzz-public/data/vuzonline.vds.sqlite
sqlite3 /int/.runtime/vvuzz-public/data/vuzonline.vds.sqlite \
  'PRAGMA integrity_check;'
df -B1 /int
```

## 3. Additive migration

Только после отдельного approval на точный путь:

```bash
python3 migrations/migrate.py \
  --db /int/.runtime/vvuzz-public/data/vuzonline.vds.sqlite \
  --apply \
  --with-stats
```

Миграция добавляет таблицы и nullable `competitions_core.campaign_id`. Она не
удаляет и не переименовывает старые таблицы. Команда завершится ошибкой при
изменении трёх контрольных счётчиков.

## 4. Secrets and first management user

`.env.public` должен быть создан оператором вне Git:

```dotenv
VVUZZ_CACHE_HMAC_KEY=<random-at-least-32-bytes>
VVUZZ_INGEST_TOKEN=<separate-random-token>
```

После миграции:

```bash
VVUZZ_ENV=production \
VVUZZ_DB_PATH=/int/.runtime/vvuzz-public/data/vuzonline.vds.sqlite \
VVUZZ_CACHE_HMAC_KEY='<key>' \
python3 -m vvuzz_public.cli create-admin --username '<operator>'
```

Дефолтной учётной записи нет. Старая таблица `users` новым `/manage` не
используется.

## 5. Beta

После approval:

1. Создать отдельное окружение runtime и установить зафиксированные
   зависимости:

   ```bash
   python3 -m venv /int/.runtime/vvuzz-public/venv
   /int/.runtime/vvuzz-public/venv/bin/pip install \
     --require-hashes \
     --requirement /int/vvuzz/requirements.lock
   ```

2. Установить обновлённый `deploy/systemd/vvuzz-online.service` и
   `deploy/systemd/vvuzz-public-beta.service`: оба используют одну runtime DB.
3. Добавить директивы из
   `deploy/nginx/vvuzz-public-http.conf.example` внутрь `http{}`, а locations
   из `deploy/nginx/vvuzz-public-beta.conf.example` — внутрь TLS `server{}`.
4. Выполнить `nginx -t`, затем разрешённые reload/start.
5. Проверить `/beta/healthz`, `/beta`, `/beta/archive` и `/beta/manage`.
6. Убедиться, что формат access log записывает `$uri`, а не `$request_uri`, и
   не содержит тела запроса.

Старый процесс на `127.0.0.1:9176` не останавливается.

## 6. Performance acceptance

Тест выполняется только после отдельного approval на beta/runtime. Файл ID
хранится в task scratch, не коммитится и содержит по одному ID на строку:
обычные, отсутствующий, а последней строкой — экстремальный ID с 500+
конкурсами. Скрипт не печатает сырые ID и возвращает только агрегаты.

Latency проверяется напрямую на beta upstream, чтобы Nginx rate limit не
искажал последовательные замеры:

```bash
/int/.runtime/vvuzz-public/venv/bin/python \
  /int/vvuzz/scripts/benchmark_beta.py \
  --mode performance \
  --base-url http://127.0.0.1:9186 \
  --year 2026 \
  --ids-file /int/.tmp/codex-agent/vvuzz-808-performance-ids.txt
```

25 одновременных публичных поисков проверяются отдельно через `/beta`. Для
этого у lookup и competition разные Nginx-зоны: burst не складывается между
двумя запросами одного поиска, но устойчивый lookup-rate остаётся 10 запросов
в минуту и сдерживает enumeration. Одновременно скрипт получает
`BEGIN IMMEDIATE`, удерживает его 250 мс и откатывает: это проверяет доступность
того же SQLite write-lock, который нужен ingest, без сохранения тестовых строк.

```bash
/int/.runtime/vvuzz-public/venv/bin/python \
  /int/vvuzz/scripts/benchmark_beta.py \
  --mode parallel \
  --base-url https://vvuzz.intdata.pro/beta \
  --year 2026 \
  --concurrency 25 \
  --db-path /int/.runtime/vvuzz-public/data/vuzonline.vds.sqlite \
  --ids-file /int/.tmp/codex-agent/vvuzz-808-performance-ids.txt
```

Оба JSON-отчёта сохраняются как evidence Issue #808. После проверки файл ID
остаётся task scratch и удаляется только по отдельному cleanup approval.

## 7. Ingest v2 transition

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

Штатный production-запуск выполняется с доверенного ПК:

```powershell
$env:VVUZZ_INGEST_TOKEN = "<token>"
python .\gosuslugi_parser.py vuznav-fetch --mode both `
  --raw-dir I:\VvuzzRaw `
  --competition-id-file .\vuznavigator-2026-competition-ids.txt `
  --target ingest+https://vvuzz.intdata.pro/api/ingest/v2/batch `
  --campaign-year 2026 `
  --refresh-stats `
  --no-finalize-campaign `
  --rpm 10 --stop-on-error
```

Все batches одного прохода используют общий `run_id`; `source_key` привязан к
конкурсу. Только после прохода без ошибок отправляется completion batch с
пустым `competitions`, который пересчитывает агрегаты. Повтор команды с тем же
явными одинаковыми `--run-id` и `--snapshot-at` безопасен при неизменных
payload. Без этих параметров новый запуск является новым снимком. Raw остаётся
только на ПК.

ID-manifest формируется на VDS без персональных данных:

```bash
sqlite3 /int/.runtime/vvuzz-public/data/vuzonline.vds.sqlite \
  "SELECT CAST(replace(replace(source_path, 'gosuslugi2026/competition/', ''), '/applicants.json', '') AS INTEGER)
   FROM competitions_core c JOIN campaigns ca ON ca.id=c.campaign_id
   WHERE ca.year=2026 AND source_path GLOB 'gosuslugi2026/competition/*/applicants.json'
   ORDER BY 1" > /int/.tmp/codex-agent/vuznavigator-2026-competition-ids.txt
```

Перед запуском файл копируется на доверенный ПК. Получение данных с VDS или
VPN-заблокированного контура запрещено; ответ `/vpn-block` завершает run без
completion batch.

После production-переключения устанавливается
`deploy/systemd/vvuzz-public.service`. Beta unit останавливается, но сохраняется
как воспроизводимый rollback-артефакт. Legacy endpoint и процесс остаются
доступны как переходный rollback-контур; их удаление требует отдельного
OpenSpec-изменения.

## 8. Production gate

До переключения обязательны:

- паритет 2025/2026 и тестовых ID;
- performance/security acceptance;
- разрешённая browser QA;
- независимый review полного commit range;
- owner-решение о публичности псевдонимного ID;
- точное approval на DB apply, systemd и Nginx.

## 9. Rollback

Rollback runtime не требует отката DB:

1. Вернуть корневой `location /` на `127.0.0.1:9176`.
2. Проверить `nginx -t`.
3. Выполнить разрешённый reload Nginx.
4. Проверить старую главную, публичный поиск и legacy ingest.

Новые таблицы остаются на месте; старый reader их не использует.
