"""ULID identifiers for server rows."""

from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTUVWXYZ"
_TIME_BITS = 48
_RANDOM_BITS = 80


def new_ulid() -> str:
    """Return a new 26-character Crockford ULID.

    Returns:
        An uppercase ULID. The first characters encode the current time in
        milliseconds and the rest are random.

    Example:
        ```python
        from tessera_server.ids import new_ulid

        assert len(new_ulid()) == 26
        ```
    """
    timestamp = int(time.time() * 1000) & ((1 << _TIME_BITS) - 1)
    value = (timestamp << _RANDOM_BITS) | secrets.randbits(_RANDOM_BITS)
    chars = ["0"] * 26
    for index in range(25, -1, -1):
        chars[index] = _ALPHABET[value & 31]
        value >>= 5
    return "".join(chars)


__all__ = ["new_ulid"]
