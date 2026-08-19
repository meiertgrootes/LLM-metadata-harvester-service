# Service Test Draft

This guide validates the current branch locally with Docker Compose. It covers
automated checks, migrations, health endpoints, single and batch harvesting,
signed webhook delivery, PostgreSQL outbox recovery, ephemeral Redis behavior,
and final log inspection.

## Prerequisites

- Docker Desktop with Docker Compose
- Python 3.12
- `curl`
- Internet access while building the images
- A valid provider API key for `gemini-3.5-flash-lite`

Provider-backed harvests may incur usage costs. The webhook allowlist used in
this guide bypasses private-address protection and must only be used for local
development.

## 1. Confirm the branch

```bash
git switch 10a_ephemeral_DB_and_batch_support
git status --short --branch
git log --oneline -5
```

Confirm that the current branch is `10a_ephemeral_DB_and_batch_support` and
that it contains the webhook task registration commit.

## 2. Run automated checks

Use a temporary virtual environment so the repository remains unchanged:

```bash
python3.12 -m venv /tmp/llm-harvester-test-venv
source /tmp/llm-harvester-test-venv/bin/activate
python -m pip install ".[api,runtime,dev]"

pytest tests -q
ruff check src tests migrations scripts
mypy src
```

Expected results:

```text
53 passed
All checks passed!
Success: no issues found
```

The Starlette `httpx` deprecation warning emitted by pytest is currently
expected.

## 3. Configure the test environment

Run these exports in the terminal that will run Compose:

```bash
export COMPOSE_PROJECT_NAME=harvester-10a-test
export WEBHOOK_SECRET_KEY="$(
  python3 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
)"
export WEBHOOK_ALLOWED_HOSTS=host.docker.internal
export JOB_RECONCILE_INTERVAL_SECONDS=5
export JOB_QUEUED_STALE_SECONDS=15

export MODEL=gemini-3.5-flash-lite
export PROVIDER_API_KEY='<your-real-provider-api-key>'
export WEBHOOK_TEST_SECRET='local-webhook-secret-123'
```

Keep these values in the same shell while running the remaining steps. In
particular, do not rotate `WEBHOOK_SECRET_KEY` while an undelivered outbox row
exists.

## 4. Build and start the stack

```bash
docker compose -f nerdctl-compose.dev.yml config
docker compose -f nerdctl-compose.dev.yml up --build -d
docker compose -f nerdctl-compose.dev.yml ps
```

The first build may take several minutes because it clones the harvester and
installs Playwright.

Inspect startup and migration logs:

```bash
docker compose -f nerdctl-compose.dev.yml logs migrate
docker compose -f nerdctl-compose.dev.yml logs --tail=100 api worker beat
```

Verify the database revision:

```bash
docker compose -f nerdctl-compose.dev.yml exec -T postgres \
  psql -U harvester -d harvester \
  -c 'SELECT version_num FROM alembic_version;'
```

Expected revision:

```text
0003
```

## 5. Check service health

```bash
curl -fsS http://localhost/health/live
curl -fsS http://localhost/health/ready
```

Expected responses:

```json
{"status":"alive"}
{"status":"ready","checks":{"postgres":"ok","redis":"ok"}}
```

Verify worker connectivity and task registration:

```bash
docker compose -f nerdctl-compose.dev.yml exec -T worker \
  celery -A llm_metadata_harvester_service.core.celery_app.celery_app \
  inspect ping --timeout 10

docker compose -f nerdctl-compose.dev.yml exec -T worker \
  celery -A llm_metadata_harvester_service.core.celery_app.celery_app \
  inspect registered --timeout 10
```

The registration output must include:

```text
llm_metadata_harvester_service.workers.tasks.reconcile_stale_jobs
llm_metadata_harvester_service.workers.tasks.run_harvester_task
llm_metadata_harvester_service.workers.webhook.deliver_job_webhook
llm_metadata_harvester_service.workers.webhook.publish_pending_webhooks
```

## 6. Verify Redis is nonpersistent

```bash
docker compose -f nerdctl-compose.dev.yml exec -T redis \
  redis-cli CONFIG GET save appendonly

docker compose -f nerdctl-compose.dev.yml config --volumes
```

Expected results:

- `save` is empty.
- `appendonly` is `no`.
- Only the PostgreSQL `pgdata` volume is listed.
- No Redis data volume exists.

## 7. Test request validation

A request without an API key should return HTTP `422`:

```bash
curl -i -X POST http://localhost/jobs/ \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"url\":\"https://example.com\"}"
```

A private webhook destination should return HTTP `422`:

```bash
curl -i -X POST http://localhost/jobs/ \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $PROVIDER_API_KEY" \
  -d "{
    \"model\":\"$MODEL\",
    \"url\":\"https://example.com\",
    \"webhook_url\":\"http://127.0.0.1:8080/\"
  }"
```

A request containing a webhook secret shorter than 16 characters should also
return HTTP `422`.

## 8. Test a single harvest

Submit a job:

```bash
JOB_RESPONSE="$(
  curl -fsS -X POST http://localhost/jobs/ \
    -H 'Content-Type: application/json' \
    -H "X-API-Key: $PROVIDER_API_KEY" \
    -d "{\"model\":\"$MODEL\",\"url\":\"https://example.com\"}"
)"

printf '%s\n' "$JOB_RESPONSE" | python3 -m json.tool

JOB_ID="$(
  printf '%s' "$JOB_RESPONSE" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])'
)"

printf 'JOB_ID=%s\n' "$JOB_ID"
```

Poll until the job reaches a terminal state:

```bash
while :; do
  STATUS_RESPONSE="$(curl -fsS "http://localhost/jobs/$JOB_ID")"
  printf '%s\n' "$STATUS_RESPONSE"

  JOB_STATUS="$(
    printf '%s' "$STATUS_RESPONSE" |
      python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])'
  )"

  case "$JOB_STATUS" in
    success|failure) break ;;
  esac

  sleep 2
```

Retrieve the result:

```bash
curl -sS "http://localhost/jobs/$JOB_ID/result" |
  python3 -m json.tool
```

A successful response includes `job_id`, `status`, `model`, `result`, and
`logs`. If the job fails, inspect its stored logs and the worker logs before
continuing.

## 9. Test batch processing

Each URL invokes the provider independently and may incur usage costs.

```bash
BATCH_RESPONSE="$(
  curl -fsS -X POST http://localhost/jobs/batch/ \
    -H 'Content-Type: application/json' \
    -H "X-API-Key: $PROVIDER_API_KEY" \
    -d "{
      \"model\":\"$MODEL\",
      \"urls\":[
        \"https://example.com\",
        \"https://example.org\"
      ]
    }"
)"

printf '%s\n' "$BATCH_RESPONSE" | python3 -m json.tool

BATCH_ID="$(
  printf '%s' "$BATCH_RESPONSE" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["batch_id"])'
)"
```

Monitor and retrieve the batch:

```bash
curl -fsS "http://localhost/batches/$BATCH_ID" |
  python3 -m json.tool

curl -fsS "http://localhost/batches/$BATCH_ID/results" |
  python3 -m json.tool
```

Confirm that the batch contains two distinct job IDs, each job retains its
original URL, and each result or failure is reported independently.

## 10. Start a signed webhook receiver

Open a second terminal and run:

```bash
cd /path/to/LLM-metadata-harvester-service
source /tmp/llm-harvester-test-venv/bin/activate
export WEBHOOK_RECEIVER_SECRET='local-webhook-secret-123'
python scripts/webhook_receiver.py
```

From the first terminal, verify that the receiver is available:

```bash
curl -fsS http://localhost:8080/
```

Expected response:

```json
{"status":"ok"}
```

On Docker Desktop, the worker reaches this receiver through
`host.docker.internal`. Other container runtimes may require a different host
alias.

## 11. Test durable webhook recovery

Stop Beat and clear Redis before submitting the job. This prevents an existing
publisher task from processing the new outbox row:

```bash
docker compose -f nerdctl-compose.dev.yml stop beat
docker compose -f nerdctl-compose.dev.yml restart redis
```

Submit a signed webhook job:

```bash
WEBHOOK_RESPONSE="$(
  curl -fsS -X POST http://localhost/jobs/ \
    -H 'Content-Type: application/json' \
    -H "X-API-Key: $PROVIDER_API_KEY" \
    -d "{
      \"model\":\"$MODEL\",
      \"url\":\"https://example.com\",
      \"webhook_url\":\"http://host.docker.internal:8080/\",
      \"webhook_secret\":\"$WEBHOOK_TEST_SECRET\"
    }"
)"

WEBHOOK_JOB_ID="$(
  printf '%s' "$WEBHOOK_RESPONSE" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])'
)"

printf '%s\n' "$WEBHOOK_RESPONSE" | python3 -m json.tool
```

Wait for the harvest to reach `success` or `failure`. Because Beat is stopped,
the outbox row should remain undelivered:

```bash
docker compose -f nerdctl-compose.dev.yml exec -T postgres \
  psql -U harvester -d harvester -c "
    SELECT job_id, attempts, published_at, delivered_at,
           exhausted_at, last_error,
           encrypted_secret IS NOT NULL AS secret_present
    FROM webhook_outbox
    WHERE job_id = '$WEBHOOK_JOB_ID';
  "
```

Expected before recovery:

- `attempts = 0`
- `delivered_at` is null
- `secret_present = true`

Clear Redis again and restart Beat:

```bash
docker compose -f nerdctl-compose.dev.yml restart redis
docker compose -f nerdctl-compose.dev.yml start beat
```

Wait approximately 5 to 15 seconds and repeat the database query. Expected
after recovery:

- The receiver prints a `job.completed` or `job.failed` payload.
- Signature verification succeeds.
- `attempts >= 1`.
- `delivered_at` is populated.
- `encrypted_secret` has been cleared.

This demonstrates that webhook recovery comes from PostgreSQL rather than
Redis.

## 12. Test ephemeral harvest loss

Stop the worker so the next harvest remains queued:

```bash
docker compose -f nerdctl-compose.dev.yml stop worker
```

Submit another job:

```bash
LOST_RESPONSE="$(
  curl -fsS -X POST http://localhost/jobs/ \
    -H 'Content-Type: application/json' \
    -H "X-API-Key: $PROVIDER_API_KEY" \
    -d "{\"model\":\"$MODEL\",\"url\":\"https://example.com\"}"
)"

LOST_JOB_ID="$(
  printf '%s' "$LOST_RESPONSE" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])'
)"

curl -fsS "http://localhost/jobs/$LOST_JOB_ID"
```

The initial status should be `queued`. Restart Redis to discard the transient
task, then restart the worker:

```bash
docker compose -f nerdctl-compose.dev.yml restart redis
docker compose -f nerdctl-compose.dev.yml start worker
```

The test configuration uses a 15-second queued timeout and a 5-second
reconciliation interval. Wait approximately 25 seconds:

```bash
sleep 25

curl -fsS "http://localhost/jobs/$LOST_JOB_ID" |
  python3 -m json.tool

curl -i "http://localhost/jobs/$LOST_JOB_ID/result"
```

Expected results:

- The job status becomes `failure`.
- The result endpoint returns HTTP `500`.
- The error detail is `dispatch_lost`.

This verifies that harvest work does not survive Redis loss while its
PostgreSQL state is reconciled.

## 13. Verify API keys are not stored

```bash
docker compose -f nerdctl-compose.dev.yml exec -T postgres \
  psql -U harvester -d harvester -c "
    SELECT column_name
    FROM information_schema.columns
    WHERE table_name IN ('jobs', 'webhook_outbox')
      AND column_name ILIKE '%api_key%';
  "
```

Expected result: zero rows.

Provider API keys are present only in transient harvest task messages. Do not
print Redis queue payloads during this test because doing so would expose the
key.

## 14. Inspect final logs and persisted outcomes

Collect the latest service logs:

```bash
docker compose -f nerdctl-compose.dev.yml logs --tail=200 \
  api worker beat redis postgres migrate
```

Review the output for:

- Unexpected tracebacks
- Unregistered Celery tasks
- Repeated connection failures or reconnection loops
- Failed Alembic migrations
- PostgreSQL errors or corruption warnings
- Webhook publication or delivery errors
- Unexpected API `500` or readiness `503` responses

The following events are expected during this test plan:

- Clean Redis `SIGTERM` shutdowns and restarts caused by Steps 11 and 12
- A transient readiness `503` while Redis is restarting
- A `500` result response for the intentionally lost job
- A development worker `KeyboardInterrupt` when the watchfiles wrapper is
  intentionally restarted
- Celery's warning that the development worker runs as root

Verify the final database state:

```bash
docker compose -f nerdctl-compose.dev.yml exec -T postgres \
  psql -U harvester -d harvester -P pager=off -c "
    SELECT version_num FROM alembic_version;

    SELECT job_id, status, error, webhook_attempts,
           webhook_delivered_at, webhook_error
    FROM jobs
    ORDER BY created_at DESC
    LIMIT 10;

    SELECT job_id, attempts, delivered_at, exhausted_at, last_error
    FROM webhook_outbox
    ORDER BY created_at DESC
    LIMIT 10;
  "
```

Confirm that migration `0003` is active, the signed webhook was delivered, the
outbox has no unexpected error, and the intentionally lost job is marked
`dispatch_lost`.

## 15. Shut down and clean up

Stop the webhook receiver with `Ctrl-C`.

Stop the Compose stack while preserving PostgreSQL test data:

```bash
docker compose -f nerdctl-compose.dev.yml down
```

Alternatively, remove the isolated test containers and PostgreSQL volume:

```bash
docker compose -f nerdctl-compose.dev.yml down -v
```

Because `COMPOSE_PROJECT_NAME=harvester-10a-test` was used, `down -v` removes
only the isolated test project. Deactivate the temporary environment when
finished:

```bash
deactivate
```
