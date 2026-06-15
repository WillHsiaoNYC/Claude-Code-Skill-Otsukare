---
name: otsukare
description: Use when starting a long or token-heavy task that might exhaust the Claude Code 5-hour or 7-day usage limit mid-run. Wraps the work in a usage-limit guardrail — arms a safety-net resume at the start, monitors the limits at natural seams, pauses safely (drains subagents, writes a checkpoint, commits WIP) at a 90% soft / 97% hard threshold, then auto-resumes just after the binding limit resets. Triggers on "otsukare", "watch my usage", "pause if I hit the limit", or any long unattended run.
---

# otsukare — usage-limit guardrail

お疲れ — "good work, go rest," then pick the work back up. Invoke this at the **start** of a long or token-heavy task. otsukare does **not** do the work itself; it wraps it in a guardrail that watches the 5h/7d rate limits, pauses safely before a limit is exhausted, and auto-resumes just after the limit resets — even if the session is hard-cut by a sudden usage spike.

> **Prerequisite:** otsukare can only see your usage if your statusline mirrors it to
> `~/.claude/last-statusline-input.json`. See the project README ("Expose your limits to
> your local agent"). If that file is missing, tell the user and stop — otsukare is blind
> without it.

## Constants (edit to taste)

- Helper: `~/.claude/skills/otsukare/scripts/otsukare_usage.py`
- Data file (read-only): `~/.claude/last-statusline-input.json` (mirrored by the statusline on every render)
- State dir: `~/.claude/otsukare/` — holds the checkpoint, the `<session_id>.heartbeat`, and the `<session_id>.state.json` run-metrics file
- `SOFT=90`, `HARD=97`, `STALE_BUFFER=8`, `STALE_AGE=120s`, `RESUME_OFFSET_MIN=10`, retry tick `every 5 min`, `HEARTBEAT_STALE=15min`

The helper already applies `SOFT`/`HARD`/`STALE_BUFFER`/`STALE_AGE`/`RESUME_OFFSET_MIN`; override via `--soft`, `--stale-buffer`, etc. or `OTSUKARE_*` env vars if needed.

## Hard constraint on the wrapped work

**Do not dispatch `run_in_background` subagents during an otsukare run.** Background agents return control immediately and finish later, so you cannot block on them at a seam — their output could land after the checkpoint and be lost. Use **foreground/blocking** subagents only, so "let in-flight subagents finish" is actually enforceable. If a background agent is unavoidable, record its task ID in the checkpoint and reconcile it in resume mode.

## At start (refresh, then arm the safety net)

Before doing any work, set up the dead-man's switch so a sudden usage spike that hard-cuts the session still auto-resumes:

1. **Block until usage is fresh** — do this FIRST. It is the guard against reading a stale reset/usage left by a previous session or window:
   ```bash
   python3 ~/.claude/skills/otsukare/scripts/otsukare_usage.py --wait-fresh
   ```
   This blocks (up to 30s) until the statusline has re-rendered the mirror file with a **current 5h window** (reset in the future), then returns `{"fresh": true, "resets_at": <epoch>, ...}`. The statusline re-renders every few seconds while the session is active, so this normally returns in under a second.
   - `fresh: true` → proceed; the subsequent `--arm` and decision reads now see current data.
   - `timed_out: true` / `fresh: false` → the mirror isn't updating (statusline not wired, or the session is idle). **Tell the user** usage couldn't be refreshed, then proceed on the conservative path (`--arm` will return `provisional: true` and the first seam will re-point it).

   Never read the plain decision/`--arm` for the initial reset time without clearing this barrier first.
2. **Write the initial checkpoint** to `~/.claude/otsukare/<session_id>-<task-slug>.md` (read `session_id` from the data file; slug = short kebab of the task). Use the template in *Pause protocol* step 3, filling Goal / cwd / branch and leaving Done/Next to be updated at each seam.
3. **Touch the heartbeat:** `mkdir -p ~/.claude/otsukare && touch ~/.claude/otsukare/<session_id>.heartbeat`
4. **Initialize run metrics** (baselines for the end-of-task summary):
   ```bash
   python3 ~/.claude/skills/otsukare/scripts/otsukare_usage.py \
     --state ~/.claude/otsukare/<session_id>.state.json --state-action init --task "<task name>"
   ```
5. **Arm the safety-net cron.** Get the target + cron string:
   ```bash
   python3 ~/.claude/skills/otsukare/scripts/otsukare_usage.py --arm
   ```
   This prints `{"safety_target": epoch, "cron": "*/5 H D M *", "provisional": bool, ...}`. Call the **`CronCreate`** tool with `cron` = that string, `recurring: true`, `durable: true`, and `prompt` = the resume prompt (see *Schedule the resume*). Record the returned job ID in the checkpoint. If `provisional: true`, note that the first seam will re-point it.

Then begin the work.

## Run loop

Do the wrapped work in steps. After each **natural seam** — a foreground subagent batch returns, a plan/todo step completes, or you make a commit (never mid-edit) — do a seam check:

1. **Emit a one-line seam note** to the user, e.g. `✓ step 3/8 done — checking usage`. This is required, not cosmetic: emitting text triggers a statusline render, which refreshes the data file so the next read is fresh.
2. **Update progress + heartbeat:** rewrite the checkpoint's `## Done` / `## Next`, then `touch ~/.claude/otsukare/<session_id>.heartbeat`.
3. **Run the helper:**
   ```bash
   python3 ~/.claude/skills/otsukare/scripts/otsukare_usage.py
   ```
   It prints JSON: `{"decision": "...", "now": epoch, "stale": bool, "limits": {...}, "binding_resets_at": epoch|null, "resume_target": epoch|null}`.
4. **Re-point the safety net if the 5h window rolled.** If `limits.five_hour.resets_at` is later than the currently-armed target minus the offset (or the armed target was provisional), `CronDelete` the old job and re-arm via `--arm` so the net always points at the next reset. Update the job ID in the checkpoint.
5. **Act on `decision`:**
   - `continue` → keep working.
   - `soft` → if the remaining work is **near a finish line** (last step, or one more small foreground batch), push through to finish — but re-check at every seam and **never** cross `hard`. If **substantial work remains**, run the **Pause protocol**.
   - `hard` → run the **Pause protocol** at this seam, no exceptions.

If `stale` is `true`, the helper has already added `STALE_BUFFER` to the effective usage (it rounds against you). Trust the decision.

## Pause protocol

When otsukare decides to pause proactively:

1. **Drain, never hard-kill.** Stop dispatching new subagents; let the current foreground subagent finish (control returns naturally). Do **not** `TaskStop` mid-work.
2. **Reach a clean seam.**
3. **Finalize the checkpoint** `~/.claude/otsukare/<session_id>-<task-slug>.md`:
   ```markdown
   # otsukare checkpoint — <task-slug>

   - Goal: <original request, 1-2 sentences>
   - Absolute cwd: <output of `pwd`>
   - Branch: <output of `git rev-parse --abbrev-ref HEAD`>
   - WIP SHA: <filled in step 4, after the commit>
   - Binding limit: <five_hour|seven_day>, resets_at <epoch> (<local time>)
   - Resume target: <resume_target epoch> (<local time>)
   - Safety-net cron job ID: <id>
   - Outstanding background agents: <task IDs, or "none">

   ## Done
   - <completed steps>

   ## Next
   - <remaining steps, in order>
   ```
4. **Commit WIP** and record the SHA into the checkpoint:
   ```bash
   git add -A && git commit -m "wip(otsukare): checkpoint before usage pause"
   git rev-parse HEAD   # paste into the checkpoint's "WIP SHA" line
   ```
5. **Re-point the safety net to the binding reset** if it differs from the armed target (e.g. the 7d limit binds, days later): `CronDelete` the old job, then `CronCreate` with the cron from `--cron-for <resume_target>`. Update the job ID in the checkpoint.
6. **Record the pause in run metrics:**
   ```bash
   python3 ~/.claude/skills/otsukare/scripts/otsukare_usage.py \
     --state ~/.claude/otsukare/<session_id>.state.json --state-action pause --pct <current 5h used %>
   ```
7. **Notify** (single message): current used %, where you stopped, the checkpoint path, the binding limit, and the resume target as **local time**.

### Schedule the resume

The resume prompt used by both the safety-net cron and the proactive re-point:

> `otsukare resume mode. Checkpoint: ~/.claude/otsukare/<session_id>-<task-slug>.md. Binding reset epoch: <binding_resets_at>. Follow the Resume mode section of the otsukare skill.`

For the proactive re-point, get the cron string with:
```bash
python3 ~/.claude/skills/otsukare/scripts/otsukare_usage.py --cron-for <resume_target>
```
It prints `*/5 H D M *` — every 5 min, but only during the target hour/date, so it retries ~12× around the reset and does not fire on other days.

## Resume mode

When a scheduled prompt fires (either the safety net or a proactive re-point):

1. **Is the work still alive?** Check the heartbeat:
   ```bash
   python3 - <<'PY'
   import os, time
   hb = os.path.expanduser("~/.claude/otsukare/<session_id>.heartbeat")
   age = time.time() - os.path.getmtime(hb) if os.path.exists(hb) else 1e9
   print("alive" if age < 15*60 else "dead")
   PY
   ```
   - `alive` → the main run is still going; **exit quietly** (it will handle its own pausing). Let the next tick re-check.
   - `dead` (or checkpoint missing / marked complete) → proceed to take over. If the checkpoint is missing or its `## Next` is empty, `CronDelete` the job and exit — nothing to resume.
2. **Confirm the limit actually cleared.** First block for a fresh render so you don't read a pre-reset file, then run the clear-check:
   ```bash
   python3 ~/.claude/skills/otsukare/scripts/otsukare_usage.py --wait-fresh --wait-timeout 20
   python3 ~/.claude/skills/otsukare/scripts/otsukare_usage.py --resume-check <binding_reset_epoch>
   ```
   - `{"status":"wait", ...}` → **stop**; the next retry tick will try again.
   - `{"status":"clear", ...}` → proceed.
3. **Re-establish context** (a cron prompt fires in whatever cwd/branch the REPL holds):
   - `cd` to the checkpoint's recorded **absolute cwd**.
   - `git checkout <branch>`. If the working tree has uncommitted WIP from a hard cut, commit it (`git add -A && git commit -m "wip(otsukare): recovered uncommitted work"`). Then verify the base is sane; if the recorded **WIP SHA** is set, confirm `git rev-parse HEAD` is that SHA or a descendant of it. If the branch/state is clearly wrong, **abort and report** — do not continue on the wrong base.
4. **Reload** the `## Next` list from the checkpoint.
5. **Record the resume in run metrics.** The state file is keyed by the **original** `session_id` recorded in the checkpoint, so a dead-man's-switch takeover in a fresh session still updates the same metrics:
   ```bash
   python3 ~/.claude/skills/otsukare/scripts/otsukare_usage.py \
     --state ~/.claude/otsukare/<original_session_id>.state.json --state-action resume
   ```
6. **Notify** (one line): `resuming <task>, 5h now at X%`.
7. **Re-arm** a fresh safety net for the new window (`--arm`) and **continue** the work from `## Next`, re-entering this run loop.

## On completion (summary + cleanup)

When the wrapped work finishes:

1. **Print the run summary** — continues, run time, tokens, and cost, all measured as the delta over the otsukare span:
   ```bash
   python3 ~/.claude/skills/otsukare/scripts/otsukare_usage.py \
     --state ~/.claude/otsukare/<session_id>.state.json --state-action summary
   ```
   Show its output to the user verbatim, e.g.:
   ```
   お疲れ! otsukare summary — <task>
     Continued:  2× (91%, 96%)
     Run time:   4h 18m total · 1h 51m active · 2h 27m waiting
     Tokens:     412K in/out · 4.1M cache (4.5M total)
     Cost:       $3.84
   ```
   (If the run never paused, `Continued: 0×` — still a tidy receipt of time/tokens/cost.)
2. `CronDelete` the safety-net job (using the ID in the checkpoint).
3. Mark the checkpoint done — delete `~/.claude/otsukare/<session_id>-<task-slug>.md` or write `STATUS: complete` at the top.
4. Remove the heartbeat and state file: `rm -f ~/.claude/otsukare/<session_id>.heartbeat ~/.claude/otsukare/<session_id>.state.json`.

A run that never hits a limit thus creates the safety-net job at start and deletes it at the end — **net zero lingering jobs.**

## Notes & guards

- The data file updates only when the statusline renders; in practice that is every few seconds while the session is active. The **`--wait-fresh` barrier** at start and resume blocks until a render lands with a current 5h window, so the initial reset time is never read from a stale file. Mid-run seam reads are fine as-is (an active session renders constantly); the stale buffer covers any idle gap.
- Usage is **account-global** — it reflects whichever session rendered last. That is why resume mode requires the file to be fresher than the reset before trusting "cleared."
- If both 5h and 7d are over threshold, the helper binds to the **later** reset (you must wait for both).
- The **safety net** is the failsafe for a sudden swarm-to-100% that hard-cuts the session before a seam: it is armed up front, so a resume is scheduled even if the pause protocol never runs. The **heartbeat** ensures it only takes over when the main run is actually dead, never duplicating a live session.
- If there is no work left when a pause would trigger, just finish normally — run the cleanup, no resume scheduled.
