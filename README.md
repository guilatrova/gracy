<p align="center">
    <img src="https://raw.githubusercontent.com/guilatrova/gracy/main/img/logo.png">
</p>

<h2 align="center">Gracefully manage your API interactions</h2>

<p align="center">
  <!-- CI --><a href="https://github.com/guilatrova/gracy/actions"><img alt="Actions Status" src="https://github.com/guilatrova/gracy/workflows/CI/badge.svg"></a>
  <!-- PyPI --><a href="https://pypi.org/project/gracy/"><img alt="PyPI" src="https://img.shields.io/pypi/v/gracy"/></a>
  <!-- Supported Python versions --><img src="https://badgen.net/pypi/python/gracy" />
  <!-- Alternative Python versioning: <img alt="python version" src="https://img.shields.io/badge/python-3.9%20%7C%203.10-blue"> -->
  <!-- LICENSE --><a href="https://github.com/guilatrova/gracy/blob/main/LICENSE"><img alt="GitHub" src="https://img.shields.io/github/license/guilatrova/gracy"/></a>
  <!-- PyPI downloads --><a href="https://pepy.tech/project/gracy/"><img alt="Downloads" src="https://static.pepy.tech/personalized-badge/gracy?period=total&units=international_system&left_color=grey&right_color=blue&left_text=%F0%9F%A6%96%20Downloads"/></a>
  <!-- Formatting --><a href="https://github.com/psf/black"><img alt="Code style: black" src="https://img.shields.io/badge/code%20style-black-000000.svg"/></a>
  <!-- Tryceratops --><a href="https://github.com/guilatrova/tryceratops"><img alt="try/except style: tryceratops" src="https://img.shields.io/badge/try%2Fexcept%20style-tryceratops%20%F0%9F%A6%96%E2%9C%A8-black" /></a>
  <!-- Typing --><a href="https://github.com/python/mypy"><img alt="Types: mypy" src="https://img.shields.io/badge/types-mypy-blue.svg"/></a>
  <!-- Follow handle --><a href="https://twitter.com/intent/user?screen_name=guilatrova"><img alt="Follow guilatrova" src="https://img.shields.io/twitter/follow/guilatrova?style=social"/></a>
  <!-- Sponsor --><a href="https://github.com/sponsors/guilatrova"><img alt="Sponsor guilatrova" src="https://img.shields.io/github/sponsors/guilatrova?logo=GitHub%20Sponsors&style=social"/></a>
</p>

Gracy helps you handle failures, logging, retries, throttling, and tracking for all your HTTP interactions. Gracy uses [httpx](https://github.com/encode/httpx) under the hood.

> "Let Gracy do the boring stuff while you focus on your application"

---

**Summary**

- [Get started](#get-started)
  - [Installation](#installation)
  - [Usage](#usage)
    - [Simple example](#simple-example)
    - [Strict/Allowed status code](#strictallowed-status-code)
    - [Parsing](#parsing)
    - [Retry](#retry)
    - [Throttling](#throttling)
    - [Logging](#logging)
    - [Reports](#reports)
    - [Overriding request configs per method](#overriding-request-configs-per-method)


## Get started

### Installation

```
pip install gracy
```

OR

```
poetry add gracy
```

### Usage

Example will use the [PokeAPI](https://pokeapi.co).

#### Simple example

```py
# 0. Import
import asyncio
from typing import Awaitable
from gracy import BaseEndpoint, Gracy, GracyConfig, LogEvent, LogLevel

# 1. Define your endpoints
class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}" # 👈 Put placeholders as needed

# 2. Define your Graceful API
class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:  # type: ignore
        BASE_URL = "https://pokeapi.co/api/v2/" # 👈 Optional BASE_URL
        # 👇 Define settings to apply for every request
        SETTINGS = GracyConfig(
          log_request=LogEvent(LogLevel.DEBUG),
          log_response=LogEvent(LogLevel.INFO, "{URL} took {ELAPSED}"),
          parser={
            "default": lambda r: r.json()
          }
        )

    async def get_pokemon(self, name: str) -> Awaitable[dict]:
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

pokeapi = GracefulPokeAPI()

async def main():
    try:
      pokemon = await pokeapi.get_pokemon("pikachu")
      print(pokemon)

    finally:
        pokeapi.report_status()


asyncio.run(main())
```

#### Strict/Allowed status code

#### Parsing

#### Retry

#### Throttling


#### Logging

You can **define and customize logs** for events by using `LogEvent` and `LogLevel`:

```py
verbose_log = LogEvent(LogLevel.CRITICAL)
custom_warn_log = LogEvent(LogLevel.WARNING. custom_message="{METHOD} {URL} is quite slow and flaky")
custom_error_log = LogEvent(LogLevel.INFO, custom_message="{URL} returned a bad status code {STATUS}, but that's fine")
```

Note that placeholders are formatted and replaced later on by Gracy based on the event type, like:

**Placeholders per event**

| Placeholder        | Description                                                                     | Example                                 | Supported Events                                                                                                                                                                                                                                |
| ------------------ | ------------------------------------------------------------------------------- | --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `{URL}`            | Endpoint being targetted. Sometimes formatted, sometimes containg placeholders. | `/pokemon/{NAME}`, `/pokemon/{PIKACHU}` | <ul><li>[x] Before request</li><li>[x] After response</li><li>[x] Response error</li><li>[x] Before retry</li><li>[x] After retry</li><li>[x] Retry exhausted</li><li>[x] Throttle limit reached</li><li>[x] Throttle limit available</li></ul> |
| `{METHOD}`         | HTTP Request being used                                                         | `GET`, `POST`                           | <ul><li>[x] Before request</li><li>[x] After response</li><li>[x] Response error</li><li>[ ] Before retry</li><li>[ ] After retry</li><li>[ ] Retry exhausted</li><li>[ ] Throttle limit reached</li><li>[ ] Throttle limit available</li></ul> |
| `{STATUS}`         | Status code returned by the response                                            | `200`, `404`, `501`                     | <ul><li>[ ] Before request</li><li>[x] After response</li><li>[x] Response error</li><li>[ ] Before retry</li><li>[ ] After retry</li><li>[ ] Retry exhausted</li><li>[ ] Throttle limit reached</li><li>[ ] Throttle limit available</li></ul> |
| `{ELAPSED}`        | Amount of seconds taken for the request to complete                             | *Numeric*                               | <ul><li>[x] Before request</li><li>[x] After response</li><li>[x] Response error</li><li>[ ] Before retry</li><li>[ ] After retry</li><li>[ ] Retry exhausted</li><li>[ ] Throttle limit reached</li><li>[ ] Throttle limit available</li></ul> |
| `{RETRY_DELAY}`    | How long Gracy will wait before repeating the request                           | *Numeric*                               | *Any Retry event*                                                                                                                                                                                                                               |
| `{CUR_ATTEMPT}`    | Current attempt count for the current request                                   | *Numeric*                               | *Any Retry event*                                                                                                                                                                                                                               |
| `{MAX_ATTEMPT}`    | Max attempt defined for the current request                                     | *Numeric*                               | *Any Retry event*                                                                                                                                                                                                                               |
| `{THROTTLE_LIMIT}` | How many reqs/s is defined for the current request                              | *Numeric*                               | *Any Throttle event*                                                                                                                                                                                                                            |
| `{THROTTLE_TIME}`  | How long Gracy will wait before calling the request                             | *Numeric*                               | *Any Throttle event*                                                                                                                                                                                                                            |

and you can set up the log events as follows:

**Requests**

1. Before request
2. After response
3. Response has non successful errors

```py
GracyConfig(
  log_request=LogEvent(),
  log_response=LogEvent(),
  log_errors=LogEvent(),
)
```

**Retry**

1. Before retry
2. After retry
3. When retry exhausted

```py
GracefulRetry(
  ...,
  log_before=LogEvent(),
  log_after=LogEvent(),
  log_exhausted=LogEvent(),
)
```

**Throttling**

1. When reqs/s limit is reached
2. When limit decreases again

```py
GracefulThrottle(
  ...,
  log_limit_reached=LogEvent()
  log_wait_over=LogEvent()
)
```

#### Reports

#### Overriding request configs per method
