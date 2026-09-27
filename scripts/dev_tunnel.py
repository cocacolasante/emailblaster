#!/usr/bin/env python3
"""Point the app at a fresh ngrok tunnel (local dev).

Multi-tenant edition: provider keys and webhook secrets now live in the
database per workspace, so this script no longer touches Unipile/Brevo
directly.  It:

  1. Reuses an already-running ngrok (via 127.0.0.1:4040) or starts a
     fresh ``ngrok http 8000`` in the background.
  2. Sets ``WEBHOOK_BASE_URL`` in .env to the tunnel (preserving every
     other line) and force-recreates backend/worker/beat when it changed.
  3. Tells you to re-register each workspace's Unipile webhooks: open
     Settings → Integrations → Unipile → "Register webhooks automatically"
     (it creates the three webhooks pointing at
     ``<tunnel>/webhooks/unipile/<workspace id>`` with that workspace's
     secret header).

Run from the project root with the host Python:
    python3 scripts/dev_tunnel.py [--dry-run] [--no-recreate] [--port N]

Requires: ngrok installed + an authtoken configured (``ngrok config
add-authtoken <T>``).  Pure stdlib — no pip install needed.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
NGROK_ADMIN = "http://127.0.0.1:4040/api/tunnels"
NGROK_START_TIMEOUT = 15  # seconds


# ---- coloured printing -----------------------------------------------------


def _c(s: str, code: str) -> str:
    return f"\033[{code}m{s}\033[0m" if sys.stdout.isatty() else s


def info(msg: str) -> None:
    print(_c("• ", "36") + msg)


def ok(msg: str) -> None:
    print(_c("✓ ", "32") + msg)


def warn(msg: str) -> None:
    print(_c("! ", "33") + msg)


def die(msg: str, code: int = 1) -> None:
    print(_c("✗ ", "31") + msg, file=sys.stderr)
    sys.exit(code)


# ---- .env handling ---------------------------------------------------------


def parse_env(path: Path) -> dict[str, str]:
    if not path.exists():
        die(f".env not found at {path}")
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def write_env_in_place(path: Path, updates: dict[str, str]) -> None:
    """Rewrite .env, updating only the keys in ``updates`` (preserving every
    other line — comments, blank lines, ordering)."""
    lines = path.read_text().splitlines()
    seen: set[str] = set()
    out: list[str] = []
    pat = re.compile(r"^(\s*)([A-Z_][A-Z0-9_]*)\s*=(.*)$")
    for line in lines:
        m = pat.match(line)
        if m and m.group(2) in updates:
            key = m.group(2)
            out.append(f"{m.group(1)}{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    # Append any keys that weren't present yet.
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    path.write_text("\n".join(out) + "\n")


# ---- ngrok -----------------------------------------------------------------


def _ngrok_url_from_admin() -> str | None:
    try:
        with urllib.request.urlopen(NGROK_ADMIN, timeout=2) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError):
        return None
    for tunnel in data.get("tunnels") or []:
        url = tunnel.get("public_url") or ""
        if url.startswith("https://"):
            return url
    return None


def ensure_ngrok(port: int = 8000) -> str:
    """Return the https public URL of an active ngrok tunnel, starting one
    if needed."""
    existing = _ngrok_url_from_admin()
    if existing:
        ok(f"ngrok already running: {existing}")
        return existing

    if not shutil.which("ngrok"):
        die("ngrok not installed.  brew install ngrok or see https://ngrok.com.")

    info(f"starting `ngrok http {port}` in the background…")
    log_path = ROOT / ".ngrok.log"
    log = log_path.open("w")
    # Detach so the tunnel survives this script exiting.
    proc = subprocess.Popen(
        ["ngrok", "http", str(port), "--log=stdout"],
        stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.time() + NGROK_START_TIMEOUT
    while time.time() < deadline:
        url = _ngrok_url_from_admin()
        if url:
            ok(f"ngrok up: {url}  (log at {log_path})")
            return url
        if proc.poll() is not None:
            die(
                "ngrok exited before producing a tunnel.  Check "
                f"{log_path} (common cause: missing authtoken — run "
                "`ngrok config add-authtoken <YOUR_TOKEN>`)."
            )
        time.sleep(0.4)
    die(
        f"ngrok did not produce a public URL within {NGROK_START_TIMEOUT}s.  "
        f"Check {log_path}."
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would change without modifying anything.")
    p.add_argument("--no-recreate", action="store_true",
                   help="Skip the docker compose force-recreate step.")
    p.add_argument("--port", type=int, default=8000,
                   help="Local port ngrok should forward (default: 8000).")
    args = p.parse_args()

    env = parse_env(ENV_PATH)
    tunnel_url = ensure_ngrok(args.port).rstrip("/")
    changed = env.get("WEBHOOK_BASE_URL", "") != tunnel_url

    if args.dry_run:
        warn("DRY RUN — no changes will be made.")
        print(f"  WEBHOOK_BASE_URL: {env.get('WEBHOOK_BASE_URL', '(unset)')} -> {tunnel_url}"
              if changed else "  .env unchanged (tunnel already current)")
        return

    if changed:
        write_env_in_place(ENV_PATH, {"WEBHOOK_BASE_URL": tunnel_url})
        ok(f".env updated: WEBHOOK_BASE_URL={tunnel_url}")
        if args.no_recreate:
            warn("skipping recreate — run `docker compose up -d --force-recreate backend worker beat`.")
        else:
            info("recreating backend/worker/beat so the new .env is picked up…")
            try:
                subprocess.run(["docker", "compose", "up", "-d", "--force-recreate",
                                "backend", "worker", "beat"], cwd=ROOT, check=True)
            except subprocess.CalledProcessError as e:
                die(f"docker compose failed (exit {e.returncode})")
    else:
        info(".env unchanged (tunnel already current)")

    ok("tunnel ready.")
    print(
        "\nNext, in EACH workspace that uses LinkedIn:\n"
        "  Settings → Integrations → Unipile → \"Register webhooks automatically\"\n"
        f"  (webhooks will point at {tunnel_url}/webhooks/unipile/<workspace id>)\n"
        "Brevo's webhook is optional (events are also polled every 10 min); its URL + secret\n"
        "are on the Brevo card if you want real-time delivery events.\n"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Don't leak a noisy traceback on Ctrl-C.
        sys.exit(130)
    except BrokenPipeError:
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        die(f"unexpected error: {type(e).__name__}: {e}")
