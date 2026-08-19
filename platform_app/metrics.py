from prometheus_client import Counter, Histogram

CHAT_REQUESTS = Counter(
    "ai_chat_requests_total",
    "Total authenticated AI chat requests.",
    ("status",),
)
CHAT_LATENCY = Histogram(
    "ai_chat_request_duration_seconds",
    "AI chat request duration in seconds.",
)
CACHE_HITS = Counter("ai_cache_hits_total", "AI response cache hits.")
CACHE_MISSES = Counter("ai_cache_misses_total", "AI response cache misses.")
TOOL_CALLS = Counter(
    "ai_agent_tool_calls_total",
    "Agents SDK function tool calls.",
    ("tool",),
)
HANDOFFS = Counter(
    "ai_agent_handoffs_total",
    "Agents SDK specialist handoffs.",
    ("from_agent", "to_agent"),
)
GUARDRAIL_TRIPS = Counter(
    "ai_agent_guardrail_trips_total",
    "Agents SDK guardrail tripwire activations.",
    ("guardrail",),
)
RATE_LIMIT_EXCEEDED = Counter(
    "ai_rate_limit_exceeded_total",
    "Requests rejected by the distributed rate limiter.",
    ("scope",),
)
CACHE_ERRORS = Counter(
    "ai_cache_errors_total",
    "Redis cache errors handled without failing the AI request.",
    ("operation",),
)
