#!/usr/bin/env python3
"""Obtain a Moodle Web Services token when ``user/managetoken.php`` is unavailable.

Many Moodle sites hide or disable the *Security keys* page (``user/managetoken.php``),
so there is no UI to read or regenerate the token that ``moodle-mcp`` needs. This
script gets one through the official Moodle mobile-app web service instead, using
two complementary strategies:

``login-token``
    ``POST /login/token.php`` with username + password and
    ``service=moodle_mobile_app``. One request; requires a native (non-SSO)
    account and the mobile service enabled.

``mobile-launch``
    Log in, then ``GET /admin/tool/mobile/launch.php?service=...&passport=...&urlscheme=moodlemobile``
    and decode the ``moodlemobile://token=<base64>`` redirect. This is the same path the
    official mobile app uses and works even when ``login/token.php`` is blocked.

Both strategies verify the token with ``core_webservice_get_site_info`` before
reporting success, and ``--write-env`` / ``--hermes`` can drop the fresh token into
existing ``.env`` files (the running MCP server keeps the env from spawn time — restart
the gateway afterwards).

Examples
--------
Interactive (prompts for the password, never echoes it)::

    python scripts/get_moodle_token.py --site https://moodle.example.com --username me

Verify an existing token::

    python scripts/get_moodle_token.py --site https://moodle.example.com --verify "$MOODLE_TOKEN"

Fetch and write it into every Hermes .env that already has MOODLE_TOKEN::

    python scripts/get_moodle_token.py --username me --hermes

Exit codes: ``0`` success, ``1`` failure (auth/API), ``2`` usage error.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import getpass
import hashlib
import json
import os
import re
import secrets
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Iterable

import requests

# Cloudflare fronts some Moodle installs (Polibatam included) and answers 403 to the
# default python-requests User-Agent. Keep this in sync with src/moodle_mcp/moodle.py.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

DEFAULT_SERVICE = "moodle_mobile_app"
DEFAULT_URLSCHEME = "moodlemobile"
WEBSERVICE_PATH = "/webservice/rest/server.php"
LOGIN_TOKEN_PATH = "/login/token.php"
LAUNCH_PATH = "/admin/tool/mobile/launch.php"
LOGIN_PATH = "/login/index.php"

ENV_TOKEN_RE = re.compile(r"^\s*(?:export\s+)?MOODLE_TOKEN\s*=", re.MULTILINE)
LOGINTOKEN_RE = re.compile(r'name="logintoken"\s+value="([^"]+)"')
LOGIN_ERROR_RE = re.compile(
    r'class="[^"]*(alert-danger|loginerrors)[^"]*"|<div[^>]*id="loginerrormessage"', re.IGNORECASE
)
LOGIN_TITLE_RE = re.compile(r"<title>[^<]*log\s*in", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")

SESSION_ERROR = "not logged in (session cookie invalid or expired): redirected to the login page"

# Trees that must never be rewritten when updating tokens across profiles.
SKIP_DIR_PARTS = frozenset({"state-snapshots", "backups", "backup", "node_modules", "__pycache__", ".git"})


# --------------------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------------------
def normalize_site_url(url: str) -> str:
    """Return the bare site root for a base URL, a ``server.php`` URL, or a bare host."""
    value = (url or "").strip().strip('"').strip("'")
    if not value:
        raise ValueError("empty site URL")
    if "://" not in value:
        value = "https://" + value
    parsed = urllib.parse.urlsplit(value)
    path = parsed.path.rstrip("/")
    if path.endswith(WEBSERVICE_PATH):
        path = path[: -len(WEBSERVICE_PATH)]
    path = path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def api_endpoint(site: str) -> str:
    """REST endpoint for a site root (or for a URL that already contains it)."""
    root = normalize_site_url(site)
    if root.endswith(WEBSERVICE_PATH):
        return root
    return root + WEBSERVICE_PATH


def new_passport() -> str:
    """32 hex chars — the ``passport`` the mobile launch URL expects."""
    return secrets.token_hex(16)


def fingerprint(value: str | None) -> str:
    """Log-safe token fingerprint: length + short digest, never the secret."""
    if not value:
        return "EMPTY"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"len={len(value)} sha256:{digest}"


def mask(value: str | None) -> str:
    """Keep a 6-char prefix for eyeballing, hide the rest."""
    if not value or len(value) <= 6:
        return "***"
    return value[:6] + "..."


def decode_launch_uri(uri: str) -> tuple[str, str] | None:
    """Decode ``moodlemobile://token=<base64>`` into ``(token, privatetoken)``.

    Moodle base64-encodes ``passport:token:privatetoken`` (older builds may omit the
    passport, giving ``token:privatetoken``). Returns ``None`` for anything else.
    """
    if "token=" not in (uri or ""):
        return None
    payload = uri.split("token=", 1)[1]
    for candidate in (urllib.parse.unquote(payload), payload):
        raw = candidate.strip()
        if not raw:
            continue
        padded = raw + "=" * (-len(raw) % 4)
        try:
            decoded = base64.b64decode(padded, validate=False).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
        parts = decoded.split(":")
        if len(parts) >= 3:
            return parts[1], parts[2]
        if len(parts) == 2 and all(parts):
            return parts[0], parts[1]
    return None


def extract_logintoken(html: str) -> str | None:
    """Pull the CSRF ``logintoken`` out of the Moodle login form."""
    match = LOGINTOKEN_RE.search(html or "")
    return match.group(1) if match else None


def login_page_has_error(html: str) -> bool:
    """True when the returned login page carries an error banner."""
    return bool(LOGIN_ERROR_RE.search(html or ""))


def looks_like_login_page(html: str) -> bool:
    """True when the body is Moodle's login screen (title or login form present)."""
    text = html or ""
    return bool(extract_logintoken(text)) or bool(LOGIN_TITLE_RE.search(text))


def plain_text(html: str, limit: int = 120) -> str:
    """Strip tags and collapse whitespace so error messages stay readable."""
    text = TAG_RE.sub(" ", html or "")
    text = " ".join(text.split())
    return text[:limit]


def update_env_token(path: Path, token: str) -> bool:
    """Set ``MOODLE_TOKEN`` in a dotenv file, preserving everything else.

    Replaces the first match, deletes duplicate ``MOODLE_TOKEN`` lines (and their
    indented ``export`` variants), and appends the line when missing.
    """
    path = Path(path)
    existed = path.exists()
    text = path.read_text(encoding="utf-8") if existed else ""
    lines = text.splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        if ENV_TOKEN_RE.match(line):
            if not replaced:
                out.append(f"MOODLE_TOKEN={token}")
                replaced = True
            continue
        out.append(line)
    if not replaced:
        out.append(f"MOODLE_TOKEN={token}")
    new_text = "\n".join(out) + "\n"
    if existed and new_text == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return True


def discover_env_files(root: str | os.PathLike[str]) -> list[Path]:
    """Every ``.env`` under ``root`` that already defines ``MOODLE_TOKEN``.

    Backup/snapshot trees are skipped — writing a fresh token into an archived
    snapshot only corrupts history.
    """
    root_path = Path(root).expanduser()
    if not root_path.exists():
        return []
    found = []
    for candidate in sorted(root_path.rglob(".env")):
        if not candidate.is_file():
            continue
        if any(part in SKIP_DIR_PARTS for part in candidate.parts):
            continue
        try:
            if ENV_TOKEN_RE.search(candidate.read_text(encoding="utf-8", errors="ignore")):
                found.append(candidate)
        except OSError:
            continue
    return found


# --------------------------------------------------------------------------------------
# network helpers
# --------------------------------------------------------------------------------------
def _post(url: str, data: dict[str, Any], timeout: float) -> requests.Response:
    return requests.post(
        url,
        data=data,
        headers={"User-Agent": BROWSER_USER_AGENT},
        timeout=timeout,
    )


def verify_token(site: str, token: str, timeout: float = 30.0) -> tuple[bool, dict[str, Any]]:
    """Call ``core_webservice_get_site_info`` and report whether the token works."""
    try:
        response = _post(
            api_endpoint(site),
            {
                "wstoken": token,
                "wsfunction": "core_webservice_get_site_info",
                "moodlewsrestformat": "json",
            },
            timeout,
        )
    except requests.RequestException as exc:
        return False, {"errorcode": "network", "message": str(exc)}

    try:
        payload = response.json()
    except ValueError:
        return False, {"errorcode": "badresponse", "message": response.text[:200]}

    if isinstance(payload, dict) and ("errorcode" in payload or "exception" in payload):
        return False, {
            "errorcode": payload.get("errorcode", "unknown"),
            "message": payload.get("message", "unknown error"),
        }
    if not isinstance(payload, dict):
        return False, {"errorcode": "badresponse", "message": str(payload)[:200]}

    username = payload.get("username", "?")
    sitename = payload.get("sitename", "?")
    return True, {
        "username": username,
        "sitename": sitename,
        "userid": payload.get("userid"),
        "functions": len(payload.get("functions") or []),
        "message": f"OK: token valid for {username} @ {sitename}",
    }


def token_via_login_token(
    site: str,
    username: str,
    password: str,
    service: str = DEFAULT_SERVICE,
    timeout: float = 30.0,
) -> tuple[str | None, str | None, str | None]:
    """``POST /login/token.php`` — direct token issuance for the mobile service."""
    url = normalize_site_url(site) + LOGIN_TOKEN_PATH
    try:
        response = _post(
            url, {"username": username, "password": password, "service": service}, timeout
        )
    except requests.RequestException as exc:
        return None, None, f"request failed: {exc}"
    try:
        payload = response.json()
    except ValueError:
        return None, None, f"unexpected non-JSON response (HTTP {response.status_code})"
    if payload.get("token"):
        return payload["token"], payload.get("privatetoken"), None
    error = payload.get("error", "no token in response")
    code = payload.get("errorcode")
    return None, None, f"{error} ({code})" if code else error


def login_with_password(
    site: str, username: str, password: str, timeout: float = 30.0
) -> tuple[requests.Session | None, str | None]:
    """Log in to the web UI (session cookie) — needed by the mobile-launch strategy."""
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_USER_AGENT})
    root = normalize_site_url(site)
    try:
        page = session.get(root + LOGIN_PATH, timeout=timeout)
    except requests.RequestException as exc:
        return None, f"cannot reach login page: {exc}"
    logintoken = extract_logintoken(page.text)
    if not logintoken:
        return None, "login form not found (site may use SSO-only authentication)"
    try:
        result = session.post(
            root + LOGIN_PATH,
            data={
                "logintoken": logintoken,
                "username": username,
                "password": password,
                "anchor": "",
            },
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        return None, f"login request failed: {exc}"
    if login_page_has_error(result.text):
        return None, "login rejected (check username/password)"
    if "MoodleSession" not in session.cookies:
        return None, "login did not establish a session"
    return session, None


def token_via_mobile_launch(
    site: str,
    service: str = DEFAULT_SERVICE,
    timeout: float = 30.0,
    session: requests.Session | None = None,
    username: str | None = None,
    password: str | None = None,
    urlscheme: str = DEFAULT_URLSCHEME,
    max_redirects: int = 6,
) -> tuple[str | None, str | None, str | None]:
    """Mimic the official mobile app: hit ``launch.php`` and decode the token redirect."""
    if session is None:
        if not username or password is None:
            return None, None, "login credentials or a browser session cookie are required"
        session, error = login_with_password(site, username, password, timeout)
        if session is None:
            return None, None, error

    root = normalize_site_url(site)
    passport = new_passport()
    url = (
        f"{root}{LAUNCH_PATH}?service={urllib.parse.quote(service)}"
        f"&passport={passport}&urlscheme={urllib.parse.quote(urlscheme)}"
    )
    for _ in range(max_redirects):
        try:
            response = session.get(url, timeout=timeout, allow_redirects=False)
        except requests.RequestException as exc:
            return None, None, f"launch request failed: {exc}"
        if response.status_code not in (301, 302, 303, 307, 308):
            if looks_like_login_page(response.text):
                return None, None, SESSION_ERROR
            body = plain_text(response.text)
            return None, None, f"unexpected response HTTP {response.status_code}: {body}"
        location = response.headers.get("Location", "")
        decoded = decode_launch_uri(location)
        if decoded:
            return decoded[0], decoded[1], None
        if not location:
            return None, None, "redirect without Location header"
        if "/login/" in urllib.parse.urlsplit(location).path:
            return None, None, SESSION_ERROR
        url = urllib.parse.urljoin(url, location)

    return None, None, "no token redirect after following redirects"


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="get_moodle_token.py",
        description="Fetch a Moodle web service token without user/managetoken.php.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--site", default=os.getenv("MOODLE_URL", ""),
                        help="site root or .../webservice/rest/server.php (default: $MOODLE_URL)")
    parser.add_argument("--method", choices=["auto", "login-token", "mobile-launch", "cookie"],
                        default="auto", help="strategy to use (default: auto)")
    parser.add_argument("--username", default=os.getenv("MOODLE_USERNAME", ""),
                        help="Moodle username (default: $MOODLE_USERNAME)")
    parser.add_argument("--password", default=None,
                        help="Moodle password; omit to be prompted (never echoed, never logged)")
    parser.add_argument("--password-stdin", action="store_true",
                        help="read the password from stdin instead of prompting")
    parser.add_argument("--cookie", default=os.getenv("MOODLE_SESSION_COOKIE", ""),
                        help="MoodleSession cookie value copied from your browser")
    parser.add_argument("--service", default=os.getenv("MOODLE_SERVICE", DEFAULT_SERVICE),
                        help=f"web service shortname (default: {DEFAULT_SERVICE})")
    parser.add_argument("--verify", metavar="TOKEN", default=None,
                        help="only verify this token and exit")
    parser.add_argument("--write-env", action="append", default=[], metavar="PATH",
                        help="write the token into this dotenv file (repeatable)")
    parser.add_argument("--hermes", action="store_true",
                        help="write the token into every ~/.hermes**/.env holding MOODLE_TOKEN")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds")
    parser.add_argument("--quiet", action="store_true", help="print only the token")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def _emit(args: argparse.Namespace, payload: dict[str, Any], token: str | None) -> None:
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    if args.quiet and token:
        print(token)
        return
    for key, value in payload.items():
        if key == "token":
            continue
        print(f"{key}: {value}")
    if token:
        print(f"token: {token}")


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)

    if not args.site:
        print("error: --site is required (or set MOODLE_URL)", file=sys.stderr)
        return 2

    try:
        site = normalize_site_url(args.site)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.verify:
        ok, info = verify_token(site, args.verify, timeout=args.timeout)
        if args.json:
            print(json.dumps({"site": site, "ok": ok, "fingerprint": fingerprint(args.verify), **info},
                             indent=2, ensure_ascii=False))
        else:
            print(f"site: {site}")
            print(f"token: {fingerprint(args.verify)}")
            print(info["message"] if ok else f"FAILED [{info.get('errorcode')}]: {info.get('message')}")
        return 0 if ok else 1

    if args.password is None and not args.cookie:
        if args.password_stdin:
            args.password = sys.stdin.readline().rstrip("\n")
        else:
            args.password = getpass.getpass(f"Moodle password for {args.username or 'user'}: ")

    session: requests.Session | None = None
    if args.cookie:
        session = requests.Session()
        session.headers.update({"User-Agent": BROWSER_USER_AGENT})
        session.cookies.set("MoodleSession", args.cookie, domain=urllib.parse.urlsplit(site).hostname)

    token: str | None = None
    private: str | None = None
    errors: list[str] = []

    methods = [args.method] if args.method != "auto" else ["login-token", "mobile-launch"]
    if args.cookie:
        methods = ["mobile-launch"]

    for method in methods:
        if args.quiet is False:
            print(f"# strategy: {method}", file=sys.stderr)
        if method == "login-token":
            if not args.username or args.password is None:
                errors.append("login-token: username and password required")
                continue
            token, private, error = token_via_login_token(
                site, args.username, args.password, args.service, args.timeout
            )
            if error:
                errors.append(f"login-token: {error}")
                continue
        elif method in ("mobile-launch", "cookie"):
            token, private, error = token_via_mobile_launch(
                site, args.service, args.timeout, session=session,
                username=args.username, password=args.password,
            )
            if error:
                errors.append(f"mobile-launch: {error}")
                continue
        if token:
            break

    if not token:
        payload = {"site": site, "ok": False, "errors": errors or ["unknown failure"]}
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print("FAILED to obtain a token:", file=sys.stderr)
            for item in payload["errors"]:
                print(f"  - {item}", file=sys.stderr)
        return 1

    ok, info = verify_token(site, token, timeout=args.timeout)
    written: list[str] = []
    if ok:
        targets = [Path(p).expanduser() for p in args.write_env]
        if args.hermes:
            targets.extend(discover_env_files(Path.home() / ".hermes"))
        for target in dict.fromkeys(targets):
            if update_env_token(target, token):
                written.append(str(target))

    payload = {
        "site": site,
        "ok": ok,
        "service": args.service,
        "token": token,
        "token_fingerprint": fingerprint(token),
        "privatetoken": fingerprint(private) if private else None,
        "verification": info["message"] if ok else f"FAILED [{info.get('errorcode')}]: {info.get('message')}",
        "env_files_updated": written,
    }
    if not args.quiet:
        payload["next_step"] = "restart the gateway(s) so the MCP server picks up the new token"

    _emit(args, payload, token)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
