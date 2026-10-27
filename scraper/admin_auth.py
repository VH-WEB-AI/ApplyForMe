"""Mints admin access tokens so the maintenance tools can run unattended.

Access tokens last about 15 minutes and a full backfill or sweep runs far longer
than that, which previously meant a human pasting a fresh JWT every few minutes.
POST /auth/login is public, so the tools can mint their own from credentials in
.env and re-mint the moment one expires.

The token is cached in TOKEN_PATH and shared between tools, so a run that starts
while another already has a valid token reuses it rather than logging in again —
the login route is rate-limited to 10 attempts/min/IP.

Two panels run the same backend: the primary (admin.applyforme.us) and the
mirror (admin.yorrp.com). Tools default to the primary; use_panel("mirror")
switches API_ROOT, credentials and the token cache for the rest of the run.
"""
import base64
import json
import os
import time

import requests

PANEL = "primary"
API_ROOT = "https://admin.applyforme.us/api"
TOKEN_PATH = "/tmp/tok"
EMAIL_ENV, PASSWORD_ENV = "ADMIN_EMAIL", "ADMIN_PASSWORD"
# Re-mint this many seconds before expiry, so a request never leaves with a
# token that dies in flight.
REFRESH_MARGIN = 45


def _load_env(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        lines = open(path, encoding="utf-8").readlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def use_panel(name):
    """Points every later call in this process at the given panel."""
    global PANEL, API_ROOT, TOKEN_PATH, EMAIL_ENV, PASSWORD_ENV
    if name == "primary":
        return
    if name != "mirror":
        raise ValueError(f"unknown panel {name!r}")
    _load_env()
    # ADMIN_API_MIRROR_URL is the intake route; the API root is everything
    # before /jobs.
    intake = os.environ.get("ADMIN_API_MIRROR_URL", "").strip()
    PANEL = name
    API_ROOT = intake.split("/jobs")[0] if intake else "https://admin.yorrp.com/api"
    TOKEN_PATH = "/tmp/tok.mirror"
    EMAIL_ENV, PASSWORD_ENV = "ADMIN_MIRROR_EMAIL", "ADMIN_MIRROR_PASSWORD"


def seconds_left(token):
    """Reads exp out of the JWT payload without verifying it — this is a
    scheduling hint, not a security check; the server remains the authority."""
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "==" * (-len(payload) % 4)))
        return int(data["exp"] - time.time())
    except Exception:
        return 0


def login():
    _load_env()
    email = os.environ.get(EMAIL_ENV, "").strip()
    password = os.environ.get(PASSWORD_ENV, "").strip()
    if not email or not password:
        raise RuntimeError(f"{EMAIL_ENV} / {PASSWORD_ENV} not set in .env")

    for attempt in range(1, 7):
        try:
            response = requests.post(f"{API_ROOT}/auth/login",
                                     json={"email": email, "password": password}, timeout=30)
            break
        except requests.RequestException:
            # DNS / network blips shouldn't kill a multi-hour sweep at login.
            if attempt == 6:
                raise
            time.sleep(10 * attempt)
    response.raise_for_status()
    body = response.json()
    token = body.get("accessToken") or (body.get("tokens") or {}).get("accessToken")
    if not token:
        raise RuntimeError(f"login returned no accessToken: {sorted(body)}")

    old = os.umask(0o077)
    try:
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(token)
    finally:
        os.umask(old)
    return token


def get_token(force=False):
    """Cached token if it still has useful life, otherwise a fresh one."""
    if not force:
        try:
            cached = open(TOKEN_PATH, encoding="utf-8").read().strip()
            if cached and seconds_left(cached) > REFRESH_MARGIN:
                return cached
        except OSError:
            pass
    return login()


def auth_headers(json_body=False, force=False):
    headers = {"Authorization": f"Bearer {get_token(force=force)}"}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers
