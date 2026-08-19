# Troubleshooting

Start with the liveness and readiness endpoints, then inspect only the service involved. Avoid
printing environment variables: they may contain the OpenAI key, JWT signing material, or database
credentials.

## Configuration failures

### The API exits during startup

Typical causes are missing `OPENAI_API_KEY`, missing `JWT_SECRET_KEY`, or an invalid production JWT
secret. Copy `.env.example` to `.env` for local use and fill in secrets there. `.env` is intentionally
ignored by Git and Docker build context.

In `ENVIRONMENT=production`, the JWT secret must be non-placeholder and at least 32 characters.
Use a cryptographically random value; do not reuse the example text.

Validate Compose interpolation without showing secret values:

```bash
docker compose config --quiet
```

## Health checks

```bash
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
```

- `/health/live` confirms the API process is serving requests.
- `/health/ready` checks PostgreSQL and Redis connectivity. A 503 response identifies a dependency
  that is not ready.

With Compose:

```bash
docker compose ps
docker compose exec postgres pg_isready -U platform -d platform
docker compose exec redis redis-cli ping
```

## Database and migrations

The container applies Alembic migrations before Uvicorn starts. If the API repeatedly restarts:

```bash
docker compose logs --tail=200 api postgres
docker compose run --rm api alembic current
docker compose run --rm api alembic upgrade head
```

Check that `DATABASE_URL` uses the `postgresql+asyncpg://` driver and that the hostname is `postgres`
inside Compose or Kubernetes. `localhost` from the API container points to the API container itself.

Registration conflicts return HTTP 409 when an email is already present. Concurrent registration of
the same email is a documented race-handling gap; the database uniqueness constraint still prevents
duplicate rows.

## Redis, cache, and rate limiting

`redis-cli ping` should return `PONG`. Redis failures intentionally fail open: the API continues, but
response caching is bypassed and distributed rate limits are not enforced. Cache failures increment
`ai_cache_errors_total`; rate-limiter backend failures are currently visible in server logs but do
not have a dedicated metric. Restore Redis promptly.

A cache miss after changing a model or workflow version is expected. Cache identity includes:

- cache workflow version;
- model identity;
- authenticated user ID;
- the exact prompt bytes.

Whitespace changes therefore produce a different key. Only complete, nonempty, successful responses
are cached; guardrail, timeout, and provider-error responses are excluded.

HTTP 429 means the configured Redis rate limit was exceeded. The response contains `Retry-After`.
Defaults are 30 registration/login requests per source IP or 30 chat requests per user in 60 seconds.

## Authentication

Use `/api/v1/auth/register` to create a user, `/api/v1/auth/token` to obtain a bearer token, and
`/api/v1/auth/me` to confirm it. Common failures:

- 401: missing, expired, malformed, or invalidly signed token;
- 403: authenticated user is inactive;
- 409: the registration email already exists;
- 422: request validation failed, such as a password shorter than eight characters.

Do not put bearer tokens in URLs or logs. Send them only in the `Authorization: Bearer ...` header.

## Streaming chat

The chat endpoint returns `text/event-stream`. Use a client that disables response buffering:

```bash
curl --no-buffer \
  -H "Authorization: Bearer <access-token>" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Why are my Kubernetes pods failing?"}' \
  http://127.0.0.1:8000/api/v1/chat/stream
```

Normal events include `agent`, `handoff`, `tool`, one or more `delta` events, and `done`. A Redis
cache hit replays the exact final text in one `delta` followed by `done`.

If deltas arrive all at once, check buffering in the client, reverse proxy, ingress, or load balancer.
The application sends SSE-compatible headers, but every intermediate proxy must also permit
streaming.

An `error` event can represent an input guardrail, provider failure, turn limit, or 90-second timeout.
The connection then closes. Partial/error responses are not cached. OpenAI provider details are kept
out of client responses; inspect authorized traces and server logs for diagnosis.

## Agent routing and tools

Routing is model-selected. A deterministic classifier adds a hint for Kubernetes, GPU, incident, or
ambiguous infrastructure requests, while the TriageAgent remains responsible for the final handoff.
Specialist tools are static mock functions, so their output does not reflect a real cluster.

For a Kubernetes pod-failure request, expect progress events naming `TriageAgent`, a handoff to
`KubernetesAgent`, and a call to `get_pod_failures`. If routing tests fail, run:

```bash
pytest -q tests/test_multi_agent.py
```

The tests use a fake model and do not require live provider access.

## Metrics and tracing

Prometheus metrics are exposed at `/metrics`. Useful signals include chat outcomes and latency,
cache hits/misses/errors, rate-limit rejections, handoffs, tool calls, and guardrail trips. Timeout
and provider outcomes appear as labels on the chat request counter, and total request duration is
recorded by the chat latency histogram. Rate-limiter backend failures currently require log-based
alerting.

OpenTelemetry export is optional. Set `OTEL_EXPORTER_OTLP_ENDPOINT` to a reachable OTLP endpoint. An
empty value keeps local instrumentation without exporting spans. The Agents SDK also emits its own
trace for each workflow. End-to-end correlation IDs, OpenAI request IDs, token/cost metrics, and a
durable agent-action audit trail remain documented production gaps.

## Docker Compose

```bash
docker compose up --build -d
docker compose ps
docker compose logs --tail=200 api postgres redis
docker compose down
```

Compose requires the Docker daemon and both required secrets in the shell or local `.env`. Database
and Redis ports are not published to the host; inspect them through `docker compose exec`.

Tests are intentionally absent from the production image because `.dockerignore` excludes `tests/`.
Run the full suite in the development or CI environment.

## Kubernetes

Render manifests before applying them:

```bash
kubectl kustomize k8s >/dev/null
```

Inspect failed rollouts with:

```bash
kubectl -n ai-platform get pods,services,hpa
kubectl -n ai-platform describe pod <pod-name>
kubectl -n ai-platform logs <pod-name> --previous
kubectl -n ai-platform rollout status deployment/ai-platform-api --timeout=180s
```

Do not apply `k8s/secret.example.yaml` unchanged. See [`KUBERNETES.md`](KUBERNETES.md) for the current
deployment model and its production gaps.

## Escalation checklist

Before escalating an incident, capture:

- UTC time window and environment;
- HTTP status or SSE event type;
- API pod/container identity;
- agent, handoff, and tool names—but not tool arguments containing sensitive data;
- relevant Prometheus series and trace ID if available;
- dependency readiness for PostgreSQL and Redis;
- whether the request was a cache hit;
- sanitized logs with credentials and bearer tokens removed.

See [`SECURITY.md`](SECURITY.md) before sharing diagnostics outside the operating team.
