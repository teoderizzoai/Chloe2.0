from datetime import datetime, timezone, timedelta

from chloe.state.db import get_connection
from chloe.actions.schema import Action

PRICE_PER_1M_TOKENS = {
    "gemini-2.5-pro":   {"input": 3.50, "output": 10.50, "thinking": 3.50},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60,  "thinking": 0.10},
}


def price_usd(model: str, input_tokens: int, output_tokens: int, thinking_tokens: int = 0) -> float:
    rates = PRICE_PER_1M_TOKENS.get(model, {"input": 1.0, "output": 1.0, "thinking": 1.0})
    return (
        input_tokens    * rates["input"]    / 1_000_000 +
        output_tokens   * rates["output"]   / 1_000_000 +
        thinking_tokens * rates["thinking"] / 1_000_000
    )


def _get_cap() -> float:
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM preferences WHERE key = 'spending_cap_usd_day'"
    ).fetchone()
    if row:
        return float(row["value"])
    return 1.50


def charge(model: str, usage: dict) -> None:
    usd = price_usd(
        model,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        usage.get("thinking_tokens", 0),
    )
    tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

    conn = get_connection()
    conn.execute(
        "UPDATE budgets SET usd = usd + ?, tokens = tokens + ?"
        " WHERE window IN ('today', 'this_hour', 'this_week')",
        (usd, tokens),
    )
    conn.commit()

    from chloe.observability.metrics import record_llm_call
    record_llm_call(
        model,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        usage.get("thinking_tokens", 0),
        usd,
    )


def exceeded_for(action: Action) -> bool:
    cap = _get_cap()
    conn = get_connection()
    row = conn.execute("SELECT usd FROM budgets WHERE window = 'today'").fetchone()
    if not row:
        return False
    return row["usd"] >= cap


def throttle_level() -> float:
    cap = _get_cap()
    if cap <= 0:
        return 1.0
    conn = get_connection()
    row = conn.execute("SELECT usd FROM budgets WHERE window = 'today'").fetchone()
    if not row:
        return 0.0
    return min(row["usd"] / cap, 1.0)


def reset_windows() -> None:
    now = datetime.now(timezone.utc)
    conn = get_connection()
    rows = conn.execute("SELECT window, reset_at FROM budgets").fetchall()

    for row in rows:
        reset_at = datetime.fromisoformat(row["reset_at"]).replace(tzinfo=timezone.utc)
        if now >= reset_at:
            if row["window"] == "today":
                next_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            elif row["window"] == "this_hour":
                next_reset = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            else:  # this_week
                days_until_monday = (7 - now.weekday()) % 7 or 7
                next_reset = (now + timedelta(days=days_until_monday)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
            conn.execute(
                "UPDATE budgets SET usd = 0, tokens = 0, reset_at = ? WHERE window = ?",
                (next_reset.isoformat(), row["window"]),
            )
    conn.commit()
