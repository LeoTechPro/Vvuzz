# Live parser / ingest / raw spec

## Objective

Парсер должен запускаться на личном ПК, читать публичные данные Госуслуг с лимитами, сохранять raw локально и наполнять нормализованные SQLite-цели.

На VDS raw не хранится. Удаленная SQLite наполняется только через ingest API, потому что SQLite нельзя надежно писать как удаленный сетевой файл.

## Run modes

1. `raw-only`
   - пишет только локальный raw-архив;
   - не принимает `--target`;
   - используется для аудита, повторного разбора и отладки формата.

2. `norm-only`
   - raw payload files не создает;
   - требует минимум один `--target`;
   - цель может быть `sqlite://<path>` или `ingest+https://vvuzz.intdata.pro/api/ingest/batch`.

3. `both`
   - пишет raw локально;
   - одновременно отправляет нормализованные данные в одну или несколько целей.

Примеры:

```powershell
python .\gosuslugi_parser.py live-crawl --mode raw-only --raw-dir I:\VvuzzRaw --url https://example.test/data

python .\gosuslugi_parser.py live-crawl --mode norm-only `
  --target sqlite://I:/Мой диск/Vvuzz/vvuzz-online/local_live.sqlite `
  --target ingest+https://vvuzz.intdata.pro/api/ingest/batch `
  --ingest-token <token> `
  --url https://example.test/data

python .\gosuslugi_parser.py live-crawl --mode both `
  --raw-dir I:\VvuzzRaw `
  --target sqlite://I:/Мой диск/Vvuzz/vvuzz-online/local_live.sqlite `
  --url https://example.test/data
```

## Raw format

Raw пишется в:

```text
raw/<run_id>/
  manifest.jsonl
  errors.jsonl
  payloads/<sha256-prefix>/<sha256>.bin.gz
```

`manifest.jsonl` хранит URL, status, content type, bytes, sha256, fetched_at и путь к payload.

`errors.jsonl` хранит HTTP errors, timeout, schema mismatch, captcha/block markers.

## Normalized targets

Нормализованная запись использует:

- `universities`;
- `programs`;
- `competitions_core`;
- `applicants`;
- `applications`.

Payload для ingest:

```json
{
  "run_id": "2026-06-09T12-00-00Z",
  "source": "gosuslugi",
  "source_key": "http:https://...",
  "payload_sha256": "...",
  "fetched_at": "2026-06-09T12:00:00+00:00",
  "competitions": [
    {
      "source_path": "stable-source-key",
      "file_name": "source.json",
      "program": "01.03.01 ...",
      "education_level": "Бакалавриат",
      "city": "Москва",
      "university": "Название вуза",
      "study_form": "Очная",
      "quota_type": "Общие места",
      "seats": 25,
      "path": ["optional", "source", "path"],
      "applications": [
        {
          "applicant_id": "123",
          "ordinal": 1,
          "competition_priority": 2,
          "consent_text": "-",
          "has_consent": false,
          "score_total": 250,
          "exam_scores_text": "80, 85, 85",
          "individual_score": 5,
          "status": "Участвует",
          "selected_at_moscow": null
        }
      ]
    }
  ]
}
```

## Limits and stop conditions

- Default `--rpm 55`.
- Историческое ограничение: больше 60 запросов в минуту на IP приводило к бану примерно на 3 часа.
- Default backoff: `3h`.
- Остановить run при:
  - HTTP `403`;
  - HTTP `429`;
  - captcha/block page;
  - repeated timeout;
  - schema mismatch;
  - превышение заданного rpm.

## Vuznavigator 2026 local-PC fetch

Для опубликованных списков 2026 используется отдельный адаптер `vuznav-fetch`. Его нормальный production-сценарий: запуск на личном ПК/доверенной сети, локальный raw-архив и запись нормализованных батчей в SQLite на VDS через ingest API.

Минимальный запуск с личного ПК:

```powershell
$env:VVUZZ_INGEST_TOKEN = "<token>"
python .\gosuslugi_parser.py vuznav-fetch --mode both `
  --raw-dir I:\VvuzzRaw `
  --target ingest+https://vvuzz.intdata.pro/api/ingest/batch `
  --url "https://www.gosuslugi.ru/vuznavigator/specialties/2.19.03.01/2/1214/1/103736/-/applicants/138272?program=2_1__103736___" `
  --rpm 10
```

Что делает `vuznav-fetch`:

1. извлекает `competition_id` из URL страницы Вузнавигатора;
2. проверяет public status endpoints `statuses/applicants` и `statuses/ratings`;
3. если `hasListOfApplicants=true`, получает `/competition/<id>/applicants`;
4. пишет raw payload только в `--raw-dir` при `raw-only`/`both`;
5. нормализует строки в ingest payload и отправляет его в `POST /api/ingest/batch`;
6. в ingest payload ставит `replace_applications=true`, чтобы новый снимок конкретного конкурса заменял старые строки этого конкурса, а не накапливал дубли.

По умолчанию для этого режима `--rpm 10`, не `55`, потому что Госуслуги чувствительны к IP/VPN/datacenter reputation.

## Current limitation

VDS может использоваться для discovery frontend/API схемы через статику `gu-st.ru`, но не является надежным источником production fetch: `www.gosuslugi.ru` может блокировать datacenter/VPN IP даже при VPN exclusions. Production fetch запускается с личного ПК/доверенной сети и пишет на VDS только через ingest API.
