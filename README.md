# six-hops (6hops)

A personal job-hunt app for one user. You maintain a network graph of people, companies and
schools, and 6hops finds the warmest path from you to any company. Jobs, Cold Email and Study
pages hang off that graph.

See [`docs/PLAN.md`](docs/PLAN.md) for the full v1 plan and milestone status.

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
  core/       pure domain logic: model, ops, validation, compile (+undo), resolution, paths
  ports/      small Protocol interfaces: LLMProvider, GraphStore, JobSource
  adapters/   implementations of the ports (SQLite store, Gemini, ...)
  app/        FastAPI routes, templates (Jinja2 + HTMX), static assets, wiring
```

Dependencies point inward: `app` and `adapters` depend on `ports` and `core`; `core` depends
on nothing in the app. Every graph change, whether from the UI, chat or an import, is expressed
as ops, validated and compiled to primitive mutations in `core`, and written through the single
`GraphStore.commit` method.
