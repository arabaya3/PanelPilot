"""Check the refresh-session cookie and the cross-site refusal over real HTTPS.

Starts the API with ENVIRONMENT=staging (so the cookie is Secure and named
``__Host-pp_refresh``) behind uvicorn's own TLS, with a throwaway certificate
authority from ``trustme``, and drives it with a client that trusts only that
authority. Prints one PASS/FAIL line per check and exits non-zero on any
failure.

Run inside the api container, against the DISPOSABLE database only:

    docker compose run --rm --no-deps -T \
      -v "<repo>/docs:/docs" --entrypoint sh api -c \
      "env -i PATH=/opt/venv/bin:/usr/bin:/bin HOME=/tmp \
       DATABASE_URL=postgresql+psycopg://panelpilot:panelpilot@postgres:5432/panelpilot_phase1_backend \
       python /docs/design-readiness/phase-1/https-check/check_https.py"

It refuses any other database. The account it creates is deleted at the end.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import trustme
from sqlalchemy import create_engine, text

DISPOSABLE = "panelpilot_phase1_backend"
WEB_ORIGIN = "https://app.panelpilot.test"
PREFIX = "httpscheck-"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def attributes(header: str) -> tuple[str, dict[str, str]]:
    name_value, *rest = [part.strip() for part in header.split(";")]
    parsed = {}
    for part in rest:
        key, _, value = part.partition("=")
        parsed[key.lower()] = value
    return name_value, parsed


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.endswith(f"/{DISPOSABLE}"):
        print(f"refusing to run against {database_url!r}: only {DISPOSABLE} is allowed")
        return 2

    ca = trustme.CA()
    server_cert = ca.issue_cert("127.0.0.1", "localhost")
    work = Path(tempfile.mkdtemp())
    key_file = work / "server.key"
    cert_file = work / "server.pem"
    ca_file = work / "ca.pem"
    server_cert.private_key_pem.write_to_path(str(key_file))
    server_cert.cert_chain_pems[0].write_to_path(str(cert_file))
    ca.cert_pem.write_to_path(str(ca_file))

    port = free_port()
    env = {
        "PATH": os.environ.get("PATH", "/opt/venv/bin:/usr/bin:/bin"),
        "HOME": "/tmp",
        "ENVIRONMENT": "staging",
        "DATABASE_URL": database_url,
        "OPENSEARCH_URL": "http://127.0.0.1:9",
        "REDIS_URL": "redis://127.0.0.1:9/0",
        "RATE_LIMIT_BACKEND": "memory",
        "JWT_SECRET": "https-check-throwaway-secret-" + uuid.uuid4().hex,
        "OPENAI_API_KEY": "test-key",
        "ANTHROPIC_API_KEY": "test-key",
        "WEB_ORIGINS": WEB_ORIGIN,
    }
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--ssl-keyfile",
            str(key_file),
            "--ssl-certfile",
            str(cert_file),
            "--log-level",
            "warning",
        ],
        cwd="/app",
        env=env,
    )
    base = f"https://127.0.0.1:{port}"
    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"{'PASS' if ok else 'FAIL'}  {label}{'  -- ' + detail if detail else ''}")
        if not ok:
            failures += 1

    email = f"{PREFIX}{uuid.uuid4().hex[:8]}@example.com"
    try:
        with httpx.Client(base_url=base, verify=str(ca_file), timeout=10) as client:
            for _ in range(100):
                try:
                    client.get("/api/v1/health/live")
                    break
                except httpx.TransportError:
                    time.sleep(0.2)
            print(f"API: {base} (ENVIRONMENT=staging, TLS from a throwaway CA)")
            print(f"WEB_ORIGINS={WEB_ORIGIN}")

            signup = client.post(
                "/api/v1/auth/signup", json={"email": email, "password": "correct horse battery"}
            )
            check("signup over HTTPS answers 201", signup.status_code == 201, str(signup.status_code))
            header = signup.headers.get("set-cookie", "")
            print(f"Set-Cookie (value elided): {header.split('=', 1)[0]}=<token>;"
                  f"{header.split(';', 1)[1] if ';' in header else ''}")
            name_value, attrs = attributes(header)
            check("cookie is named __Host-pp_refresh", name_value.startswith("__Host-pp_refresh="))
            check("cookie is Secure", "secure" in attrs)
            check("cookie is HttpOnly", "httponly" in attrs)
            check("cookie is SameSite=Strict", attrs.get("samesite", "").lower() == "strict")
            check("cookie Path=/", attrs.get("path") == "/")
            check("cookie has no Domain", "domain" not in attrs)
            check("body carries no refresh token", "refresh_token" not in signup.json())
            access = signup.json()["access_token"]
            cookie = {"__Host-pp_refresh": name_value.split("=", 1)[1]}

            def refresh(headers: dict[str, str]) -> httpx.Response:
                return client.post(
                    "/api/v1/auth/session/refresh", headers=headers, cookies=cookie
                )

            refused = refresh({"Origin": "https://evil.example", "X-PanelPilot-CSRF": "1"})
            check(
                "untrusted origin refused (403 csrf_origin_refused)",
                refused.status_code == 403 and refused.json().get("code") == "csrf_origin_refused",
                str(refused.status_code),
            )
            opaque = refresh({"Origin": "null", "X-PanelPilot-CSRF": "1"})
            check(
                "null origin refused (403 csrf_origin_refused)",
                opaque.status_code == 403 and opaque.json().get("code") == "csrf_origin_refused",
                str(opaque.status_code),
            )
            bare = refresh({"Origin": WEB_ORIGIN})
            check(
                "missing CSRF header refused (403 csrf_header_missing)",
                bare.status_code == 403 and bare.json().get("code") == "csrf_header_missing",
                str(bare.status_code),
            )
            trusted = refresh({"Origin": WEB_ORIGIN, "X-PanelPilot-CSRF": "1"})
            check("trusted origin refreshes (200)", trusted.status_code == 200, str(trusted.status_code))
            rotated_name, _ = attributes(trusted.headers.get("set-cookie", ""))
            cookie = {"__Host-pp_refresh": rotated_name.split("=", 1)[1]}
            access = trusted.json().get("access_token", access)

            logout = client.post(
                "/api/v1/auth/session/logout",
                headers={
                    "Origin": WEB_ORIGIN,
                    "X-PanelPilot-CSRF": "1",
                    "Authorization": f"Bearer {access}",
                },
                cookies=cookie,
            )
            check("logout answers 204", logout.status_code == 204, str(logout.status_code))
            clear = logout.headers.get("set-cookie", "")
            print(f"Logout Set-Cookie: {clear}")
            clear_name, clear_attrs = attributes(clear)
            check(
                "logout clears __Host-pp_refresh with the same attributes",
                clear_name.startswith("__Host-pp_refresh=")
                and clear_attrs.get("max-age") == "0"
                and "secure" in clear_attrs
                and "httponly" in clear_attrs
                and clear_attrs.get("samesite", "").lower() == "strict"
                and clear_attrs.get("path") == "/"
                and "domain" not in clear_attrs,
            )
            after = refresh({"Origin": WEB_ORIGIN, "X-PanelPilot-CSRF": "1"})
            check("the cookie no longer refreshes after logout (401)", after.status_code == 401)
    finally:
        server.terminate()
        server.wait(timeout=10)
        engine = create_engine(database_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM refresh_tokens WHERE user_id IN "
                    "(SELECT id FROM users WHERE email LIKE :p)"
                ),
                {"p": f"{PREFIX}%"},
            )
            tenants = [
                row[0]
                for row in conn.execute(
                    text("SELECT tenant_id FROM users WHERE email LIKE :p"), {"p": f"{PREFIX}%"}
                )
            ]
            conn.execute(text("DELETE FROM users WHERE email LIKE :p"), {"p": f"{PREFIX}%"})
            for tenant in tenants:
                conn.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant})

    print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
