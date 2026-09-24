# six-hops (6hops)

A personal job-hunt app for one user. You maintain a network graph of people, companies and
schools, and 6hops finds the warmest path from you to any company. Jobs, Cold Email and Study
pages hang off that graph.

See [`docs/PLAN.md`](docs/PLAN.md) for the full v1 plan and milestones.

**Status:** M0–M4 done (skeleton, core, SQLite store with undo, graph UI, path queries).
Next: M5 entity resolution, M6 LinkedIn import, M7 LLM chat. Jobs, Cold Email and Study are stubs.

## Using the graph page

- **Add** people, companies and schools from the side panel; tick "I know them" to link to you.
- **Click** a node or connection to edit it, connect it to others (existing or new), merge a
  duplicate into it, or delete it. Every change can be undone from the Overview panel.
- **Reach a company**: type a company in the toolbar and press *Find paths*. You get the
  strongest and shortest paths from you (up to 4 hops; *Show longer paths* goes to 6), who to
  ask first, and the path highlighted on the graph. Tick *via shared employers/schools* to
  count a shared company or school as a weak tie.
- **Import/export** the whole graph as JSON from the Overview panel. Import replaces the graph
  and can be undone.

### Path ranking

Each connection has a strength from 1 to 5, mapped to a weight
(1: 0.20, 2: 0.40, 3: 0.60, 4: 0.80, 5: 0.95). A path's score is the product of its weights.
A former employee (WORKED_AT) counts half as much as a current one. A shared company or
school, when enabled, is a weak tie worth 0.3. "Strongest" ranks by score; "shortest" by hops.

## Known issues

- The map layout can put labels on top of each other in dense areas. *Tidy layout* re-runs it,
  and you can drag nodes; positions are remembered per browser.
- Pickers use the browser's built-in suggestion list, which gets unwieldy with thousands of
  nodes (after a large LinkedIn import).
- Fixed in the UI polish pass: on phones, finding paths zoomed the map out to a small cluster
  and left the results off-screen.

## Setup

Requires [uv](https://docs.astral.sh/uv/) (local run) or Docker (container run).

```sh
cp .env.example .env      # then set APP_PASSWORD, SECRET_KEY, ME_NAME
```

Generate a secret key with `python -c "import secrets; print(secrets.token_hex(32))"`.

## Run

```sh
make up        # docker compose; http://localhost:8000, data persisted in ./data
# or
make dev       # uv + uvicorn with reload; http://127.0.0.1:8000
```

## Test and lint

```sh
make test      # pytest
make lint      # ruff check + ruff format --check
make fmt       # auto-fix
```

CI (GitHub Actions) runs lint and tests on every push.

## API

All endpoints need the session cookie or `Authorization: Bearer $API_TOKEN`.
Interactive docs at `/api/docs`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/graph` | Whole graph snapshot |
| POST | `/api/ops` | Apply a list of ops (`create_node`, `update_node`, `merge_nodes`, `create_edge`, `update_edge`, `delete_node`, `delete_edge`) |
| GET | `/api/history` | Recent changes |
| POST | `/api/undo` | Undo the last change |
| GET | `/api/graph/export` | Download the graph as JSON |
| POST | `/api/graph/import` | Replace the graph from a JSON file (multipart `file`) |
| GET | `/api/paths?target=<company id>&max_hops=4&via_institutions=false` | Ranked paths |

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `APP_PASSWORD` | yes | Login password |
| `SECRET_KEY` | yes | Signs the session cookie |
| `ME_NAME` | | Name of your "Me" node (default `Me`) |
| `API_TOKEN` | | Enables `Authorization: Bearer <token>` for curl/scripts |
| `DATABASE_URL` | | Default `sqlite:///data/6hops.db` |
| `COOKIE_SECURE` | | `true` when served over HTTPS |
| `BASE_URL` | | Public URL when deployed |
| `HOST` / `PORT` | | Bind address (container binds `0.0.0.0:8000`) |
| `LLM_PROVIDER` | | `gemini` (default) |
| `GEMINI_MODE` | | `vertex` (default) or `api_key` |
| `GEMINI_MODEL` | | Model name |
| `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` | Vertex | Vertex AI project and location |
| `GOOGLE_APPLICATION_CREDENTIALS` | | Service-account JSON path (otherwise ADC) |
| `GEMINI_API_KEY` | API-key mode | Gemini API key |

## Architecture

```
src/sixhops/
  core/       pure domain logic: model, ops, validation, compile (+undo), paths
              (entity resolution and planning arrive in M5)
  ports/      small Protocol interfaces: LLMProvider, GraphStore, JobSource
  adapters/   implementations of the ports (SQLite store, Gemini, ...)
  app/        FastAPI routes, templates (Jinja2 + HTMX), static assets, wiring
```

Visual design notes (tokens, type, principles) are in [`docs/DESIGN.md`](docs/DESIGN.md). The UI
font is Overpass (SIL OFL), self-hosted under `app/static/fonts/`.

Dependencies point inward: `app` and `adapters` depend on `ports` and `core`; `core` depends
on nothing in the app. Every graph change, whether from the UI, chat or an import, is expressed
as ops, validated and compiled to primitive mutations in `core`, and written through the single
`GraphStore.commit` method.
