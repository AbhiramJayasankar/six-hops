# 6hops v1 plan

Approved plan for v1, with the review changes folded in. The graph page is fully built;
Jobs, Cold Email and Study are working stubs.

## 1. Stack and graph store

| Layer | Choice | Why |
|---|---|---|
| Language / deps | Python 3.12, `uv`, `ruff`, `pytest` | Strongest language for the owner; lockfile; fast. |
| Web | FastAPI, sync handlers | Typed, Pydantic-native. Single user, so no async needed. |
| Schemas | Pydantic v2 | One set of models for API, LLM structured output, JSON import/export and op validation. |
| Frontend | Jinja2 + HTMX + Cytoscape.js, vendored, no Node build | Server-rendered, nearly all Python. One small `graph.js`. |
| Store of record | SQLite via SQLAlchemy Core | Zero ops, one file. `DATABASE_URL` can point at Postgres later. |
| Path engine | NetworkX over a snapshot, inside the pure core | See below. |
| Fuzzy matching | rapidfuzz | Entity resolution. |
| Run | `docker compose up` or `make dev` | One command. Data in a mounted `./data` volume. |
| CI | GitHub Actions: `ruff check`, `ruff format --check`, `pytest` | |

**Why not a graph database.** A personal network is hundreds to low thousands of nodes.
Loading a snapshot and running Yen's k-shortest-paths in NetworkX takes milliseconds, and our
ranking (strength product, WORKED_AT discount, weak institution ties, hop cap) is custom anyway.
Neo4j needs GDS/APOC for weighted k-paths, ~1 GB JVM and a second container. Kùzu was archived
upstream in late 2025. Pure-Python ranking over a snapshot is trivial to unit test. The
`GraphStore` port leaves room for a Neo4j adapter if the graph outgrows this (~100k edges).

## 2. Repo structure

```
six-hops/
  pyproject.toml  uv.lock  Makefile  Dockerfile  docker-compose.yml  .env.example  README.md
  .github/workflows/ci.yml
  docs/PLAN.md
  src/sixhops/
    core/                 # PURE: no I/O, no framework imports.
      model.py            # Node, Edge, enums, GraphSnapshot
      ops.py              # Op union, ChangeSet, Mutation, Extraction
      validate.py         # validate ops / snapshots
      compile.py          # ops -> primitive mutations (incl. merge lowering) + inverse
      resolve.py          # entity resolution / duplicate candidates
      plan.py             # Extraction + snapshot + decisions -> ChangeSet
      paths.py            # path ranking
    ports/                # Protocols only.
      llm.py  graph_store.py  job_source.py
    adapters/
      llm/  gemini.py  fake.py  claude.py (skeleton)  openai_compat.py (skeleton)
      store/sqlite.py
      importers/linkedin.py   # Connections.csv -> Extraction
      jobs/README.md
    app/
      main.py  config.py  auth.py  wiring.py
      services/  chat.py  changesets.py
      prompts/extract.md
      routes/  graph.py  chat.py  paths.py  io.py  jobs.py  email.py  study.py  auth.py
      templates/  static/
  tests/
    core/  adapters/  e2e/
```

Dependencies point inward: `app -> ports <- adapters`, and everything may use `core`, which
imports nothing from the app.

## 3. Data model and op schema

```python
NodeKind = Literal["me", "person", "company", "school"]
EdgeKind = Literal["KNOWS", "WORKS_AT", "WORKED_AT", "STUDIED_AT"]

class Node(BaseModel):
    id: str                      # ULID-like
    kind: NodeKind
    name: str
    aliases: list[str] = []
    attrs: dict[str, str] = {}   # person: title, email, linkedin; company/school: domain
    notes: str = ""

class Edge(BaseModel):
    id: str
    kind: EdgeKind
    src: str; dst: str           # KNOWS canonical (src < dst); *_AT: person/me -> institution
    strength: int = 3            # 1..5
    note: str = ""

class GraphSnapshot(BaseModel):
    version: int
    nodes: dict[str, Node]; edges: dict[str, Edge]
```

"Institution" means a company or school node.

Invariants (`validate.py`):
- exactly one `me` node; it can't be deleted or merged away
- KNOWS: person/me <-> person/me, no self-loops
- WORKS_AT / WORKED_AT: person/me -> company
- STUDIED_AT: person/me -> school
- at most one edge per (kind, src, dst); strength in 1..5

**Three schema layers. The LLM (and the LinkedIn importer) only produce the first.**

(a) `Extraction`, flat and name-based:
```python
class Mention(BaseModel):   key: str; kind: Literal["person","company","school"]; name: str; attrs: dict[str,str] = {}
class Relation(BaseModel):  kind: EdgeKind; src: str; dst: str      # mention keys, or "me"
                            strength: int | None = None; note: str = ""
class Extraction(BaseModel): mentions: list[Mention]; relations: list[Relation]
class ChatTurn(BaseModel):  intent: Literal["add_facts","find_path","other"]
                            extraction: Extraction | None; target_company: str | None; reply: str
```

(b) `Op`, the reviewable diff, produced deterministically by `plan.py` or directly by manual CRUD:
```python
NodeRef = ExistingRef(id) | NewRef(key)
Op = CreateNode(key, kind, name, aliases, attrs, notes)
   | UpdateNode(node_id, name?, add_aliases?, attrs?, notes?)
   | MergeNodes(keep_id, drop_id, score, reason)        # both existing nodes
   | CreateEdge(kind, src, dst, strength, note, strength_inferred)
   | UpdateEdge(edge_id, kind?, strength?, note?)           # e.g. WORKS_AT -> WORKED_AT
   | DeleteNode(node_id) | DeleteEdge(edge_id)            # manual UI only
class ChangeSet: id; source: "chat"|"manual"|"import"|"linkedin"; base_version; ops; resolutions; warnings
```
The planner, and therefore chat, never emits deletes.

(c) `Mutation`, primitive writes: `PutNode | DelNode | PutEdge | DelEdge`. `compile.py` lowers
ops into mutations. A merge becomes: repoint edges of the dropped node, collapse duplicate edges
(max strength, joined notes), drop self-loops, union aliases/attrs, delete the dropped node.
`compile.py` also computes the **inverse mutations** against the pre-commit snapshot, which is
what undo replays.

## 4. Port interfaces

```python
class LLMProvider(Protocol):
    name: str
    def structured(self, *, system: str, messages: list[ChatMessage], schema: type[T]) -> T: ...
    def text(self, *, system: str, messages: list[ChatMessage]) -> str: ...

class GraphStore(Protocol):
    def snapshot(self) -> GraphSnapshot: ...
    def commit(self, mutations, *, expected_version: int, inverse, summary: str) -> int: ...
    def history(self, limit: int = 20) -> list[ChangeRecord]: ...
    def undo_last(self, *, expected_version: int) -> int: ...

class JobSource(Protocol):
    name: str
    def search(self, query: JobQuery) -> Iterator[JobPosting]: ...
```

`commit` is the only write path. Manual edits, chat, JSON import and LinkedIn import all go
through ops -> `validate` -> `compile` -> `commit`. JSON import compiles to "delete everything,
put everything", so it is undoable too.

`StructuredLLMBase` (adapter side) converts the Pydantic schema to JSON Schema, calls
`_complete_json`, validates with `model_validate_json`, and retries once feeding the validation
error back. Backends:
- **Gemini** (google-genai SDK), two modes selected by `GEMINI_MODE`:
  - `vertex` (default): `GOOGLE_CLOUD_PROJECT` + `GOOGLE_CLOUD_LOCATION`, auth via ADC or a
    service account (`GOOGLE_APPLICATION_CREDENTIALS`)
  - `api_key`: `GEMINI_API_KEY`
- **Claude**: forced tool call / structured outputs (skeleton in v1)
- **OpenAI-compatible (Fireworks)**: `response_format: json_schema` (skeleton in v1)

## 5. Chat -> ops -> confirm

1. `POST /chat` sends the system prompt, recent history, and a short list of existing nodes that
   fuzzy-match tokens in the message. The LLM returns a validated `ChatTurn`.
2. `find_path` -> `paths.rank` -> shown in chat and highlighted on the graph. No mutation.
3. `add_facts` -> `plan.plan(extraction, snapshot, decisions)`:
   - score >= 0.95 links to the existing node (still shown in the diff)
   - 0.70-0.95 becomes a `Resolution` with candidates, defaulting to the best match
   - below that, `CreateNode`
   - relations become `CreateEdge`, or `UpdateEdge` if the edge exists and differs, or nothing if
     identical. A new WORKS_AT when the person already WORKS_AT elsewhere proposes turning the old
     one into WORKED_AT.
   - LLM-inferred strengths are flagged; no signal means 3
4. The ChangeSet is stored as pending with `base_version`, and rendered as a diff card (per-op
   include checkbox, resolution radios, inline strength edit). Changing a resolution re-plans.
5. Confirm -> validate -> compile -> commit. Version conflict -> re-plan and show again.

Entity resolution (`resolve.py`, pure): identity keys first (`linkedin` URL, `email` exact match
= 1.0); then normalize (casefold, strip accents/punctuation/honorifics; company suffixes such as
Pvt Ltd/Inc/Technologies); base score = max(alias exact, `token_set_ratio`, initials rule);
+0.1 context boost for a shared neighbour mentioned in the same message. The same function powers
a manual "find duplicates" action that proposes `MergeNodes`.

## 6. Path ranking (`paths.py`, pure)

- Traversal graph: me/person nodes and KNOWS edges (undirected). The target company attaches as
  the final hop via WORKS_AT (factor 1.0) or WORKED_AT (factor 0.5).
- **Institution hops toggle** (off by default): companies and schools other than the target
  become intermediate nodes. Passing through one (in via an affiliation edge, out via another)
  is a weak tie with combined factor **0.3**, regardless of WORKS_AT/WORKED_AT/STUDIED_AT.
- p(strength) = {1: .20, 2: .40, 3: .60, 4: .80, 5: .95}; score = product of p x factors;
  cost = -ln p, so strongest = cheapest.
- Strongest: Yen's k-shortest by cost. Shortest: k-shortest by hops, ties by score. Merged,
  de-duplicated, each tagged `shortest` / `strongest`.
- Hop cap 6 (computed). The UI shows paths up to **4 hops** by default, with a
  "show longer paths" control to expand to 6.
- Each `RankedPath` carries edge notes, the **first hop** (who to message) and the **referrer**.
- Edge cases: Me already works there -> direct path; unknown company -> fuzzy suggestions;
  unreachable -> "N people at X, none connected to you".
- The Jobs page's "who can refer me" overlay will reuse `rank()`.

## 7. Undo

Every commit stores a `ChangeRecord(id, version_before, version_after, summary, inverse)`.
"Undo last change" applies the most recent record's inverse atomically, if the graph is still at
that record's `version_after`, and pops it. Repeated undos walk back through history.

## 8. Milestones

| # | Milestone | Runnable result |
|---|---|---|
| M0 | Skeleton: pyproject, config, auth, layout + nav, all four pages as stubs, Dockerfile, compose, Makefile, CI | Log in and click through every page |
| M1 | `core/model`, `ops`, `validate`, `compile` (merge lowering + inverse), ports, unit tests | `make test` |
| M2 | SQLite `GraphStore` (commit, history, undo), store contract tests, Me bootstrap, JSON import/export | Export/import/undo via curl |
| M3 | Graph UI: Cytoscape view, click-to-edit panel, manual CRUD + merge via ops, undo button, import/export buttons | Usable graph app, no LLM |
| M4 | `core/paths.py` + tests, "Reach company" control, 4-hop default + expand, institution toggle, highlight on graph | Path queries in the UI |
| **Pause** | **Owner review before M5** | |
| M5 | `core/resolve.py`, `core/plan.py` + tests; duplicate warnings in manual create; "find duplicates" | Deterministic merge proposals |
| M6 | LinkedIn `Connections.csv` import -> Extraction -> plan/resolve -> normal ChangeSet diff + confirm; idempotent re-import | Import LinkedIn export |
| M7 | `LLMProvider`, `StructuredLLMBase`, Gemini (Vertex + API key), `FakeLLM`; chat pane, diff card, replan, confirm, path intent; e2e test | Full chat workflow |
| M8 | Claude and OpenAI-compatible skeletons, README complete, session summary | Docs complete |

**Ordering note.** LinkedIn import was requested "after M3", but it has to run through
`plan`/`resolve`, which land in M5. It is therefore placed directly after M5 (as M6), and the
pause after M4 (paths) is unchanged.

### LinkedIn import (M6)

- Parse LinkedIn's `Connections.csv` (skip the "Notes:" preamble; columns First Name, Last Name,
  URL, Email Address, Company, Position, Connected On).
- Each row -> person mention (`linkedin`, `email`, `title` attrs), optional company mention,
  `me KNOWS person` (strength 2, note "LinkedIn connection"), `person WORKS_AT company`.
- Resolution matches on the LinkedIn URL first, so re-import resolves to the same nodes.
- Import planning never overwrites manually edited strength/notes on existing edges; identical
  edges produce no op. **Re-importing the same file yields an empty ChangeSet.** A changed
  Company proposes WORKS_AT -> WORKED_AT for the old employer.
- Large imports: the diff card groups ops by type with counts and paginates.

### End-to-end test (M7)

FastAPI TestClient + `FakeLLM` + temp SQLite. Seed Me KNOWS Rahul, "Priya S" WORKS_AT Razorpay;
post "Rahul used to work with Priya, and Priya is at Razorpay."; assert Priya resolves to Priya S,
Rahul KNOWS Priya S is proposed, WORKS_AT is not duplicated; confirm; assert graph; assert
`rank(Razorpay)` returns Me -> Rahul -> Priya S -> Razorpay.

## 9. Configuration

| Variable | Purpose |
|---|---|
| `APP_PASSWORD` | Login password (required) |
| `SECRET_KEY` | Signs the session cookie (required) |
| `API_TOKEN` | Optional Bearer token for API/curl |
| `DATABASE_URL` | Default `sqlite:///data/6hops.db` |
| `ME_NAME` | Name of the Me node |
| `LLM_PROVIDER` | `gemini` (later `claude`, `openai_compat`) |
| `GEMINI_MODE` | `vertex` (default) or `api_key` |
| `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` | Vertex mode |
| `GOOGLE_APPLICATION_CREDENTIALS` | Vertex service-account JSON (else ADC) |
| `GEMINI_API_KEY` | API-key mode |
| `GEMINI_MODEL` | Model name, configurable |
| `BASE_URL` | Public URL when deployed |
| `COOKIE_SECURE` | `true` behind HTTPS |
| `HOST` / `PORT` | Bind address |

## 10. Risks

- **Privacy**: graph names/notes go to the LLM. Vertex AI (the default) does not train on
  customer data; free-tier API keys may. Manual UI and FakeLLM work offline.
- **Wrong merges**: never automatic, always in the diff, and undoable.
- **Extraction quality**: nicknames, pronouns across messages, initials-first names. Diff and
  resolution UI are the safety net; few-shot prompt examples.
- **Gemini schema limits**: the LLM-facing `Extraction` is flat, not the Op union.
- **Postgres**: SQLAlchemy Core should port; v1 is tested on SQLite only.
- **Strength is subjective**: ranking constants in one dataclass.
