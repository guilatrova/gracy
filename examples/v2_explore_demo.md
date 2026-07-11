# `gracy explore` - explore an API, walk away with a typed client

A hands-on walkthrough of the interactive explorer. Every request you fire is
executed through the real gracy pipeline, recorded into a session file, and
mined for types - then `save` compiles the whole session into a typed client
plus tests that pass offline.

## 1. Human REPL flow

```
$ gracy explore https://pokeapi.co/api/v2
⚡ gracy explore · session: pokeapi.gracy.json · base: https://pokeapi.co/api/v2
type `help` for commands, Ctrl-D to quit

gracy› get /pokemon/pikachu
GET https://pokeapi.co/api/v2/pokemon/pikachu -> 200 (81 ms)
{ "id": 25, "name": "pikachu", "height": 4, "weight": 60, ... }
✨ tip: `name <endpoint>` to save this request as an endpoint

gracy› name get_pokemon
endpoint 'get_pokemon': /pokemon/pikachu

gracy› get /pokemon/mew
GET .../pokemon/mew -> 200 (78 ms)

gracy› name get_pokemon
endpoint 'get_pokemon': /pokemon/{pokemon}
✨ params: {pokemon} (segment 1) - rename with `param <index> as <name>`

gracy› param 1 as name
get_pokemon: param 1 -> {name}

gracy› get /pokemon/notreal999
GET .../pokemon/notreal999 -> 404 (120 ms)
✨ one segment differs from get_pokemon - folds into /pokemon/{name}

gracy› on 404 none
get_pokemon: on 404 -> none

gracy› model Pokemon
response model named 'Pokemon'

gracy› show class
class PokeGracy(Gracy):
    base_url = "https://pokeapi.co/api/v2"

    config = GracyConfig(
        decoder=PydanticDecoder(),
    )

    @get("/pokemon/{name}", on={404: None})
    async def get_pokemon(self, name: t.Annotated[str, Path]) -> Pokemon | None: ...

gracy› save pokeapi.py --tests
wrote pokeapi.py, test_pokeapi.py, pokeapi.cassette.db

gracy› quit
```

`pokeapi.py` is a ready-to-use typed client; `test_pokeapi.py` replays the
recorded responses from `pokeapi.cassette.db` - **it passes with no network**.

## 2. Bodies: POST / PUT / PATCH (httpie-style)

```
gracy› post /battle name=pikachu level:=25 moves:='["thunder","quick-attack"]'
POST .../battle -> 201 (89 ms)
{ "id": 731, "name": "pikachu", "level": 25, ... }
✨ body inferred -> model CreateBattleRequest

gracy› name create_battle
gracy› on 422 raise InvalidBattle

gracy› put  /battle/731 @battle.json          # body read from a file
gracy› patch /battle/731 level:=26            # partial update
gracy› post /battle {"name": "mew", "level": 99}   # inline JSON also works
```

Body value syntax:

| syntax | meaning | example |
|---|---|---|
| `k==v` | query string | `limit==20` |
| `k=v` | body field, string | `name=pikachu` |
| `k:=v` | body field, raw JSON | `level:=25` · `moves:=[1,2]` |
| `@file` | body from a file | `@battle.json` |
| `{...}` | inline JSON body | `{"name": "mew"}` |
| `-H 'K: v'` | header | `-H 'X-Api-Key: $KEY'` |

`$VAR` / `${VAR}` resolve from the environment at send time but are stored
**unresolved** in the session - secrets never hit disk (even a value the server
echoes back is redacted to `***`).

## 3. Agent one-shot mode (`gracy x`)

The same engine, no TTY - every command is one process, state lives in the
session file, and `--json` prints a machine-readable result. This is how an AI
agent drives it:

```
$ gracy x 'get /pokemon/ditto' --base https://pokeapi.co/api/v2 \
      --session poke.gracy.json --json
{"step_id": 1, "method": "GET", "url": ".../pokemon/ditto", "status": 200,
 "elapsed_ms": 74.1, "matched_endpoint": null, "body_preview": {...}}

$ gracy x 'name get_pokemon' --session poke.gracy.json --json
{"ok": true, "endpoint": "get_pokemon", "template": "/pokemon/ditto"}

$ gracy x 'get /pokemon/mew' --session poke.gracy.json --json
{"step_id": 2, "status": 200, "matched_endpoint": "get_pokemon",
 "template_proposal": "/pokemon/{pokemon}", "model_drift": []}

$ gracy x 'save pokeapi.py --tests' --session poke.gracy.json --json
{"ok": true, "files": ["pokeapi.py", "test_pokeapi.py", "pokeapi.cassette.db"]}
```

Exit codes: `0` success · `2` parse error · `1` execution error (each carries a
JSON `error` field under `--json`). An agent can explore an API overnight and
leave you a reviewed typed client, passing tests, and OpenAPI docs
(`gracy docs pokeapi:PokeGracy --format yaml`) in the morning.

## 4. Persistent agent loop (`--stdio`)

For a long session, `gracy explore --stdio` stays alive as one process and speaks
JSONL both ways: one JSON command per stdin line, one JSON result per stdout line
(the framing LSP and MCP use). The session lives in memory, so there is no
per-command Python startup or session-file re-read.

```
$ gracy explore --stdio --base https://pokeapi.co/api/v2 --session poke.gracy.json
```

```
→ {"cmd": "get /pokemon/pikachu"}
← {"step_id": 1, "status": 200, "matched_endpoint": null, "body_preview": {...}}
→ {"cmd": "name get_pokemon"}
← {"ok": true, "endpoint": "get_pokemon", "template": "/pokemon/pikachu"}
→ {"cmd": "get /pokemon/mew"}
← {"step_id": 2, "status": 200, "matched_endpoint": null, "template_proposal": "/pokemon/{pokemon}", "model_drift": []}
→ {"cmd": "save pokeapi.py --tests"}
← {"ok": true, "files": ["pokeapi.py", "test_pokeapi.py", "pokeapi.cassette.db"]}
```

A malformed line or bad command yields a `{"error": "..."}` line and the stream
keeps going; EOF (Ctrl-D) exits 0. Each input line accepts either `{"cmd": "<command>"}`
or a bare JSON string `"<command>"`.

## 5. Drift detection in CI (`--check`)

`gracy explore --check` re-probes every named endpoint against the live API and
diffs the live response shape against what you recorded. Clean exits `0`; any
added, removed, or type-changed field (or a status change) exits `1`, so a
changed upstream fails the build.

```
$ gracy explore --check --session poke.gracy.json
  ✓ list_pokemon   GET /pokemon
  ✗ get_pokemon    GET /pokemon/{name}
      - removed: base_experience
      + added:   cries.latest
      ~ type:    weight: int -> str

1/2 endpoints drifted
```

Add `--json` for a machine-readable report (per-endpoint `added`/`removed`/
`type_changed` and the `ok` flag) to wire into a scheduled CI job.
