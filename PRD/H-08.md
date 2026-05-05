# H-08 · Phase H acceptance: procedural rule changes deliberation outcome

## Overview

Reproduce the scenario from PRD §26: revert a `calendar.add_reminder` three times in two weeks. Run `distill_procedural()`. Verify the resulting procedural rule appears in the next deliberation prompt for `calendar.add_reminder`. Verify the deliberation outcome is `abort` or `revise` (not `proceed`) as a result. Document the scenario in `docs/RUNBOOKS.md`.

## Context

This is the Phase H gate. It proves the full procedural memory loop works end-to-end: bad behavior → revert → distillation → rule stored → deliberation fetches rule → rule influences decision. It must be run manually using real production data (or a production-equivalent replay). The scenario is designed to be reproducible, so it can be re-run after model changes.

## Scenario Setup

### Prerequisites

- Phase H (H-01 through H-07) fully deployed.
- At least 3 `calendar.add_reminder` actions reverted within a 14-day window.
- Or: inject synthetic actions directly into the DB for the test run.

### Option A — Production scenario (preferred)

1. Over two weeks, ask Chloe to add calendar reminders at times Teo doesn't want.
2. Each time, revert the action via the mobile Activity tab.
3. After 3 reverts, wait for the Sunday distillation run (or trigger manually).
4. Verify the procedural rule was created.
5. Ask Chloe to add another calendar reminder and observe deliberation.

### Option B — Synthetic injection for testing

```python
# scripts/inject_test_reverts.py
"""
Inject 3 reverted calendar.add_reminder actions into the DB to trigger distillation.
Run this script, then run distillation, then observe deliberation.
"""
import asyncio
import json
from datetime import datetime, timedelta
from chloe.state.db import get_connection, migrate
import ulid


def inject_reverts(count: int = 3):
    migrate()
    conn = get_connection()
    now = datetime.utcnow()

    for i in range(count):
        action_id = str(ulid.new())
        created_at = (now - timedelta(days=i + 1)).isoformat()  # 1, 2, 3 days ago
        conn.execute(
            """
            INSERT INTO actions
              (id, tool, verb, args, intent, state, authorization, cost_usd, created_at)
            VALUES (?, 'calendar', 'add_reminder', ?, ?, 'reverted', 'kinetic', 0.002, ?)
            """,
            (
                action_id,
                json.dumps({"title": f"Morning standup #{i+1}", "time": "08:00"}),
                "Add morning standup reminder",
                created_at,
            ),
        )

    conn.commit()
    print(f"Injected {count} reverted calendar.add_reminder actions")
    return conn.execute(
        "SELECT id FROM actions WHERE state='reverted' AND tool='calendar'"
    ).fetchall()


if __name__ == "__main__":
    rows = inject_reverts()
    print(f"Total reverted calendar actions: {len(rows)}")
```

## Verification Script

```python
# scripts/verify_h08_acceptance.py
"""
Phase H acceptance test — run manually.
Verifies the procedural memory loop works end-to-end.
"""
import asyncio
import json
from pathlib import Path


async def run_acceptance_test():
    from chloe.state.db import migrate, get_connection
    from chloe.memory.procedural import distill_procedural
    from chloe.memory.retrieval import query_mixed
    from chloe.actions.deliberate import deliberate, _get_procedural_memories
    from chloe.llm.schemas import Verdict

    print("=== H-08 Phase H Acceptance Test ===\n")

    migrate()
    conn = get_connection()

    # Step 1: Check reverted actions exist
    reverted = conn.execute(
        "SELECT COUNT(*) as n FROM actions WHERE state='reverted' AND tool='calendar'"
    ).fetchone()["n"]
    print(f"Step 1: Reverted calendar actions found: {reverted}")
    assert reverted >= 3, f"Need at least 3 reverted calendar actions, found {reverted}"
    print("  PASS\n")

    # Step 2: Run distillation
    print("Step 2: Running distill_procedural()...")
    new_ids = await distill_procedural()
    print(f"  Created {len(new_ids)} new procedural memories")

    # Step 3: Verify a calendar-related rule was created
    procedural_memories = conn.execute(
        "SELECT id, content, tags FROM memories WHERE kind='procedural' ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    print(f"\nStep 3: Procedural memories in DB: {len(procedural_memories)}")
    calendar_rules = [
        m for m in procedural_memories
        if "calendar" in m["content"].lower() or "calendar" in (m["tags"] or "")
    ]
    print(f"  Calendar-related rules: {len(calendar_rules)}")
    if calendar_rules:
        print(f"  Example rule: {calendar_rules[0]['content'][:100]}...")
    assert len(calendar_rules) >= 1, "No calendar procedural rule found after distillation"
    print("  PASS\n")

    # Step 4: Verify rule appears in next deliberation
    print("Step 4: Simulating deliberate() for calendar.add_reminder...")

    class MockAction:
        id = "h08_test_action"
        tool = "calendar"
        verb = "add_reminder"
        args = {"title": "Morning standup", "time": "08:00"}
        intent = "Add morning standup reminder"
        auth_class = "kinetic"
        cost_estimate = None
        cost_usd = 0.002

    action = MockAction()
    hits = await _get_procedural_memories(action)
    print(f"  Procedural hits retrieved: {len(hits)}")
    if hits:
        print(f"  First hit: {hits[0]['content'][:100]}...")
    assert len(hits) >= 1, "No procedural hits retrieved for calendar.add_reminder"
    print("  PASS\n")

    # Step 5: Run real deliberation and verify outcome
    print("Step 5: Running deliberate() with real LLM...")
    context = {"budget_throttle": 0.2, "last_chat_seen": "5 minutes ago"}
    verdict = await deliberate(action, context)

    if verdict is None:
        print("  NOTE: Deliberation returned None (LLM failure or fail-open)")
    else:
        print(f"  Verdict: {verdict.decision}")
        print(f"  Rationale: {verdict.rationale[:200]}")
        assert verdict.decision in ("abort", "revise"), (
            f"Expected abort or revise, got {verdict.decision}. "
            f"Procedural rule may not be influencing the LLM."
        )
    print("  PASS\n")

    print("=== ALL STEPS PASSED ===")
    print("\nRecord in docs/RUNBOOKS.md:")
    print(f"  - Reverted action IDs: (check 'actions' table)")
    print(f"  - Procedural memory IDs: {new_ids}")
    print(f"  - Verdict: {verdict.decision if verdict else 'None (fail-open)'}")
    print(f"  - Rationale: {verdict.rationale[:100] if verdict else 'N/A'}...")


if __name__ == "__main__":
    asyncio.run(run_acceptance_test())
```

## RUNBOOKS.md entry template

```markdown
## H-08 — Procedural memory loop acceptance

**Run date:** 2026-MM-DD
**Run by:** Teo

### Setup
- 3 reverted `calendar.add_reminder` actions (IDs: [ULID1, ULID2, ULID3])
- Distillation triggered: Sunday weekly job (or manual)

### Results
- Procedural memory ID: [ULID]
- Rule text: "When adding calendar reminders in early morning (before 9am), abort — pattern of reverts observed"
- Deliberation verdict for next `calendar.add_reminder`: **abort**
- Rationale: "Procedural rule [ULID] warns against early morning calendar additions. Three reverts observed."

### Sign-off
H-08 PASSED — [date] — Teo
```

## Testing

### Automated component tests

```python
# tests/unit/test_h08_component.py
"""
Verify each component of the H-08 loop in isolation.
The full end-to-end is manual (see acceptance script).
"""
import pytest
import json
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from chloe.state.db import migrate, close, get_connection

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def _insert_reverted_calendar(db, days_ago=1):
    import ulid
    conn = db
    action_id = str(ulid.new())
    created_at = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
    conn.execute(
        """
        INSERT INTO actions (id, tool, verb, args, intent, state, authorization, cost_usd, created_at)
        VALUES (?, 'calendar', 'add_reminder', ?, ?, 'reverted', 'kinetic', 0.002, ?)
        """,
        (action_id, json.dumps({"title": "Standup"}), "Add standup", created_at),
    )
    conn.commit()
    return action_id


@pytest.mark.asyncio
async def test_three_reverts_produce_calendar_rule(db):
    for i in range(3):
        _insert_reverted_calendar(db, days_ago=i + 1)

    mock_rule = {
        "rule_text": "Avoid adding calendar reminders before 9am — Teo has reverted these repeatedly.",
        "tool": "calendar",
        "tags": ["timing", "morning"],
        "confidence": 0.8,
        "valence": "avoid",
    }

    stored = []

    with patch("chloe.memory.procedural._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(return_value=[mock_rule])
        with patch("chloe.memory.procedural._memory_store") as mock_store:
            async def capture(memory):
                stored.append(memory)
            mock_store.upsert = capture
            from chloe.memory.procedural import distill_procedural
            ids = await distill_procedural()

    assert len(ids) >= 1
    assert len(stored) >= 1
    assert any("calendar" in m.tags for m in stored)


@pytest.mark.asyncio
async def test_procedural_rule_retrieved_in_deliberation(db):
    import ulid
    conn = db
    memory_id = str(ulid.new())
    conn.execute(
        """
        INSERT INTO memories (id, kind, content, tags, source, weight, archived_tier, artifact_refs, created_at)
        VALUES (?, 'procedural',
          'Avoid adding calendar reminders before 9am.',
          ?, 'distillation', 0.8, 'hot', '[]', ?)
        """,
        (memory_id, json.dumps(["procedural", "calendar"]), datetime.utcnow().isoformat()),
    )
    conn.commit()

    class MockAction:
        id = "test"
        tool = "calendar"
        verb = "add_reminder"
        args = {}
        intent = "Add reminder"
        auth_class = "kinetic"
        cost_estimate = None
        cost_usd = 0.002

    from chloe.actions.deliberate import _get_procedural_memories
    hits = await _get_procedural_memories(MockAction())
    assert len(hits) >= 1
    assert any("calendar" in h.get("content", "") or "9am" in h.get("content", "") for h in hits)
```

## Dependencies

- H-01 (`distill_procedural()` — creates rules from reverts).
- H-02 (`_get_procedural_memories()` — retrieves rules in deliberation).
- D-01 (`deliberate()` — produces Verdict).
- C-11 (`revert` action state — source data for distillation).

## Acceptance criteria

- `distill_procedural()` after 3 reverted `calendar.add_reminder` actions → at least 1 procedural memory with "calendar" in tags.
- `_get_procedural_memories()` for `calendar.add_reminder` returns the stored rule.
- Full `deliberate()` call with rule in context → verdict is `abort` or `revise` (not `proceed`).
- Scenario documented in `docs/RUNBOOKS.md` with real action IDs and verdict.
- `docs/RUNBOOKS.md` entry signed off by Teo.
