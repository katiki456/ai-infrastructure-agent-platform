# Production risks

This register distinguishes controls that exist in the repository from work that is recommended but
not implemented. It combines the original staff-level production review with the repository-wide
pre-commit security review.

## Status definitions

- **Implemented:** The control exists and has automated validation.
- **Partially implemented:** A control exists, but a documented gap remains.
- **Recommended / not implemented:** The current source does not provide the control.

## Implemented controls

| Control | Status | Evidence |
|---|---|---|
| JWT authentication and active-user lookup | Implemented | `platform_app/auth.py`, auth/chat dependencies |
| Argon2 password hashing | Implemented | `PasswordHash.recommended()` |
| Distributed Redis rate limits | Partially implemented | Registration/login by IP and chat by user; 30/60 seconds; Redis errors fail open |
| Bounded agent turns and duration | Implemented | Six turns and 90 seconds; safe timeout SSE error |
| Versioned/model/user/prompt cache identity | Implemented | `platform_app/routes/chat.py` |
| Exact cached-text replay | Implemented | Whitespace-preserving single SSE delta |
| Failed/incomplete response cache exclusion | Implemented | Writes occur only after normal nonempty completion |
| Input/output scope guardrails | Partially implemented | Keyword-based; output validation occurs after streamed deltas |
| Read-only mock tools | Implemented | No network, process, filesystem, or infrastructure mutation |
| Prometheus and OpenTelemetry instrumentation | Partially implemented | Core counters/spans exist; correlation/cost/audit fields are missing |
| API pod runtime hardening | Implemented | Non-root, RuntimeDefault seccomp, read-only root, no privilege escalation, capabilities dropped |

## Risk summary

| Priority | Risk | Status |
|---|---|---|
| Critical | Tool authorization, tenant, role, resource, and side-effect enforcement | Recommended / not implemented |
| High | Prompt injection and tool misuse as integrations become real | Recommended / not implemented |
| High | Self-registration multiplies per-user AI quotas and cost | Recommended / not implemented |
| High | PostgreSQL/HPA connection scaling and migration coordination | Recommended / not implemented |
| High | Kubernetes production controls | Recommended / not implemented |
| High | Production secret management | Partially implemented |
| Medium | Redis limiter fails open | Partially implemented |
| Medium | Redis authentication, isolation, and persistence | Recommended / not implemented |
| Medium | Trusted-proxy/client-IP integrity | Recommended / not implemented |
| Medium | Email ownership verification | Recommended / not implemented |
| Medium | Correlation, provider IDs, structured tool outcomes, token/cost metrics, and audit trail | Recommended / not implemented |
| Medium | Registration race handling | Recommended / not implemented |

## Critical

### Tool authorization, tenant, role, and side-effect enforcement

**Description:** Function tools receive model-generated arguments but no authenticated user context,
tenant, role, resource scope, approval state, or side-effect policy. Today they return fixed mock data
and perform no mutation.

**Impact:** Replacing mocks with Kubernetes, Prometheus, cloud, ticketing, or incident clients without
an independent authorization layer can create cross-tenant data access or production changes based
on prompt-controlled decisions.

**Current state:** **Recommended / not implemented.** Authentication occurs at the route, but user
identity is not propagated into the Agents SDK run or tools. There is no role/tenant model.

**Recommended remediation:** Define a typed run context containing user, tenant, roles, allowed
clusters/namespaces/services, and approval state. Enforce authorization in each tool before reading or
mutating a resource. Separate read and write tools, require explicit approvals for side effects, and
record immutable audit events.

## High

### Prompt injection and tool misuse

**Description:** `classify_request()` creates a routing hint, but all handoffs remain available to the
model. Input/output guardrails are keyword-based. Final-output guardrails may run after SSE text has
already been released.

**Impact:** Current impact is limited to misrouting, misleading mock output, and extra model use. The
impact becomes high when tools reach real infrastructure or tenant data.

**Current state:** **Partially implemented.** System instructions, deterministic hints, narrow mock
tools, turn/time bounds, and guardrails exist. They are not a hard policy boundary.

**Recommended remediation:** Enforce allowed specialist/handoff in application code, structurally
separate untrusted text from policy, validate tool arguments, add chunk-level or pre-release output
controls where needed, and require tool-boundary authorization.

### Self-registration multiplies AI quotas

**Description:** Any client can create active users and obtain JWTs. Chat limits are keyed by user ID,
so new accounts create new quotas.

**Impact:** An attacker can multiply OpenAI calls, concurrent SSE streams, token consumption, and
Redis cache cardinality. The default registration limit allows up to 30 new identities per trusted IP
window, each with a separate chat allowance.

**Current state:** **Recommended / not implemented.** Per-IP registration and per-user chat limits,
prompt length, six turns, and a 90-second timeout limit individual operations but not aggregate spend.

**Recommended remediation:** Require verified/invited accounts, add tenant/deployment/global
concurrency and token budgets, enforce provider spend limits, and add a circuit breaker.

### PostgreSQL and HPA connection scaling

**Description:** Each API process may retain 10 database connections and burst to 30. The HPA allows
10 replicas. Every API container also runs Alembic before startup.

**Impact:** Ten replicas can request up to 300 connections, exceed a common database budget, create
connection storms, and race migrations during starts or rollouts.

**Current state:** **Recommended / not implemented.** Pool pre-ping and a PostgreSQL readiness probe
exist. No connection proxy, replica-aware pool budget, or singleton migration Job exists.

**Recommended remediation:** Establish a database-wide connection budget, reduce per-pod pools,
introduce PgBouncer or a managed proxy, run migrations as a dedicated release Job, and add rollout
tests for concurrent startup.

### Kubernetes production controls

**Description:** The base uses mutable image tags, lacks an Ingress/TLS definition, PodDisruptionBudget,
NetworkPolicy, explicit ServiceAccount/RBAC, topology spread/anti-affinity, and hardened PostgreSQL/
Redis container contexts. Redis uses `emptyDir`.

**Impact:** Deployments can consume unintended images, experience avoidable disruption, permit
unnecessary east-west access, lose Redis state after rescheduling, and expose services without an
explicit TLS boundary.

**Current state:** **Partially implemented.** API probes, resources, HPA, non-root and seccomp controls,
PostgreSQL PVC, and namespace isolation exist.

**Recommended remediation:** Deploy immutable digests, add PDB/topology controls, default-deny
NetworkPolicies, TLS ingress/gateway, explicit ServiceAccounts/RBAC, persistent or managed Redis,
backup/restore testing, and hardened data-service security contexts.

### Secrets management

**Description:** The repository provides safe placeholder examples, but production has no External
Secrets/Sealed Secrets/workload identity integration or rotation process. Compose uses known local
database credentials. Current startup checks do not reject every documented placeholder.

**Impact:** Operators can deploy known values, credentials can be copied into plaintext manifests,
and rotation remains manual.

**Current state:** **Partially implemented.** `.env`, local Secret manifests, kubeconfigs, keys, and
certificates are excluded from Git/Docker contexts. Documentation now forbids editing the tracked
Secret example. Real secret-manager integration is not implemented.

**Recommended remediation:** Integrate an approved external secret manager, use workload identity,
reject every example/default in production startup validation, define rotation and revocation, and
prefer short-lived credentials.

## Medium

### Redis limiter fail-open behavior

**Description:** `RedisError` causes the limiter to allow registration, login, and chat requests.

**Impact:** During Redis failure, attackers can bypass login/account/model-call quotas. Readiness may
eventually remove a Kubernetes pod but does not eliminate the window or protect direct/Compose runs.

**Current state:** **Partially implemented.** Errors are logged and Redis/cache error metrics exist.

**Recommended remediation:** Fail closed for auth and paid model scopes or use a conservative bounded
process-local fallback plus a provider-call circuit breaker.

### Redis authentication, isolation, and persistence

**Description:** Kubernetes Redis has no ACL, password, TLS, or NetworkPolicy. AOF writes to
`emptyDir`, so state is lost when the pod is replaced.

**Impact:** An adjacent compromised workload can read/poison cached responses and alter rate-limit
counters. Pod replacement resets cached data and limiter windows.

**Current state:** **Recommended / not implemented.** ClusterIP limits direct internet exposure; Docker
Compose uses a persistent named volume.

**Recommended remediation:** Use managed Redis or ACL/TLS credentials, default-deny NetworkPolicy,
persistent storage when limiter continuity matters, and separate security state from response cache.

### Trusted-proxy and client-IP integrity

**Description:** `main.py` trusts forwarding headers from every peer while auth limits use the derived
client address.

**Impact:** Clients reaching the direct runner can rotate forged forwarding values and bypass auth
rate-limit buckets.

**Current state:** **Recommended / not implemented.** The container entrypoint does not explicitly use
the wildcard setting; chat uses user ID rather than source IP.

**Recommended remediation:** Configure exact proxy CIDRs, disable proxy headers for direct serving,
prevent ingress bypass, and enforce auth throttling at a trusted edge.

### Email ownership verification

**Description:** Registration activates accounts immediately without proving control of the supplied
email address.

**Impact:** Identity squatting can block the legitimate owner. Impact grows if email later controls
organization membership, recovery, billing, or preexisting records.

**Current state:** **Recommended / not implemented.** Email syntax, normalization, uniqueness, password
hashing, and JWT validation are implemented.

**Recommended remediation:** Add pending accounts, expiring verification, delayed token issuance,
generic anti-enumeration responses, and recovery for pre-registered addresses.

### Correlation IDs and OpenAI request IDs

**Description:** HTTP, agent, Redis, database, and provider events are not tied together by one
request/correlation identifier. OpenAI provider request IDs are not persisted or surfaced internally.

**Impact:** Incident diagnosis and provider support investigations require manual timing-based
correlation.

**Current state:** **Recommended / not implemented.** FastAPI and agent spans exist.

**Recommended remediation:** Generate/accept a validated request ID, put it in response headers,
structured logs and spans, and capture provider request IDs without exposing secrets.

### Structured tool outcomes

**Description:** Tool metrics count calls, while SSE reports called/completed. There is no structured
success/failure/duration/result-size record.

**Impact:** Operators cannot distinguish tool failures, latency, retries, or evidence quality reliably.

**Current state:** **Recommended / not implemented.** Tool call counters and Agents traces exist.

**Recommended remediation:** Wrap tools with structured outcome telemetry and low-cardinality labels;
record duration, outcome, retry count, and sanitized resource scope.

### Token and cost metrics

**Description:** The service does not record input/output tokens, model cost, budget consumption, or
per-tenant/provider limits.

**Impact:** Cost regressions and abuse may be detected only through external billing.

**Current state:** **Recommended / not implemented.** Request, latency, cache, handoff, tool, and
guardrail metrics exist.

**Recommended remediation:** Capture usage from model results/traces, aggregate by model and approved
tenant identifiers, define alerts and budgets, and avoid high-cardinality user labels.

### Agent-action audit trail

**Description:** Handoffs and tools are observable in transient traces/metrics but not in a durable,
queryable audit record tied to identity and authorization.

**Impact:** Real infrastructure integrations would lack forensic evidence for who requested what,
which policy allowed it, which tool ran, and what resource was affected.

**Current state:** **Recommended / not implemented.** Agents traces and SSE progress events exist.

**Recommended remediation:** Persist append-only sanitized audit events containing request ID, user,
tenant, policy decision, agent, tool, resource scope, approval, outcome, and trace identifiers.

### Registration race handling

**Description:** Registration checks for an existing email before insert. Concurrent requests can both
pass the check; the database unique constraint rejects one, but the route does not translate
`IntegrityError` into HTTP 409.

**Impact:** A normal race can return a 500 and leave the session requiring rollback, reducing API
correctness and observability.

**Current state:** **Partially implemented.** The database unique constraint prevents duplicate rows.

**Recommended remediation:** Treat the insert/unique constraint as authoritative, catch
`IntegrityError`, rollback, and return a consistent conflict/generic registration response.

## Additional lower-priority concerns

- `/metrics` is unauthenticated on the shared API listener. It exposes aggregate operational data,
  not prompts or secrets. Restrict it to monitoring networks or credentials.
- Final-output guardrails can trip after SSE deltas have been delivered. Buffer or validate chunks if
  guardrails must prevent disclosure.
- GitHub Actions use mutable major-version tags. Pin reviewed commit SHAs and scope permissions by job.
- Base images and Kubernetes images use mutable tags. Prefer digests in production.
