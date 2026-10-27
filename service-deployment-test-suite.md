# Service Deployment Validation

This guide separates behavior covered by automated tests from checks that an
administrator should perform against the actual deployment. It is intentionally
not a second, exhaustive copy of the automated test suite.

## Automated Coverage

GitHub Actions runs `.github/workflows/ci.yml` on every push. A successful CI
run executes formatting-independent static checks, the fast test suite, and a
Docker-backed integration suite without making billable provider requests.

The fast suite uses an isolated SQLite database and Celery memory broker. It
covers:

- Request validation, including missing API keys, private webhook destinations,
  and webhook secrets shorter than 16 characters.
- Single-job and batch API responses, persistence, dispatch, status, and result
  behavior.
- Optional field-list validation and forwarding for single and batch requests.
- Webhook URL protections, encryption, signing, retries, exhaustion, duplicate
  handling, and secret cleanup.
- Stale queued and pending job reconciliation.
- Celery task names, transient queue settings, and Beat schedule configuration.
- The development webhook receiver's health and signature verification.
- Alembic migration structure on SQLite.
- Successful rendering of the development, local, public, and integration
  Compose configurations.

The integration suite runs real PostgreSQL 16, Redis 7, API, Celery worker,
Celery Beat, migrations, and a controlled webhook receiver. A deterministic
test harvester replaces the external provider. It covers:

- Fresh and legacy PostgreSQL migrations through revision `0003`.
- Absence of API-key columns in PostgreSQL.
- API health and readiness against real PostgreSQL and Redis.
- Live worker connectivity and registration of all service tasks.
- Disabled Redis persistence and absence of a Redis data volume.
- End-to-end single and batch processing through Redis and Celery.
- Exact forwarding of requested metadata fields through the API and worker.
- Signed webhook delivery and durable outbox recovery after Redis loss.
- Encrypted webhook-secret cleanup after successful delivery.
- Loss of queued harvest work after Redis restart and subsequent
  `dispatch_lost` reconciliation from PostgreSQL state.

Run the same checks locally with:

```bash
python3.12 -m venv /tmp/llm-harvester-test-venv
source /tmp/llm-harvester-test-venv/bin/activate
python -m pip install ".[api,runtime,dev]"

pytest -q -m "not integration"
ruff check src tests migrations scripts
mypy src
docker compose -f docker-compose.test.yml config --quiet
pytest -q tests/integration
```

The integration suite builds an isolated test image, assigns random host ports,
uses a unique Compose project name, and removes its containers and volumes when
finished. It does not require a provider API key.

## Administrator Checks

CI cannot validate credentials, routing, certificates, storage, provider
availability, or infrastructure policies in the target environment. Perform
the following checks for each new deployment and after relevant infrastructure
changes.

### 1. Confirm The Release

Deploy only a commit with a successful GitHub Actions run. Record the commit or
image digest in the deployment notes.

Confirm that required configuration is supplied through the deployment's
secret manager or environment:

```text
DATABASE_URL
CELERY_BROKER_URL
WEBHOOK_SECRET_KEY
```

`WEBHOOK_SECRET_KEY` must be a stable Fernet key. Do not rotate or remove it
while an undelivered webhook outbox row may still contain an encrypted secret.
Provider API keys are supplied by clients and must not be configured as service
defaults or written to logs.

Render and inspect the deployment configuration before starting it. For the
provided Compose files, use:

```bash
docker compose -f nerdctl-compose.public.yml config
```

Confirm that PostgreSQL has durable storage and Redis does not have a data
volume. Verify that Redis is configured with an empty `save` setting and
`appendonly no`.

### 2. Verify Startup And Dependencies

Start the selected deployment track and verify that the migration service exits
successfully before the API and workers become ready.

```bash
docker compose -f nerdctl-compose.public.yml up -d
docker compose -f nerdctl-compose.public.yml ps
docker compose -f nerdctl-compose.public.yml logs migrate api worker beat
```

Check the deployed endpoints through the same proxy, DNS name, and TLS path
used by clients:

```bash
curl -fsS https://SERVICE_HOST/health/live
curl -fsS https://SERVICE_HOST/health/ready
```

Expected responses are:

```json
{"status":"alive"}
{"status":"ready","checks":{"postgres":"ok","redis":"ok"}}
```

Confirm that the worker is reachable through the deployed broker:

```bash
docker compose -f nerdctl-compose.public.yml exec -T worker \
  celery -A llm_metadata_harvester_service.core.celery_app.celery_app \
  inspect ping --timeout 10
```

These checks validate the deployment's actual credentials, DNS, firewall,
container network, proxy, and certificate configuration; automated CI cannot.

### 3. Run One Provider-Backed Harvest

The automated integration suite uses a deterministic fake harvester. Run one
small harvest with a real supported model and provider key to validate the
installed upstream harvester version, provider credentials, outbound network,
browser dependencies, and provider response format.

```bash
JOB_RESPONSE="$(
  curl -fsS -X POST https://SERVICE_HOST/jobs/ \
    -H 'Content-Type: application/json' \
    -H "X-API-Key: $PROVIDER_API_KEY" \
    -d "{\"model\":\"$MODEL\",\"url\":\"https://example.com\"}"
)"

JOB_ID="$(
  printf '%s' "$JOB_RESPONSE" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])'
)"

while :; do
  STATUS_RESPONSE="$(curl -fsS "https://SERVICE_HOST/jobs/$JOB_ID")"
  printf '%s\n' "$STATUS_RESPONSE"
  STATUS="$(
    printf '%s' "$STATUS_RESPONSE" |
      python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])'
  )"
  case "$STATUS" in
    success|failure) break ;;
  esac
  sleep 2
done

curl -fsS "https://SERVICE_HOST/jobs/$JOB_ID/result" |
  python3 -m json.tool
```

Provider-backed requests can incur usage charges. One successful request is
sufficient unless the deployment supports multiple providers or models with
different outbound requirements.

#### Verify Optional Field Selection

The `fields` property is optional and case-sensitive. Omitting it requests the
complete metadata standard. Empty lists and empty or whitespace-only names are
rejected with HTTP `422`.

Field selection also depends on the installed `llm-metadata-harvester`
version. The current upstream `main` signature does not accept `fields`; the
service then harvests all fields and stores this warning in the job logs:

```text
Installed llm_metadata_harvester does not support field selection; harvesting all fields.
```

Harvester candidate `0.1.4` accepts a field list. Development and local images
can select it at build time:

```bash
export HARVESTER_GIT_REF=0.1.4
docker compose -f nerdctl-compose.dev.yml up --build -d
```

After changing the harvester version, submit one provider-backed subset request:

```bash
curl -fsS -X POST https://SERVICE_HOST/jobs/ \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $PROVIDER_API_KEY" \
  -d "{
    \"model\":\"$MODEL\",
    \"url\":\"https://example.com\",
    \"fields\":[\"Title\",\"Description\",\"License\"]
  }"
```

With `0.1.4`, verify that the result contains the requested supported fields.
With legacy `main`, verify that the complete result and fallback warning are
returned. This provider-backed compatibility check remains manual because CI
uses a deterministic test harvester rather than cloning either upstream ref.

### 4. Verify The Real Webhook Path

CI verifies signing and durable delivery inside its isolated Docker network. An
administrator must still verify the real receiver, public DNS, TLS certificate,
egress rules, allowlist, proxy, and any bot or firewall protection.

Submit one job using a non-production test endpoint controlled by the deploying
organization:

```bash
curl -fsS -X POST https://SERVICE_HOST/jobs/ \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $PROVIDER_API_KEY" \
  -d "{
    \"model\":\"$MODEL\",
    \"url\":\"https://example.com\",
    \"webhook_url\":\"$WEBHOOK_TEST_URL\",
    \"webhook_secret\":\"$WEBHOOK_TEST_SECRET\"
  }"
```

Confirm at the receiver that:

- Exactly one terminal event arrives.
- `X-Webhook-Signature` validates against the exact request body.
- The event is `job.completed` or `job.failed` as appropriate.
- The receiver returns a `2xx` response.
- No provider API key or plaintext webhook secret appears in the payload or
  service logs.

The target URL must use the expected scheme and resolve to an address allowed by
the service's webhook security policy. DataverseNL and other source sites may
apply bot protection to harvest requests; webhook success does not demonstrate
that every source site is harvestable.

### 5. Verify Operational Durability

CI proves the application's Redis-loss behavior in an isolated stack. The
administrator remains responsible for validating deployment-specific storage
and recovery:

- PostgreSQL data resides on the intended durable volume or managed service.
- Database backups complete and a restore procedure has been tested.
- Redis has no snapshot, AOF, or persistent volume enabled.
- Monitoring alerts on API readiness failures, worker disconnects, migration
  failures, stale-job reconciliation, and exhausted webhook deliveries.
- Restart policies and orchestration recover the API, worker, and Beat without
  introducing duplicate service instances unexpectedly.

A destructive Redis-loss drill is optional for routine deployment because CI
already covers the service behavior. Run it in the target environment only when
validating new orchestration, broker, storage, or recovery configuration. Use a
dedicated test deployment, not active production traffic.

### 6. Review Logs And Persisted State

After the provider and webhook checks, inspect deployment logs for unexpected
tracebacks, unregistered tasks, repeated broker reconnects, migration errors,
webhook failures, readiness failures, or leaked credentials.

Confirm that completed jobs and webhook outcomes are present in PostgreSQL and
that no API-key column exists:

```sql
SELECT version_num FROM alembic_version;

SELECT job_id, status, error, webhook_attempts,
       webhook_delivered_at, webhook_error
FROM jobs
ORDER BY created_at DESC
LIMIT 10;

SELECT column_name
FROM information_schema.columns
WHERE table_name IN ('jobs', 'webhook_outbox')
  AND column_name ILIKE '%api_key%';
```

The expected migration revision is `0003`, and the API-key query must return no
rows.

## When To Repeat Manual Checks

Repeat startup and health checks for every deployment. Repeat the real provider
and webhook checks when changing the upstream harvester version, provider,
model, credentials, networking, proxy, TLS, webhook allowlist, or receiver.
Repeat storage and recovery drills when changing PostgreSQL, Redis,
orchestration, volumes, backup policy, or restart behavior.

Request-validation permutations, batch result projection, retry calculations,
signature algorithms, migration mechanics, and Redis-loss application behavior
do not need to be manually repeated when CI has passed and the deployment does
not alter those components.
