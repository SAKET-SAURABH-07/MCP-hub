"""Get a demo access token from the local Keycloak (password grant, DEV ONLY).

python scripts/get_token.py alice   # viewer
python scripts/get_token.py bob     # developer
python scripts/get_token.py carol   # admin
"""

from __future__ import annotations

import sys

import httpx

BASE = "http://localhost:8080/realms/mcp-hub/protocol/openid-connect/token"
PASSWORDS = {"alice": "alice-pw", "bob": "bob-pw", "carol": "carol-pw"}


def main() -> None:
    user = sys.argv[1] if len(sys.argv) > 1 else "alice"
    if user not in PASSWORDS:
        raise SystemExit(f"user must be one of {sorted(PASSWORDS)}")
    r = httpx.post(
        BASE,
        data={
            "grant_type": "password",
            "client_id": "mcp-client",
            "username": user,
            "password": PASSWORDS[user],
        },
        timeout=10,
    )
    r.raise_for_status()
    print(r.json()["access_token"])


if __name__ == "__main__":
    main()
