# Todo
- [x] Strict status code
- [x] Allowed status code
- [x] Retry
- [ ] Retry but pass
- [x] Metrics (% of successful calls)
  - [x] Status codes
  - [x] % per status code
  - [x] avg ms taken to execute
- [] Throttle
  - [] URL regex support
  - [] throttle=[(200, 10), (404, 20)]
- [x] Parsing
  - [x] default = lambda r: r.json()
  - [x] 200 = lambda r: r.text()
  - [x] 404 = None
  - [x] 401 = InadequatePermissions
---

Implement custom exception + log per status

```py
        try:
            async with self._session.request(
                method=method,
                url=endpoint,
                headers=headers,
                params=params,
                data=data,
            ) as r:
                r_json = await r.json()
        except ClientResponseError as e:
            match e.status:
                case HTTPStatus.UNAUTHORIZED:
                    raise InadequatePermissions(self.REQUIRED_PERMISSIONS) from e
                case HTTPStatus.NOT_FOUND:
                    return None
                case HTTPStatus.TOO_MANY_REQUESTS | HTTPStatus.SERVICE_UNAVAILABLE:
                    logger.debug(f"429 Error at {endpoint} waiting {retry_wait_time}(s) before retrying")
                case _:
                    raise
```
