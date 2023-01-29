# Todo
- [x] Strict status code
- [x] Allowed status code
- [x] Retry
- [x] Retry but pass
- [x] Metrics (% of successful calls)
  - [x] Status codes
  - [x] % per status code
  - [x] avg ms taken to execute
- [x] Parsing
  - [x] default = lambda r: r.json()
  - [x] 200 = lambda r: r.text()
  - [x] 404 = None
  - [x] 401 = InadequatePermissions
- [x] Throttle
  - [x] URL regex support
- [ ] Authorization
  - [ ] Validate if token is still valid
  - [ ] Auto refresh
- [ ] Docs
  - [ ] Methods without `_`
---

Implement custom exception + log per status
