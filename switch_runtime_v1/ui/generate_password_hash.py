#!/usr/bin/env python3
from __future__ import annotations

import getpass
import hashlib
import secrets


def _pbkdf2_sha256(password: str, salt: str, iterations: int) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return digest.hex()


def main() -> int:
    print("Generate SWITCH_UI_PASSWORD_HASH (format: pbkdf2_sha256$iters$salt$hex)")
    iterations = 210_000
    salt = secrets.token_hex(16)
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm : ")
    if password != confirm:
        print("Error: passwords do not match")
        return 1
    digest = _pbkdf2_sha256(password, salt, iterations)
    out = f"pbkdf2_sha256${iterations}${salt}${digest}"
    print("\nUse this in env:")
    print(f"SWITCH_UI_PASSWORD_HASH={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
