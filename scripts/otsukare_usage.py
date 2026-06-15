#!/usr/bin/env python3
"""otsukare usage-decision + scheduling-math helper.

Reads the Claude Code statusline mirror blob (~/.claude/last-statusline-input.json)
and emits a JSON decision: continue / soft (pause-worthy) / hard (must pause).
All freshness logic is mtime-based because the blob carries no timestamp.

Modes:
  (default)           decision for the current usage
  --arm               safety-net target for the 5h window (validate-or-fallback)
  --resume-check N    'clear'/'wait' check against binding reset epoch N
  --cron-for EPOCH    print the bounded retry-window cron string for EPOCH
"""
import argparse
import json
import os
import sys
import time

DEFAULTS = dict(soft=90, hard=97, stale_buffer=8, stale_age=120, resume_offset_min=10)
LIMIT_KEYS = ("five_hour", "seven_day")
FIVE_HOUR_SECONDS = 5 * 3600


def load_blob(path):
    with open(path) as f:
        return json.load(f)


def _limit(blob, key):
    rl = (blob.get("rate_limits") or {}).get(key) or {}
    return rl.get("used_percentage"), rl.get("resets_at")


def decide(path, now, mtime, cfg):
    blob = load_blob(path)
    age = now - mtime
    stale = age > cfg["stale_age"]
    buf = cfg["stale_buffer"] if stale else 0
    out = {"ok": True, "now": now, "stale": stale, "age_seconds": age,
           "mtime": mtime, "limits": {}}
    over_resets = []
    decision = "continue"
    for key in LIMIT_KEYS:
        used, resets = _limit(blob, key)
        if used is None:
            out["limits"][key] = None
            continue
        eff = used + buf
        out["limits"][key] = {"used": used, "effective": eff, "resets_at": resets}
        if eff >= cfg["hard"]:
            decision = "hard"
        elif eff >= cfg["soft"] and decision != "hard":
            decision = "soft"
        if eff >= cfg["soft"] and resets is not None:
            over_resets.append(resets)
    out["decision"] = decision
    if over_resets:
        binding = max(over_resets)  # must wait for the LATEST reset
        out["binding_resets_at"] = binding
        out["resume_target"] = binding + cfg["resume_offset_min"] * 60
    else:
        out["binding_resets_at"] = None
        out["resume_target"] = None
    return out


def arm_target(path, now, mtime, cfg):
    """Compute the safety-net resume target for the 5h window, to be armed at
    skill start as a dead-man's switch. Uses the cached 5h reset only if it is
    genuinely in the future; otherwise (missing, or a window that already rolled
    over) falls back to a worst-case 'now + 5h' and flags it provisional so the
    caller knows to re-point it at the first seam once a fresh render lands."""
    blob = load_blob(path)
    _used, resets = _limit(blob, "five_hour")
    if resets is not None and resets > now:
        target = resets + cfg["resume_offset_min"] * 60
        provisional = False
    else:
        target = now + FIVE_HOUR_SECONDS + cfg["resume_offset_min"] * 60
        provisional = True
    return {
        "now": now,
        "five_hour_resets_at": resets,
        "safety_target": target,
        "cron": cron_for(target),
        "provisional": provisional,
        "stale": (now - mtime) > cfg["stale_age"],
    }


def resume_check(path, now, mtime, reset_epoch, cfg):
    """At resume time, only report 'clear' when the file is genuinely fresh
    (mtime newer than the reset) AND usage has dropped back under the soft
    threshold. Otherwise 'wait' and let the next retry tick try again."""
    out = {"file_mtime": mtime, "reset_epoch": reset_epoch}
    if mtime <= reset_epoch:
        out["status"] = "wait"
        out["reason"] = "stale_pre_reset"
        return out
    blob = load_blob(path)
    worst = 0
    for key in LIMIT_KEYS:
        used, _ = _limit(blob, key)
        if used is not None:
            worst = max(worst, used)
    out["max_used"] = worst
    if worst < cfg["soft"]:
        out["status"] = "clear"
    else:
        out["status"] = "wait"
        out["reason"] = "still_over"
    return out


def cron_for(epoch):
    """Cron string that fires every 5 min, but ONLY during the target hour on
    the target date (local time): '*/5 H D M *'. Bounds the resume retry window
    to ~12 ticks around the reset, with no firing on other days/hours."""
    t = time.localtime(epoch)
    return "*/5 {} {} {} *".format(t.tm_hour, t.tm_mday, t.tm_mon)


# --- Freshness barrier --------------------------------------------------------
# The mirror file is rewritten whenever the statusline renders, which happens
# every few seconds while a session is active (verified empirically) — but it
# reflects whichever session rendered last. A file left by a previous, already-
# expired window has a `resets_at` in the past. wait_fresh() blocks until a
# render lands after we started AND the 5h window is current, so callers never
# act on a stale reset/usage from another session.

def _read_freshness(path):
    """Return (mtime, five_hour_resets_at); (0, None) if missing/unreadable."""
    try:
        mtime = os.path.getmtime(path)
        blob = load_blob(path)
    except OSError:
        return 0, None
    return mtime, _limit(blob, "five_hour")[1]


def _is_fresh(mtime, start_mtime, resets_at, now, max_age=15):
    """Fresh = (a render landed after we began waiting, OR the file is already
    very recent) AND the 5h window is current (reset in the future). The
    future-reset guard is what rejects a stale file from an expired window."""
    rendered_since = mtime > start_mtime
    already_recent = (now - mtime) <= max_age
    valid_window = resets_at is not None and resets_at > now
    return (rendered_since or already_recent) and valid_window


def wait_fresh(path, timeout=30, poll=0.5, max_age=15,
               clock=None, sleeper=None, reader=None):
    """Block until the mirror file is freshly rendered with a current 5h window.
    Returns {fresh, timed_out, waited_seconds, mtime, resets_at}. On timeout it
    returns fresh=False so the caller can warn and fall back conservatively."""
    clock = clock or time.time
    sleeper = sleeper or time.sleep
    reader = reader or _read_freshness
    start = clock()
    start_mtime = reader(path)[0]
    while True:
        now = clock()
        mtime, resets = reader(path)
        if _is_fresh(mtime, start_mtime, resets, now, max_age):
            return {"ok": True, "fresh": True, "timed_out": False,
                    "waited_seconds": round(now - start, 1),
                    "mtime": int(mtime), "resets_at": resets}
        if now - start >= timeout:
            return {"ok": resets is not None, "fresh": False, "timed_out": True,
                    "waited_seconds": round(now - start, 1),
                    "mtime": int(mtime), "resets_at": resets}
        sleeper(poll)


# --- Run-metrics state (continues / time / tokens / cost) ----------------------
# All token and cost numbers are session-cumulative at the source, so the run
# summary measures the DELTA over the otsukare-wrapped span: a baseline is taken
# at start and at each resume, and per-segment deltas are accumulated. This keeps
# the math in tested code rather than in model prose.

def transcript_tokens(path):
    """Sum token usage across a Claude Code transcript .jsonl: returns
    {'in_out': input+output, 'cache': cache_creation+cache_read}. A missing or
    unreadable transcript -> zeros, so the summary degrades gracefully."""
    totals = {"in_out": 0, "cache": 0}
    try:
        f = open(path)
    except OSError:
        return totals
    with f:
        for line in f:
            try:
                usage = (json.loads(line).get("message") or {}).get("usage")
            except Exception:
                continue
            if not usage:
                continue
            totals["in_out"] += (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
            totals["cache"] += ((usage.get("cache_creation_input_tokens") or 0)
                                + (usage.get("cache_read_input_tokens") or 0))
    return totals


def _snapshot(blob_path):
    """Current (token totals, cost_usd, transcript_path) from the live blob."""
    try:
        blob = load_blob(blob_path)
    except OSError:
        return {"in_out": 0, "cache": 0}, 0.0, ""
    tpath = blob.get("transcript_path", "")
    cost = (blob.get("cost") or {}).get("total_cost_usd", 0.0)
    return transcript_tokens(tpath), cost, tpath


def _load_state(path):
    with open(path) as f:
        return json.load(f)


def _save_state(path, state):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f)


def state_init(path, task, blob_path, now):
    tok, cost, tpath = _snapshot(blob_path)
    _save_state(path, {
        "task": task, "started_at": now, "segment_start": now,
        "resume_count": 0, "pauses": [], "pause_at": None,
        "active_seconds": 0, "paused_seconds": 0,
        "tokens_accrued": {"in_out": 0, "cache": 0}, "cost_accrued_usd": 0.0,
        "token_baseline": tok, "cost_baseline_usd": cost, "transcript": tpath,
    })


def _accrue_segment(state, tok, cost):
    base = state["token_baseline"]
    state["tokens_accrued"]["in_out"] += max(0, tok["in_out"] - base["in_out"])
    state["tokens_accrued"]["cache"] += max(0, tok["cache"] - base["cache"])
    state["cost_accrued_usd"] += max(0.0, cost - state["cost_baseline_usd"])


def state_pause(path, blob_path, now, pct):
    state = _load_state(path)
    tok, cost, _ = _snapshot(blob_path)
    state["active_seconds"] += max(0, now - state["segment_start"])
    _accrue_segment(state, tok, cost)
    state["pauses"].append({"at": now, "pct": pct})
    state["pause_at"] = now
    _save_state(path, state)


def state_resume(path, blob_path, now):
    state = _load_state(path)
    tok, cost, tpath = _snapshot(blob_path)
    state["resume_count"] += 1
    if state.get("pause_at"):
        state["paused_seconds"] += max(0, now - state["pause_at"])
    state["pause_at"] = None
    state["segment_start"] = now
    state["token_baseline"] = tok
    state["cost_baseline_usd"] = cost
    state["transcript"] = tpath
    _save_state(path, state)


def summarize(state, blob_path, now):
    tok, cost, _ = _snapshot(blob_path)
    in_out = state["tokens_accrued"]["in_out"] + max(0, tok["in_out"] - state["token_baseline"]["in_out"])
    cache = state["tokens_accrued"]["cache"] + max(0, tok["cache"] - state["token_baseline"]["cache"])
    cost_total = state["cost_accrued_usd"] + max(0.0, cost - state["cost_baseline_usd"])
    return {
        "task": state.get("task", ""),
        "resume_count": state.get("resume_count", 0),
        "pause_pcts": ", ".join("{}%".format(p["pct"]) for p in state.get("pauses", [])
                                if p.get("pct") is not None),
        "total_seconds": max(0, now - state["started_at"]),
        "active_seconds": state["active_seconds"] + max(0, now - state["segment_start"]),
        "paused_seconds": state["paused_seconds"],
        "tokens": {"in_out": in_out, "cache": cache, "total": in_out + cache},
        "cost_usd": round(cost_total, 2),
    }


def _fmt_dur(seconds):
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "{}h {}m".format(h, m)
    if m:
        return "{}m {}s".format(m, s)
    return "{}s".format(s)


def _fmt_tokens(n):
    if n >= 1_000_000:
        return "{:.1f}M".format(n / 1_000_000)
    if n >= 1_000:
        return "{:.0f}K".format(n / 1_000)
    return str(int(n))


def format_summary(s):
    pcts = " ({})".format(s["pause_pcts"]) if s["pause_pcts"] else ""
    title = "お疲れ! otsukare summary"
    if s["task"]:
        title += " — " + s["task"]
    t = s["tokens"]
    return "\n".join([
        title,
        "  Continued:  {}×{}".format(s["resume_count"], pcts),
        "  Run time:   {} total · {} active · {} waiting".format(
            _fmt_dur(s["total_seconds"]), _fmt_dur(s["active_seconds"]),
            _fmt_dur(s["paused_seconds"])),
        "  Tokens:     {} in/out · {} cache ({} total)".format(
            _fmt_tokens(t["in_out"]), _fmt_tokens(t["cache"]), _fmt_tokens(t["total"])),
        "  Cost:       ${:.2f}".format(s["cost_usd"]),
    ])


def _build_parser():
    p = argparse.ArgumentParser(description="otsukare usage-decision helper")
    p.add_argument("--file",
                   default=os.path.expanduser("~/.claude/last-statusline-input.json"))
    p.add_argument("--now", type=int, default=None, help="override current epoch (testing)")
    p.add_argument("--mtime", type=int, default=None, help="override file mtime (testing)")
    p.add_argument("--arm", action="store_true",
                   help="print the safety-net arm target for the 5h window")
    p.add_argument("--resume-check", type=int, default=None, metavar="RESET_EPOCH",
                   help="resume-clear check against this binding reset epoch")
    p.add_argument("--cron-for", type=int, default=None, metavar="EPOCH",
                   help="print the bounded retry-window cron string for this epoch")
    p.add_argument("--wait-fresh", action="store_true",
                   help="block until the mirror file is freshly rendered with a current 5h reset")
    p.add_argument("--wait-timeout", type=int, default=30,
                   help="max seconds to wait for --wait-fresh")
    p.add_argument("--state", default=None, help="path to the otsukare run-state JSON")
    p.add_argument("--state-action", choices=["init", "pause", "resume", "summary"],
                   default=None, help="run-metrics lifecycle action on the state file")
    p.add_argument("--task", default="", help="task name (for --state-action init)")
    p.add_argument("--pct", type=int, default=None,
                   help="usage %% at pause (for --state-action pause)")
    for k, v in DEFAULTS.items():
        env = os.environ.get("OTSUKARE_" + k.upper())
        p.add_argument("--" + k.replace("_", "-"), type=int,
                       default=int(env) if env else v)
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    now = args.now if args.now is not None else int(time.time())
    if args.cron_for is not None:
        print(cron_for(args.cron_for))
        return 0
    if args.wait_fresh:
        print(json.dumps(wait_fresh(args.file, timeout=args.wait_timeout)))
        return 0
    if args.state_action is not None:
        if not args.state:
            print(json.dumps({"ok": False, "error": "--state-action requires --state"}))
            return 2
        try:
            if args.state_action == "init":
                state_init(args.state, args.task, args.file, now)
                print(json.dumps({"ok": True, "state": args.state}))
            elif args.state_action == "pause":
                state_pause(args.state, args.file, now, args.pct)
                print(json.dumps({"ok": True}))
            elif args.state_action == "resume":
                state_resume(args.state, args.file, now)
                print(json.dumps({"ok": True}))
            else:  # summary
                print(format_summary(summarize(_load_state(args.state), args.file, now)))
        except OSError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 2
        return 0
    cfg = {k: getattr(args, k) for k in DEFAULTS}
    try:
        mtime = args.mtime if args.mtime is not None else int(os.path.getmtime(args.file))
        if args.arm:
            print(json.dumps(arm_target(args.file, now, mtime, cfg)))
        elif args.resume_check is not None:
            print(json.dumps(resume_check(args.file, now, mtime, args.resume_check, cfg)))
        else:
            print(json.dumps(decide(args.file, now, mtime, cfg)))
    except OSError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
