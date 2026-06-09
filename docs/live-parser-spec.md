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

## Current limitation

До фиксации актуальных API/URL Госуслуг live parser готовит transport/raw/ingest pipeline. Реальный discovery и mapping 2026 нужно добавить после появления данных и просмотра сетевых запросов браузера.
