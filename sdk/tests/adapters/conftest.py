"""Make `httpx` resolve to the installed HTTP client before tests import it."""

import httpx2

httpx2.alias_httpx()
