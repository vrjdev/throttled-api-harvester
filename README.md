# throttled-api-harvester

Calls an API in fixed increments (`offset = counter * STEP`, with `counter` counting **down** from `TOTAL_REQUESTS` to 1) at a hard-capped rate and stores every
response in S3. Built for ~10M requests under a **2 QPS** API limit.

## How it works

- **Two workers, one per machine, 1 QPS each** (default 0.95 for headroom). Worker `k` handles
  sequence positions `i` where `i % 2 == k` (`counter = TOTAL_REQUESTS - i`), so the halves never overlap and no coordination is needed.
- **Rate limiter** spaces calls evenly (no bursts). **No retries:** every request is made once.
  Any problem (4xx, 429, 5xx, timeout, network error, unexpected exception) is logged as a warning and
  the worker continues with the next increment.
- **Storage:** about 1,000 responses per gzip-compressed JSONL object at `s3://BUCKET/results/w<k>/batch-<first i>.jsonl.gz`
  (read with `aws s3 cp <key> - | gunzip`, or Athena/Spark, which read `.gz` natively).
  Failed requests are stored with their HTTP `status` (or `"status": null` plus an `"error"` for
  network errors), so gaps can be found later by filtering on `status != 200`.
  If an S3 write fails, it is logged and the batch is kept in memory and written with the next flush.
- **Resumable:** `results/w<k>/checkpoint.json` holds the next `i`. Data is written before the
  checkpoint, and batch keys are deterministic, so a crash replays at most one batch without duplicates.
- 10M requests at 2 QPS is about 58 days (about 61 at 0.95 QPS per worker).

## Configuration (environment variables)

| Var | Default | Meaning |
|---|---|---|
| `API_URL` | required | Endpoint to call |
| `API_KEY` / `API_AUTH_HEADER` | empty / `Authorization` | Credential and header name |
| `API_PARAM` | `offset` | Query parameter that carries the increment |
| `STEP` | 100 | Increment size |
| `TOTAL_REQUESTS` | 10000000 | Number of increments |
| `WORKER_INDEX` / `WORKER_COUNT` | required / 2 | This worker's slot |
| `QPS` | 0.95 | Per-worker rate |
| `BUCKET`, `PREFIX` | required, `results` | Where results go |
| `BATCH_SIZE` | 1000 | Responses per S3 object |

## Deploy (AWS)

Two `t4g.nano` instances (about $3 a month each), one S3 bucket, least-privilege IAM, and the API
key in SSM Parameter Store.

```
cd infra
terraform init
terraform apply -var api_url=https://api.example.com/items -var api_key=... -var bucket_name=<unique>
```

## Local development

```
pip install -e '.[dev]'
pytest
```
