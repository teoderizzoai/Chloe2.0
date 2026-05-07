from prometheus_client import Counter, Gauge, Histogram
from fastapi import APIRouter
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response

chloe_actions_total = Counter(
    "chloe_actions_total",
    "Total actions submitted to the gate",
    labelnames=["tool", "verb", "state"],
)

chloe_actions_held_back_total = Counter(
    "chloe_actions_held_back_total",
    "Total actions held back (suppressed, self_aborted, denied)",
    labelnames=["reason"],
)

chloe_llm_calls_total = Counter(
    "chloe_llm_calls_total",
    "Total LLM API calls made",
    labelnames=["model"],
)

chloe_llm_tokens_total = Counter(
    "chloe_llm_tokens_total",
    "Total tokens consumed",
    labelnames=["model", "kind"],
)

chloe_llm_usd_total = Counter(
    "chloe_llm_usd_total",
    "Total USD spent on LLM calls",
    labelnames=["model"],
)

chloe_affect_valence = Gauge(
    "chloe_affect_valence",
    "Current affect valence (-1 to 1)",
)

chloe_affect_arousal = Gauge(
    "chloe_affect_arousal",
    "Current affect arousal (0 to 1)",
)

chloe_pending_confirmations = Gauge(
    "chloe_pending_confirmations",
    "Number of confirmation tickets currently awaiting response",
)

chloe_chroma_size = Gauge(
    "chloe_chroma_size",
    "Number of documents in the Chroma memories collection",
)

chloe_kv_age_seconds = Gauge(
    "chloe_kv_age_seconds",
    "Seconds since the oldest kv entry was last updated",
)

chloe_budget_usd_today = Gauge(
    "chloe_budget_usd_today",
    "USD spent today",
)

chloe_llm_errors_total = Counter(
    "chloe_llm_errors_total",
    "LLM call failures after retries",
    labelnames=["call_type"],
)

chloe_chat_turns_total = Counter(
    "chloe_chat_turns_total",
    "Total chat turns processed",
)

chloe_voice_latency_seconds = Histogram(
    "chloe_voice_latency_seconds",
    "Time to first audio byte in voice pipeline",
    buckets=[0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0],
)

chloe_memory_writes_total = Counter(
    "chloe_memory_writes_total",
    "Memory upsert operations",
    labelnames=["kind"],
)

chloe_initiative_ticks_total = Counter(
    "chloe_initiative_ticks_total",
    "Initiative engine tick outcomes",
    labelnames=["outcome"],
)

chloe_db_migration_failures_total = Counter(
    "chloe_db_migration_failures_total",
    "Database migration failures",
)

deliberation_calls_total = Counter(
    "chloe_deliberation_calls_total",
    "Number of deliberation LLM calls",
    labelnames=["model"],
)

metrics_router = APIRouter()


@metrics_router.get("/metrics")
async def metrics_endpoint():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def record_action(tool: str, verb: str, state: str) -> None:
    chloe_actions_total.labels(tool=tool, verb=verb, state=state).inc()


def record_held_back(reason: str) -> None:
    chloe_actions_held_back_total.labels(reason=reason).inc()


def record_llm_call(
    model: str,
    input_tokens: int,
    output_tokens: int,
    thinking_tokens: int,
    usd: float,
) -> None:
    chloe_llm_calls_total.labels(model=model).inc()
    chloe_llm_tokens_total.labels(model=model, kind="input").inc(input_tokens)
    chloe_llm_tokens_total.labels(model=model, kind="output").inc(output_tokens)
    chloe_llm_tokens_total.labels(model=model, kind="thinking").inc(thinking_tokens)
    chloe_llm_usd_total.labels(model=model).inc(usd)


def set_affect(valence: float, arousal: float) -> None:
    chloe_affect_valence.set(valence)
    chloe_affect_arousal.set(arousal)


def set_pending_confirmations(n: int) -> None:
    chloe_pending_confirmations.set(n)


def set_chroma_size(n: int) -> None:
    chloe_chroma_size.set(n)
