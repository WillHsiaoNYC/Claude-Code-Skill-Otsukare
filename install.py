#!/usr/bin/env python3
"""otsukare installer (cross-platform). Copies the skill into the Claude Code
user skills dir and runs the helper tests. Does NOT touch your statusline or
settings — see the README to wire the rate-limit mirror."""
import os
import shutil
import subprocess
import sys


def install(src, dest):
    """Copy SKILL.md + scripts/ from src into dest (idempotent)."""
    os.makedirs(dest, exist_ok=True)
    shutil.copy2(os.path.join(src, "SKILL.md"), os.path.join(dest, "SKILL.md"))
    shutil.copytree(os.path.join(src, "scripts"), os.path.join(dest, "scripts"),
                    dirs_exist_ok=True)
    return dest


def main():
    src = os.path.dirname(os.path.abspath(__file__))
    dest = os.path.join(os.path.expanduser("~"), ".claude", "skills", "otsukare")
    print("Installing otsukare -> " + dest)
    install(src, dest)
    try:                                   # best-effort; no-op on Windows
        os.chmod(os.path.join(dest, "scripts", "otsukare_usage.py"), 0o755)
    except OSError:
        pass
    print("Running tests...")
    r = subprocess.run([sys.executable, "-m", "unittest", "test_otsukare_usage"],
                       cwd=os.path.join(dest, "scripts"))
    if r.returncode == 0:
        print("\n  Skill installed. Next: wire the rate-limit mirror (see README),"
              "\n  then restart Claude Code.")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
