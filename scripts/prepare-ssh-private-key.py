#!/usr/bin/env python3
# ---
# relationships:
#   implements: package-repository-publishing
# ---

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    destination = Path(sys.argv[1])
    key_material = sys.stdin.buffer.read()
    if not key_material:
        print("AUR private key material is empty.", file=sys.stderr)
        return 1

    normalized = (
        key_material.replace(b"\r\n", b"\n").replace(b"\r", b"\n").rstrip(b"\n")
        + b"\n"
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as private_key:
            descriptor = None
            private_key.write(normalized)
    except OSError:
        print("Unable to prepare AUR private key file.", file=sys.stderr)
        return 1
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
