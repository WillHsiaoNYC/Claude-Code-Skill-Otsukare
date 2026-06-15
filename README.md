# otsukare — a usage-limit guardrail skill for Claude Code

お疲れ (*otsukare*) — "good work, go rest." A [Claude Code](https://claude.com/claude-code) **skill** that wraps a long task in a rate-limit guardrail: it watches your 5-hour and 7-day usage, pauses the work at a safe checkpoint before a limit is exhausted, and **auto-resumes just after the limit resets** — even if a sudden burst of subagents hard-cuts the session.

> You start a big multi-hour job, walk away, and come back to find it **finished** — it quietly paused when you ran out of budget and picked itself back up after the window reset, instead of dying half-done.

> **🤖 Installing with an AI agent?** Paste this repo's URL into Claude Code (or any coding agent) and say **"install this skill by following the README."** The agent should follow [Install → With an AI agent](#install--with-an-ai-agent-recommended) below — it's written as a step-by-step procedure an agent can execute directly, including the one manual-ish step (wiring your statusline).

---

## The problem

Claude Code enforces a rolling **5-hour** limit and a **7-day** limit. A long, token-heavy run (big refactors, multi-agent workflows, batch jobs) can hit the wall mid-task and simply stop — losing the thread and leaving the work half-finished. You either babysit the clock or lose progress.

## How it works

otsukare runs as a skill you invoke at the **start** of a long task. It doesn't do the work itself — it wraps it:

1. **Arms a safety net.** Before any work, it schedules a "dead-man's-switch" resume for just after the next 5h reset. If a swarm of subagents spikes you to 100% before the skill can react, a resume is *already scheduled*.
2. **Monitors at seams.** After each natural stopping point (a subagent batch returns, a step completes, a commit), it checks usage.
3. **Pauses safely.** At **90%** (soft) it pauses if substantial work remains; at **97%** (hard) it always pauses. Pausing = drain in-flight subagents, write a checkpoint, commit work-in-progress.
4. **Resumes automatically.** Just after the binding limit resets (+10 min), it re-establishes context (cd, checkout, verify), confirms the limit actually cleared, and continues from where it left off.
5. **Reports & cleans up.** On completion it prints a run summary — how many times it continued, total/active/waiting time, tokens, and cost (measured as the delta over the otsukare span) — then removes the scheduled job, checkpoint, and state. Net zero lingering state.

```
お疲れ! otsukare summary — refactor auth module
  Continued:  2× (91%, 96%)
  Run time:   4h 18m total · 1h 51m active · 2h 27m waiting
  Tokens:     412K in/out · 4.1M cache (4.5M total)
  Cost:       $3.84
```

A **heartbeat** file lets the safety net tell whether the main run is still alive, so it only takes over if the session actually died — never double-resuming a healthy run.

Architecture: a small, unit-tested Python helper (`scripts/otsukare_usage.py`) owns all the freshness/threshold/scheduling math; `SKILL.md` is the model-facing playbook that orchestrates pause/resume.

---

## Install — With an AI agent (recommended)

**If you are an AI coding agent installing this, follow these steps exactly.** They are deterministic; the only judgement call is editing the user's existing statusline in step 4 (do it idempotently and back it up).

**1. Get the files.** Clone the repo (or use the local copy if already present):
```bash
git clone https://github.com/WillHsiaoNYC/Claude-Code-Skill-Otsukare.git
cd Claude-Code-Skill-Otsukare
```

**2. Install the skill** into the Claude Code user skills directory:
```bash
mkdir -p ~/.claude/skills/otsukare
cp -R SKILL.md scripts ~/.claude/skills/otsukare/
chmod +x ~/.claude/skills/otsukare/scripts/otsukare_usage.py
```

**3. Verify the helper** runs and its tests pass:
```bash
python3 ~/.claude/skills/otsukare/scripts/otsukare_usage.py --cron-for 1700000000   # prints a cron string
( cd ~/.claude/skills/otsukare/scripts && python3 -m unittest test_otsukare_usage ) # all tests OK
```

**4. Wire the rate-limit mirror (REQUIRED — otsukare is blind without it).** Claude Code pipes live rate-limit data *only* to the statusline script's stdin, so the statusline must mirror that blob to `~/.claude/last-statusline-input.json`.

  a. Read `~/.claude/settings.json` (and `~/.claude/settings.local.json` if it exists) and find `"statusLine".command`.

  b. **If a statusline script is configured** (e.g. `"command": "bash ~/.claude/statusline.sh"`):
   - Resolve the script path and read it.
   - **Idempotency:** if it already contains `last-statusline-input.json`, the mirror is present — skip this step.
   - Otherwise back it up (`cp script script.bak`), then insert these lines **immediately after the line that captures stdin** (usually `input=$(cat)` — match the script's actual variable name if different):
     ```bash
     # otsukare: mirror the rate-limit blob so the local agent can read it (atomic write)
     printf '%s' "$input" > ~/.claude/last-statusline-input.json.tmp 2>/dev/null \
       && mv ~/.claude/last-statusline-input.json.tmp ~/.claude/last-statusline-input.json 2>/dev/null
     ```
   - If the script never captures stdin, add `input=$(cat)` as its first executable line, then the mirror lines above (Claude Code feeds the JSON on stdin).

  c. **If NO statusline is configured:** create `~/.claude/statusline.sh` with the minimal script from [Manual setup](#install--manual) below (`chmod +x` it), and merge this into `~/.claude/settings.json` **without clobbering other keys**:
   ```json
   { "statusLine": { "type": "command", "command": "bash ~/.claude/statusline.sh" } }
   ```

**5. Verify the mirror works.** Ask the user to send any message in Claude Code (the statusline renders every few seconds while active), then:
```bash
python3 ~/.claude/skills/otsukare/scripts/otsukare_usage.py
```
Expect JSON with `"ok": true` and real `five_hour` / `seven_day` values. If `"ok": false` or the values are missing, the mirror isn't writing yet — re-check step 4.

**6. Finish.** Tell the user to **restart Claude Code** so it discovers the skill, then invoke it with:
```
otsukare — <your long task>
```

---

## Install — Manual

```bash
git clone https://github.com/WillHsiaoNYC/Claude-Code-Skill-Otsukare.git
cd Claude-Code-Skill-Otsukare
./install.sh        # copies the skill into ~/.claude/skills/otsukare and runs the tests
```

Then do the **one required manual step** — mirror your usage to a file the skill can read.

### Add the mirror line to your existing statusline

In the script configured under `statusLine.command` in `~/.claude/settings.json`, right after it reads stdin, add:

```bash
input=$(cat)

# otsukare: mirror the rate-limit blob so the local agent can read it (atomic write)
printf '%s' "$input" > ~/.claude/last-statusline-input.json.tmp 2>/dev/null \
  && mv ~/.claude/last-statusline-input.json.tmp ~/.claude/last-statusline-input.json 2>/dev/null
```

### Don't have a statusline yet?

Create `~/.claude/statusline.sh`:

```bash
#!/bin/bash
input=$(cat)

# otsukare: mirror the rate-limit blob (atomic write)
printf '%s' "$input" > ~/.claude/last-statusline-input.json.tmp 2>/dev/null \
  && mv ~/.claude/last-statusline-input.json.tmp ~/.claude/last-statusline-input.json 2>/dev/null

# minimal status line: model + 5h usage
echo "$input" | python3 -c '
import json, sys
d = json.load(sys.stdin)
model = d.get("model", {}).get("display_name", "Claude")
five = (d.get("rate_limits", {}).get("five_hour", {}) or {}).get("used_percentage", "?")
print(f"{model} · 5h {five}%")
'
```

Then wire it in `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "bash ~/.claude/statusline.sh"
  }
}
```

Restart Claude Code afterward so it discovers the skill.

---

## Why the statusline step?

Claude Code provides live rate-limit data **only to your statusline script's stdin** — it is not available to the model or to a plain script. Mirroring that blob to `~/.claude/last-statusline-input.json` is what lets otsukare's helper read your current 5h/7d usage and reset times.

> The mirrored blob must contain a `rate_limits` object with `five_hour` / `seven_day` (each with `used_percentage` and `resets_at`). Claude Code provides these to the statusline; if your plan or version doesn't surface them, otsukare can't see your limits.

## Usage

Invoke at the start of a long task:

```
otsukare — then <your long task here>
```

otsukare arms the safety net, runs your task, and handles the limits transparently. When it pauses, it tells you exactly where it stopped and when it will resume. You can also use the helper standalone:

```bash
python3 ~/.claude/skills/otsukare/scripts/otsukare_usage.py | python3 -m json.tool
```

## Configuration

Defaults live at the top of `scripts/otsukare_usage.py`; override per-call via flags (`--soft 85`, `--stale-buffer 10`, …) or environment variables (`OTSUKARE_SOFT=85`):

| Constant | Default | Meaning |
|---|---|---|
| `SOFT` | 90 | Soft threshold (%) — pause if substantial work remains |
| `HARD` | 97 | Hard ceiling (%) — always pause |
| `STALE_BUFFER` | 8 | Points added to usage when the data file is stale (rounds against you) |
| `STALE_AGE` | 120 | Seconds before a read is considered stale |
| `RESUME_OFFSET_MIN` | 10 | Minutes after reset to resume |

## Helper modes

```bash
otsukare_usage.py                     # decision for current usage
otsukare_usage.py --wait-fresh        # block until the mirror is freshly rendered with a current 5h reset
otsukare_usage.py --arm               # safety-net target for the 5h window (validate-or-fallback)
otsukare_usage.py --resume-check N    # 'clear' / 'wait' against binding reset epoch N
otsukare_usage.py --cron-for EPOCH    # bounded retry-window cron string for EPOCH
otsukare_usage.py --state S --state-action init|pause|resume|summary   # run-metrics lifecycle + summary
```

## How tokens & cost are measured

The summary reports the **delta over the otsukare span**, not whole-session totals — because both the token and cost sources are *session-cumulative* and a session often includes work from before otsukare was invoked.

- **Baseline + delta.** At skill start, otsukare snapshots a baseline (current token total and `total_cost_usd`). At completion it subtracts the baseline. Across pauses/resumes the baseline is re-snapshotted at each resume and the per-segment deltas are summed, so the number reflects only the wrapped task. (State is keyed by the original `session_id`, so a dead-man's-switch resume in a fresh session still accumulates into the same totals.)

- **Tokens** come from the session **transcript** (`transcript_path` in the statusline blob) — a `.jsonl` where each assistant message carries a `usage` object. otsukare sums:
  - `in/out` = `input_tokens` + `output_tokens`
  - `cache` = `cache_creation_input_tokens` + `cache_read_input_tokens`

  These are shown separately on purpose: **cache-read usually dominates the raw count** (re-reading context each turn is cheap), so a single "total tokens" figure is misleading. The blob's `context_window.total_input_tokens` is *not* used — it's the current context-window size, not cumulative usage.

- **Cost** comes from the statusline blob's `cost.total_cost_usd` — Claude Code's own running cost meter — as a start→end delta. Because it accounts for cache and model pricing automatically, **cost is the most honest bottom line** of the four numbers.

- **Run time** is wall-clock from `started_at` to completion, split into *active* (sum of work segments) and *waiting* (sum of paused-for-reset gaps) via the pause/resume timestamps.

**Caveats.** The numbers are best-effort: tokens depend on the transcript being readable, and a hard cut mid-segment can miss the sliver of work since the last checkpoint. The arithmetic lives in the unit-tested helper (`--state-action init|pause|resume|summary`), not in model prose, so the accounting itself is deterministic.

## Limitations

- **Resume needs Claude Code running.** The resume fires via a scheduled job that runs while the REPL is idle. It's robust if your machine stays awake with Claude Code open; if the app is fully quit at reset time, resume waits until it's next launched.
- **Usage data is best-effort.** The mirror file refreshes when the statusline renders (every few seconds while active). At start and resume, otsukare blocks on a `--wait-fresh` barrier until a render lands with a *current* 5h window, so it never reads a stale reset from a previous session; a conservative stale buffer covers any remaining idle gap. It still isn't a guaranteed live meter.
- **Account-global usage.** Limits are shared across all your sessions; otsukare reads whichever session rendered last and verifies freshness before trusting a "cleared" reading.
- **Foreground subagents only** during a wrapped run (background agents can't be drained at a checkpoint).

## License

[MIT](./LICENSE) © Will Hsiao
