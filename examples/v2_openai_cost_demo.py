"""Gracy 2.0 live cost gauge demo - OpenAI chat completions, ZERO credits spent.

Terminal A:  python examples/v2_openai_cost_demo.py
Terminal B:  python -m gracy.monitor

A MockTransport answers every request with a real-shape ``chat.completion``
payload (the exact JSON the OpenAI API returns, ``usage`` block included), so
no API key is needed and no credits are spent. An after-hook reads ``usage``
from each response, aggregates prompt vs completion tokens across the whole
session, prices them (arbitrary demo rates - swap in your model's real ones)
and publishes a SINGLE live gauge line on the monitor via a keyed message:

    14:03:52 • openai: 1,234 in / 567 out tok · est. $0.0123

Because the message uses ``key="openai-cost"``, every update REPLACES the
previous line instead of appending - the history never fills up with
intermediate totals, and the gauge escalates to warn/error as spend grows.
"""

from __future__ import annotations

import asyncio
import random
import time
import typing as t

import gracy
from gracy import Body, Gracy, post
from gracy.testing import MockTransport

RUN_FOR_S = 45.0

# Arbitrary demo pricing (USD per 1M tokens) - the user brings real rates.
PRICE_IN_PER_1M = 2.50
PRICE_OUT_PER_1M = 10.00
WARN_AT_USD = 0.05  # gauge turns yellow here...
ERROR_AT_USD = 0.12  # ...and red here


def fake_completion(spec: t.Any) -> dict[str, t.Any]:
    """Real-shape OpenAI chat.completion response with wandering usage numbers
    so the gauge visibly climbs."""
    prompt = random.randint(200, 1200)
    completion = random.randint(50, 800)
    return {
        "id": f"chatcmpl-demo{random.randint(10**8, 10**9 - 1)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Mocked answer - no credits were harmed.",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


class OpenAIChat(Gracy):
    base_url = "https://api.openai.com"

    # Session-wide aggregates, owned by the after-hook (one instance per run).
    prompt_tokens = 0
    completion_tokens = 0

    @post("/v1/chat/completions")
    async def chat(self, payload: t.Annotated[dict, Body]) -> dict: ...

    async def after(self, context: t.Any, result: t.Any, retry_state: t.Any) -> None:
        """Aggregate token usage from every successful response and surface the
        running cost as one keyed gauge on the live monitor."""
        if not (isinstance(result, gracy.Response) and result.is_success):
            return
        usage = result.json().get("usage") or {}
        cls = type(self)
        cls.prompt_tokens += int(usage.get("prompt_tokens", 0))
        cls.completion_tokens += int(usage.get("completion_tokens", 0))
        cost = (
            cls.prompt_tokens * PRICE_IN_PER_1M + cls.completion_tokens * PRICE_OUT_PER_1M
        ) / 1_000_000
        level = "error" if cost >= ERROR_AT_USD else ("warn" if cost >= WARN_AT_USD else "info")
        self.message(
            f"openai: {cls.prompt_tokens:,} in / {cls.completion_tokens:,} out tok"
            f" · est. ${cost:.4f}",
            level=level,
            key="openai-cost",
        )


BANNER = """
=========================================================================
  GRACY OPENAI COST GAUGE DEMO (mocked - zero credits, no API key)

  >>> Open a SECOND terminal and run:

      python -m gracy.monitor

  Watch the MESSAGES panel: the "openai: ... est. $" line is ONE keyed
  gauge updated in place by an after-hook, turning yellow past ${warn}
  and red past ${error}. Ctrl+C stops the demo cleanly.
=========================================================================
"""


async def main() -> None:
    transport = MockTransport(
        {"POST https://api.openai.com/v1/chat/completions": fake_completion}
    )
    print(BANNER.format(warn=WARN_AT_USD, error=ERROR_AT_USD), flush=True)

    async with OpenAIChat(transport=transport, monitor=True) as api:
        api.message("openai cost demo started - watch the $ gauge climb")
        deadline = time.monotonic() + RUN_FOR_S
        turn = 0
        while time.monotonic() < deadline:
            turn += 1
            await asyncio.gather(
                *(
                    api.chat(
                        payload={
                            "model": "gpt-4o-mini",
                            "messages": [{"role": "user", "content": f"question #{turn}-{i}"}],
                        }
                    )
                    for i in range(random.randint(2, 6))
                )
            )
            print(
                f"turn {turn:>2}: {OpenAIChat.prompt_tokens:,} in"
                f" / {OpenAIChat.completion_tokens:,} out tokens so far",
                flush=True,
            )
            await asyncio.sleep(1.0)
        api.message("openai cost demo finished")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
