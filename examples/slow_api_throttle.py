import asyncio
import typing as t
from datetime import timedelta
from http import HTTPStatus

from rich import print
from rich.live import Live

from gracy import BaseEndpoint, GracefulRetry, GracefulThrottle, Gracy, GracyConfig, LogEvent, LogLevel, ThrottleRule

RETRY = GracefulRetry(
    delay=0,  # Force throttling to work
    max_attempts=3,
    retry_on=None,
    log_after=None,  # LogEvent(LogLevel.WARNING),
    log_exhausted=None,  # LogEvent(LogLevel.CRITICAL),
    behavior="pass",
)

THROTTLE_RULE = ThrottleRule(r".*", 1, timedelta(seconds=1))


class HttpBinEndpoint(BaseEndpoint):
    DELAY = "/delay/{DELAY}"


class HttpBinAPI(Gracy[HttpBinEndpoint]):
    class Config:  # type: ignore
        BASE_URL = "https://httpbin.org/"
        SETTINGS = GracyConfig(
            strict_status_code={HTTPStatus.OK},
            retry=RETRY,
            throttling=GracefulThrottle(
                rules=THROTTLE_RULE,
                log_limit_reached=LogEvent(LogLevel.ERROR),
                log_wait_over=LogEvent(LogLevel.WARNING),
            ),
        )

    async def get_slow(self, delay: int):
        await self.get(HttpBinEndpoint.DELAY, {"DELAY": str(delay)})


httpbin = HttpBinAPI()

TOO_SLOW: t.Final = 15
QUITE_SLOW: t.Final = 10
SOMEWHAT_SLOW: t.Final = 5
OK: t.Final = 2
FAST: t.Final = 1


async def main():
    delays_sequence = [
        TOO_SLOW,
        TOO_SLOW,
        TOO_SLOW,
        FAST,
        FAST,
        FAST,
        OK,
        OK,
        SOMEWHAT_SLOW,
        QUITE_SLOW,
        OK,
        OK,
    ]
    print(f"Will hit HTTPBin concurrently - {str(THROTTLE_RULE)}")

    try:
        throttler = httpbin._throttle_controller  # type: ignore

        with Live(throttler.debug_get_table()) as live:
            # def live_print_limit_reached(r: httpx.Response | None) -> str:
            #     live.console.print("THROTTLING HIT")
            #     return ""
            # httpbin.Config.SETTINGS.throttling.log_limit_reached = LogEvent(LogLevel.NOTSET, live_print_limit_reached)

            reqs = [asyncio.create_task(httpbin.get_slow(delay)) for delay in delays_sequence]

            for coro in asyncio.as_completed(reqs):
                await coro
                live.update(throttler.debug_get_table())

    finally:
        httpbin.report_status("rich")


asyncio.run(main())
