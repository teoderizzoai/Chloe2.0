# Agent Teams — Master Reference Guide

Source: https://code.claude.com/docs/en/agent-teams  
Requires: Claude Code v2.1.32+, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`

---

## What Agent Teams Are

One Claude Code session acts as **team lead** — it creates tasks, spawns teammates, and synthesizes results. Each **teammate** is a fully independent Claude Code session with its own context window. Teammates share a task list and can message each other directly, without going through the lead.

This is the key architectural difference from subagents:

| | Subagents | Agent Teams |
|---|---|---|
| Context | Own window; results return to caller | Own window; fully independent |
| Communication | Report to main agent only | Message each other directly |
| Coordination | Main agent manages all work | Shared task list, self-coordinating |
| Best for | Focused tasks, result matters | Complex work needing discussion |
| Token cost | Lower | Higher (each teammate = full instance) |

**Rule of thumb**: use subagents when only the result matters. Use agent teams when teammates need to share findings, challenge each other, or coordinate on their own.

---

## When to Use Agent Teams

Strong use cases (parallel exploration adds real value):

- **Parallel research/review** — multiple teammates investigate different aspects simultaneously, challenge each other's findings
- **New independent modules** — each teammate owns a separate piece with no file overlap
- **Competing hypotheses debugging** — teammates test different theories in parallel, converge faster
- **Cross-layer changes** — frontend, backend, tests each owned by a different teammate

Avoid agent teams for:
- Sequential tasks (dependencies between steps)
- Same-file edits (leads to overwrites)
- Tasks with many cross-dependencies
- Simple/routine work (single session is more cost-effective)

---

## Enabling Agent Teams

In `settings.json` (preferred) or shell environment:

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  }
}
```

---

## Starting a Team

Tell Claude in natural language. Be explicit about the team structure you want:

```
Create an agent team with 3 teammates to review PR #142:
- One focused on security
- One on performance
- One on test coverage
```

Claude creates the team, spawns teammates, assigns work, and synthesizes results. You can also let Claude propose a team structure — it will ask for confirmation before spawning.

---

## Display Modes

| Mode | How it works | When to use |
|---|---|---|
| `in-process` | All teammates in your terminal; Shift+Down cycles through them | Default, any terminal |
| `tmux` / iTerm2 | Each teammate in its own split pane | When you want to see all output at once |

Default is `"auto"` — uses split panes if already inside tmux, in-process otherwise.

Override in `~/.claude/settings.json`:
```json
{ "teammateMode": "in-process" }
```

Or per-session: `claude --teammate-mode in-process`

**In-process navigation:**
- `Shift+Down` — cycle through teammates (wraps back to lead after last)
- `Enter` — view a teammate's session
- `Escape` — interrupt their current turn
- `Ctrl+T` — toggle task list

---

## Architecture Internals

```
Team Lead
  ├── Creates/assigns tasks
  ├── Spawns/shuts down teammates
  ├── Receives idle notifications automatically
  └── Synthesizes results

Teammates (each a full Claude Code session)
  ├── Own context window (no lead history inherited)
  ├── Load CLAUDE.md + MCP servers + skills from project
  ├── Can message any other teammate by name
  └── Self-claim tasks from shared task list

Shared task list
  ├── States: pending → in_progress → completed
  ├── Dependencies: blocked tasks unlock automatically when dependencies complete
  └── File locking prevents race conditions on simultaneous claims

Storage (local):
  ~/.claude/teams/{team-name}/config.json   ← runtime state, never hand-edit
  ~/.claude/tasks/{team-name}/              ← task list
```

**Important**: the team config is overwritten on every state update. Don't pre-author or edit it manually. Use subagent definitions for reusable roles.

---

## Controlling the Team

### Specify teammates and models

```
Create a team with 4 teammates to refactor these modules in parallel.
Use Sonnet for each teammate.
```

Teammates don't inherit the lead's `/model` by default. Set **Default teammate model** in `/config` to have them follow the lead.

### Require plan approval before implementation

```
Spawn an architect teammate to refactor the auth module.
Require plan approval before they make any changes.
```

Teammate works in read-only plan mode → submits plan to lead → lead approves or rejects with feedback → teammate implements.

Influence lead's judgment in the prompt: `"only approve plans that include test coverage"`.

### Assign tasks

- **Lead assigns explicitly**: tell the lead which task goes to which teammate
- **Self-claim**: teammate picks up the next unassigned, unblocked task after finishing

### Talk directly to a teammate

In-process: `Shift+Down` to reach the teammate, then type. Split panes: click into the pane.

### Shut down a teammate

```
Ask the researcher teammate to shut down
```

The teammate can approve (graceful exit) or reject with an explanation.

### Clean up the team

```
Clean up the team
```

Always run cleanup from the **lead**. Shut down all teammates first — cleanup fails if any are still running.

---

## Subagent Definitions as Teammate Roles

Define a role once in a `.claude/agents/` file (project, user, or plugin scope), then reference it when spawning:

```
Spawn a teammate using the security-reviewer agent type to audit the auth module.
```

The teammate honors the definition's `tools` allowlist and `model`. Team tools (`SendMessage`, task management) are always available even when `tools` restricts others.

**Note**: `skills` and `mcpServers` frontmatter in subagent definitions are NOT applied when the definition runs as a teammate — those load from project/user settings instead.

---

## Quality Gates via Hooks

Enforce rules on the team's lifecycle:

| Hook | Trigger | Exit 2 effect |
|---|---|---|
| `TeammateIdle` | Teammate is about to go idle | Send feedback, keep teammate working |
| `TaskCreated` | Task is being created | Prevent creation, send feedback |
| `TaskCompleted` | Task being marked complete | Prevent completion, send feedback |

---

## Best Practices

### Give teammates explicit context in the spawn prompt

Teammates don't inherit the lead's conversation history. Put task-specific details in the spawn prompt:

```
Spawn a security reviewer teammate with the prompt:
"Review src/auth/ for vulnerabilities. Focus on token handling,
session management, and input validation. The app uses JWT tokens
in httpOnly cookies. Rate issues by severity."
```

### Team size: 3–5 teammates is the sweet spot

- Token costs scale linearly with teammates
- Coordination overhead grows with team size
- 3–5 teammates balances parallel work with manageable coordination
- Target 5–6 tasks per teammate to keep everyone productive

Scale up only when the work genuinely benefits from simultaneous parallel work.

### Size tasks correctly

- **Too small** — coordination overhead exceeds the benefit
- **Too large** — long periods without check-ins, risk of wasted effort
- **Right** — self-contained unit with a clear deliverable (a function, a test file, a review finding)

If the lead isn't creating enough tasks, ask it explicitly to split the work into smaller pieces.

### Avoid file conflicts

Two teammates editing the same file causes overwrites. Structure work so each teammate owns a distinct set of files.

### Keep the lead from jumping in

If the lead starts implementing instead of waiting for teammates:

```
Wait for your teammates to complete their tasks before proceeding
```

### Monitor and steer

Check in on progress, redirect teammates that are off-track, and synthesize findings as they come in. Don't let the team run unattended for long.

### Start with research/review tasks

If new to agent teams, start with tasks that have clear boundaries and don't require writing code. Research, PR review, and bug investigation show the value of parallel exploration without implementation coordination challenges.

---

## Token Cost Guidance

Each teammate is a full Claude instance — token usage scales linearly with active teammates. Agent teams are cost-effective for:
- Research and review (parallelism adds speed and quality)
- New independent features (genuine parallel work)
- Competing hypothesis debugging (debate structure gives better results)

Agent teams are wasteful for:
- Sequential tasks (teammates idle while waiting)
- Routine/simple tasks (single session suffices)

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Teammates not appearing | Press `Shift+Down` — they may be running but not visible |
| Too many permission prompts | Pre-approve common operations in permission settings before spawning |
| Teammate stops on error | Navigate to it with `Shift+Down`, give additional instructions directly |
| Lead shuts down early | Tell it to keep going; tell it to wait for teammates to finish |
| Task status stuck/lagging | Check if work is done; tell lead to nudge the teammate to update status |
| Orphaned tmux sessions | `tmux ls` then `tmux kill-session -t <name>` |

---

## Known Limitations (Experimental)

- No session resumption for in-process teammates (`/resume`, `/rewind` don't restore them)
- Task status can lag — teammates sometimes fail to mark tasks complete
- Shutdown can be slow — teammate finishes its current request first
- One team at a time per lead session
- No nested teams — teammates cannot spawn their own teammates
- Lead is fixed for the team's lifetime — no leadership transfer
- All teammates start with the lead's permission mode; can change individually after spawn
- Split panes not supported in VS Code integrated terminal, Windows Terminal, or Ghostty

---

## Quick Reference: Prompts That Work Well

```
# Parallel code review
Create an agent team to review PR #142. Spawn three reviewers:
- One focused on security
- One checking performance
- One validating test coverage

# Competing hypothesis debugging
Spawn 5 teammates to investigate [bug]. Have them debate each
other's theories like a scientific debate, then update a findings doc
with whatever consensus emerges.

# Independent module implementation
Create a team with 4 teammates to refactor these modules in parallel.
Each teammate should own a separate module with no overlapping files.

# Require plan approval
Spawn an architect teammate to redesign the auth module.
Require plan approval before any changes. Only approve plans
that include migration steps and test coverage.

# Research from multiple angles
Create an agent team to explore [problem] from different angles:
one on UX, one on technical architecture, one as devil's advocate.
```
