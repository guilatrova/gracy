from __future__ import annotations

import asyncio
import typing as t

import httpx

from gracy import Gracy


class GracefulHttpbin(Gracy[str]):
    class Config:  # type: ignore
        BASE_URL = "https://httpbin.org/"

    async def post_json_example(self):
        res = await self.post("post", None, json={"test": "json"}, headers={"header1": "1"})
        return res

    async def post_data_example(self):
        res = await self.post("post", None, data="data", headers={"header2": "2"})
        return res


async def main():
    api = GracefulHttpbin()

    json_res = t.cast(httpx.Response, await api.post_json_example())
    data_res = t.cast(httpx.Response, await api.post_data_example())

    print(json_res.json())
    print("-" * 100)
    print(data_res.json())


if __name__ == "__main__":
    asyncio.run(main())
