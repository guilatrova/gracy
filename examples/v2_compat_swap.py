"""The one-line swap - a script written for `requests`, running on gracy.

Before:  import requests
After:   from gracy.compat import requests

Everything below the import is untouched requests code. configure() then
upgrades every call site with retry + throttling without editing them.
"""

from __future__ import annotations

# import requests                                   # <- before
from gracy.compat import requests  # noqa: E402     # <- after (the one line)

r = requests.get("https://pokeapi.co/api/v2/pokemon/ditto", timeout=10)
print(r.status_code, r.ok, r.json()["name"], r.headers.get("content-type"))
r.raise_for_status()

with requests.Session() as s:
    s.headers.update({"user-agent": "gracy-compat-demo"})
    print(s.get("https://pokeapi.co/api/v2/pokemon/mew", timeout=10).json()["id"])

# --- the upgrade: same call sites, now with retry + rate limiting -----------
import gracy  # noqa: E402
from gracy import GracyConfig, Rate, Retry, Throttle  # noqa: E402

requests.configure(
    GracyConfig(
        retry=Retry(on=gracy.status(429, 502, 503), attempts=3, wait=0.3),
        throttle=Throttle(rules=[Rate(3, per="1s")]),
    )
)

for name in ("pikachu", "charmander", "bulbasaur", "squirtle"):
    print(name, requests.get(f"https://pokeapi.co/api/v2/pokemon/{name}", timeout=10).status_code)

requests.shutdown()
print("swap OK")
