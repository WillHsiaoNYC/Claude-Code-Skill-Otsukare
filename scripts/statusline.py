#!/usr/bin/env python3
"""otsukare statusline: mirror Claude Code's rate-limit blob to a file the local
agent can read, and print a minimal status line. Cross-platform (no shell)."""
import json
import os
import sys


def mirror_path(home=None):
    return os.path.join(home or os.path.expanduser("~"),
                        ".claude", "last-statusline-input.json")


def write_mirror(raw, dest):
    """Atomically write the blob: tmp in the same dir, then os.replace (atomic on
    Windows and Unix). Swallow OSError so a transient lock never breaks rendering."""
    tmp = dest + ".tmp"
    try:
        d = os.path.dirname(dest)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(raw)
        os.replace(tmp, dest)
    except OSError:
        pass


def format_status(raw):
    try:
        d = json.loads(raw)
        model = (d.get("model") or {}).get("display_name", "Claude")
        five = ((d.get("rate_limits") or {}).get("five_hour") or {}).get("used_percentage", "?")
        return "{} · 5h {}%".format(model, five)
    except Exception:
        return "Claude"


def main():
    # The status line includes non-ASCII (the · separator). On a cp932/Shift-JIS
    # console stdout defaults to the locale codec and would raise UnicodeEncodeError
    # every render; force UTF-8 so rendering never crashes.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    raw = sys.stdin.read()
    write_mirror(raw, mirror_path())
    sys.stdout.write(format_status(raw))
    return 0


if __name__ == "__main__":
    sys.exit(main())
