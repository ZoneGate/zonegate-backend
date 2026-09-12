# ZoneGate Backend

ZoneGate is a network-attested authorization system for critical human actions.

It bridges telecommunications carrier network APIs (via Nokia Network As Code / CAMARA standards) with enterprise authorization, utilizing advisory AI for intelligent evidence planning and contextual risk interpretation while strictly preserving deterministic, LLM-free policy authority.

## Core Flow & Architecture

```
Request
  ↓
Litestar
  ↓
AuthorizationService
  ↓
Evidence Planner (AI Advisory)
  ↓
Evidence Plan Validator (Deterministic)
  ↓
Evidence Gateway (Runtime Boundary)
  ↓
Nokia / CAMARA (Carrier Network APIs)
  ↓
Canonical Evidence (Normalized)
  ↓
Context Evaluator (AI Advisory)
  ↓
Deterministic Policy Engine (LLM-free)
  ↓
APPROVE / HOLD / DENY
  ↓
Scoped Authorization Token / Human Authority Transfer
```

### Security Invariants & Principles
- **Carrier Network Evidence**: Nokia / CAMARA APIs provide network-attested facts (SIM swap continuity, cell-level geofencing, hardware device swap, network reachability) that cannot be spoofed by client software.
- **AI is Advisory Only**: The AI plans optional evidence and assesses risk context. It **must never** directly authorize actions, issue authorization tokens, execute protected operations, mutate policy, or bypass deterministic controls.
- **Evidence Gateway Constrains Access**: The AI never accesses Nokia APIs, raw HTTP clients, or credentials. All carrier invocations are strictly validated against actor-device bindings, static capability allowlists, and workflow policies.
- **Deterministic Policy Authority**: Automatic authorization decisions are owned exclusively by a deterministic, LLM-free Policy Engine. Even if an AI advises `APPROVE`, failing deterministic rules (e.g. location mismatch, unverified phone number) unconditionally enforce `DENY`.
- **HOLD Transfers Authority to Humans**: An outcome of `HOLD` is an authority transfer to a designated operational role (e.g. `ROLE_CARGO_SUPERVISOR`), never an AI retry loop.

### Evidence Tiers (RELEASE_CARGO Workflow)
- **Mandatory**: `NUMBER_VERIFICATION`, `LOCATION_VERIFICATION` (always collected regardless of AI decisions).
- **Optional**: `SIM_SWAP`, `DEVICE_SWAP`, `REACHABILITY` (the AI Evidence Planner may select from this set when risk parameters warrant).
- **Forbidden**: `KYC_MATCH` (strictly disallowed; proposing it causes immediate validation failure).

---

## Deterministic Policy Rules (RELEASE_CARGO)

Evaluated in order; the first rule that fires decides. The agent's advisory
assessment is attached to the record but never reaches this list.

| # | Condition | Outcome | Authority |
|---|---|---|---|
| 1 | Actor lacks `cargo:release` | DENY | — |
| 2 | Number verification is not `true` | DENY | — |
| 3 | Location verification is `false` | DENY | — |
| 4 | Recent SIM swap | HOLD | `ROLE_SECURITY_OFFICER` |
| 5 | Cargo category is restricted | HOLD | the category's own role |
| 6 | Outside the operational window | HOLD | `ROLE_CARGO_SUPERVISOR` |
| 7 | Otherwise | APPROVE | — |

Rule 5 replaced a single monetary threshold: what makes a release sensitive is
what is in the container, not only what it is worth, and each kind of
sensitivity answers to a different role. Which categories are restricted, and
to whom, is configuration (`PUT /v1/policy/config`). Declared value is still
recorded on every transaction for the audit trail; it no longer decides
anything on its own.

Rule 2 rejects `null` as well as `false`: evidence that was never collected is
not evidence that passed.

---

## Strict Hard Requirements

- **Python 3.14**: Explicitly pinned via `requires-python = "==3.14.*"`.
- **Zova 1.0.0**: Embedded records, objects, and persistence layer pinned to `zova = "1.0.0"` (officially supports Linux, macOS, and Windows via prebuilt `win_amd64` wheels).

---

## Current Integration Status

### Nokia / CAMARA Integration
- Standard CAMARA models implemented for Number Verification, Location Verification, SIM Swap, Device Swap, and Device Reachability.
- `NokiaEvidenceClient` implements async HTTP calls conforming to CAMARA specs using `httpx.AsyncClient`.
- In test environments, deterministic test doubles (`FakeNokiaClient`) verify all gateway authorization rules offline without external network dependency.
- Production deployment requires valid Nokia Network as Code API credentials (`NOKIA_BASE_URL` and `NOKIA_API_KEY`).

### Nokia Network as Code MCP Server (Model Context Protocol)
- Supports the official Nokia RapidAPI Hub MCP server via `mcp-remote`:
  ```json
  {
    "mcpServers": {
      "RapidAPI Hub - Network as Code": {
        "command": "npx",
        "args": [
          "mcp-remote",
          "https://mcp.prodeu.apihub.nokia.io",
          "--header",
          "x-api-host: network-as-code.nokia.rapidapi.com",
          "--header",
          "x-api-key: <YOUR_RAPIDAPI_KEY>"
        ]
      }
    }
  }
  ```
- Template provided in `nokia_mcp.example.json`. Users can copy it to `nokia_mcp.json` (gitignored) and insert their key.
- **Static Allowlist**: Enforces an explicit allowlist of permissible tools (`NOKIA_MCP_STATIC_ALLOWLIST`). An empty allowlist means all tools provided by the MCP server are eligible. Unlisted tools are blocked at the client boundary before invocation.
- Used by the **Context Evaluation Agent** to query supplemental carrier facts during risk assessment.

### Zova 1.0.0 Storage Integration
- Zova 1.0.0 native embedded database (`Database.create`, `Database.open`, `Database.create_memory`) is used as the sole state and persistence engine.
- Leverages Zova's partitioned byte namespaces (`actors`, `device_bindings`, `transactions`, `evidence_plans`, `evidence`, `context_evaluations`, `decisions`, `receipts`) with transactional commits/rollbacks.
- **Thread Model Consideration**: Zova's PyO3 binding (`PyDatabase`) is thread-pinned (`!Send`). `ZoneGateStore` isolates database handle lifecycle and all execution inside a dedicated single-threaded worker executor, providing safe async execution across concurrent requests and event loops without thread assertion panics.

---

## Setup & Local Execution

### 1. Prerequisites
- [uv](https://docs.astral.sh/uv/) installed
- Python 3.14 installed (`/opt/homebrew/bin/python3.14` or managed via `uv`)

### 2. Install Dependencies
```bash
uv sync
```

### 3. Environment Configuration
Copy the sample environment file:
```bash
cp .env.example .env
```

Settings available:
```ini
APP_ENV=development
APP_HOST=0.0.0.0
APP_PORT=8000
LITESTAR_APP=zonegate.app:app

# AI Provider: "ollama" (local) or "gemini" (cloud)
LLM_PROVIDER=ollama

# Local Ollama Configuration (when LLM_PROVIDER=ollama)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2

# Google Gemini Configuration (when LLM_PROVIDER=gemini)
GEMINI_API_KEY=your-gemini-api-key-here
GEMINI_MODEL=gemini-3.6-flash

NOKIA_BASE_URL=https://api.nokia.example.com
NOKIA_API_KEY=mock-nokia-api-key

ZOVA_DB_PATH=data/zonegate.zova
```

### 4. Configuring AI Runtime (Ollama or Gemini)
- **Local Ollama**: Start Ollama and run your model:
  ```bash
  ollama run llama3.2
  ```
  Set `LLM_PROVIDER=ollama` in `.env`.
- **Cloud Gemini**: Provide your Google AI API key:
  Set `LLM_PROVIDER=gemini` and `GEMINI_API_KEY=your-api-key` in `.env`.

*Note: ZoneGate is designed to boot and function even if the AI runtime is offline. AI planning steps safely fall back to mandatory baselines, and `/health` reports AI provider reachability status transparently.*

### 5. Run the Test Suite
```bash
uv run pytest -v
```

### 6. Start the Litestar Server
```bash
uv run litestar run --port 8000
```
or directly via uvicorn:
```bash
uv run uvicorn zonegate.app:app --host 0.0.0.0 --port 8000
```

Verify application health:
```bash
curl http://127.0.0.1:8000/health
```

### 7. Run with Docker Compose

Docker is the only prerequisite. Compose brings up the API together with the
mock carrier it needs:

```bash
docker compose up --build -d
```

- API — <http://127.0.0.1:8000>
- Mock carrier — `127.0.0.1:8899`

```bash
docker compose logs -f zonegate    # follow the pipeline
docker compose down                # stop
```

Decisions are written to `.docker/zonegate.zova` on the host and survive a
`down`.

**A local `.env` does not configure the container, and must not.** That file is
written for running the app directly on the host, where `NOKIA_BASE_URL` and
`ZOVA_DB_PATH` mean different things than they do inside a container. Compose
reads `.env` when substituting `${...}`, so anything describing the container's
own world -- the carrier address, the MCP flag, the database path -- is written
literally in `docker-compose.yml` where that file cannot reach it. Getting this
wrong is not a visible failure: the gateway ends up pointed at a domain that
does not resolve and **every decision comes back DENY** on a carrier error.

To decide on a real carrier instead of the mock:

```bash
CARRIER_URL=https://your-carrier.example.com CARRIER_KEY=... docker compose up -d
```

### Tests

```bash
docker compose --profile tools run --rm test
```

161 tests. They are hermetic -- never reaching a live Gemini, Ollama or Nokia
endpoint, whatever a local `.env` says. The package is installed into the
image, so after changing anything under `src/` rebuild before re-running:
`docker compose --profile tools build test`.

---

## Running the Demo Stack (API + Dashboard)

The dashboard at [zonegate-website](https://github.com/ZoneGate/zonegate-website)
talks to this API over CORS. Origins are configured with `CORS_ALLOW_ORIGINS`
(default `http://localhost:3000,http://127.0.0.1:3000`). `PUT` must stay in the
allowed methods or saving policy thresholds from the console fails as a
preflight rejection, with nothing useful shown in the browser.

### 1. Enrol an actor

The store starts empty, and an unenrolled actor is denied by design:

```bash
curl -X POST http://127.0.0.1:8000/v1/actors -H 'Content-Type: application/json'   -d '{"actor":{"actor_id":"usr_cargo_operator_01","role":"ROLE_CARGO_OPERATOR","permissions":["cargo:release","cargo:inspect"],"registered_phone_number":"+14155550199","registered_device_id":"dev_imei_99887766","enrollment_status":"ACTIVE"}}'
```

There is also a seed script. Zova is an **embedded** database: if the API
server holds the file open, writes from a second process are silently lost with
no error. Stop the server first:

```bash
docker compose stop zonegate
docker compose run --rm zonegate python scripts/seed.py
docker compose start zonegate
```

### 2. Drive a scenario

The mock carrier runs as a compose service and needs no separate start.

The mock listens on `8899` (override with `MOCK_CAMARA_PORT`) and reads its
scenario from `scripts/camara_state.json`. Edit that file between requests to
drive a specific outcome — setting `"location_verified": false` turns the next
release request into the blocked presence-attack case:

```json
{"number_verified": true, "location_verified": false, "sim_swapped": false,
 "device_swapped": false, "reachability": "CONNECTED_DATA"}
```

Stage a scenario by editing `scripts/camara_state.json` while it runs:

```json
{"location_verified": false}
```

### 3. Start the dashboard

```bash
cd ../zonegate-website/zonegate-web
cp .env.example .env.local     # NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
npm install && npm run dev
```

The Hold Queue at `http://localhost:3000/holds` reads live decisions from this
API and posts the supervisor's verdict back.

---

## HOLD: Human Authority Transfer

A `HOLD` hands the decision to the role named in `required_authority`. The
designated human decides on the **same evidence package** the policy engine
used — no new input is requested.

- `GET /v1/authorizations?decision=HOLD&pending=true` — the queue awaiting a human
- `GET /v1/authorizations/{decision_id}/context` — decision + transaction + evidence + plan + receipt
- `POST /v1/authorizations/{decision_id}/resolve` — the binding verdict

```bash
curl -X POST http://127.0.0.1:8000/v1/authorizations/dec_abc123/resolve   -H "Content-Type: application/json"   -d '{"outcome":"APPROVE","resolved_by":"OFFICER K. VANCE","note":"Verified with the berth master."}'
```

Enforced invariants:

- **Only a HOLD is resolvable.** Resolving an `APPROVE` or a deterministic `DENY`
  returns `409`. A hard DENY can never be overridden by a person.
- **The policy outcome is never rewritten.** The stored decision stays `HOLD`;
  the human verdict is attached as a separate `resolution` record, so the audit
  trail shows both what the engine decided and what the person decided.
- **One resolution per decision.** A second attempt returns `409`.
- **APPROVE issues a scoped token** bound to the actor, action, resource and
  zone of the original transaction; DENY issues none.

---

## API Reference

### Health
- `GET /health`: Returns service health and reachability of dependencies (Zova, Ollama, Nokia).

### Console sign-in
The console is reachable only to somebody already on the enrolled roster; there
is no registration endpoint and there will not be one. The session lives in an
httpOnly cookie, so no page script can read it.

- `POST /v1/auth/login`: Signs an enrolled actor in and sets the session cookie.
- `GET /v1/auth/session`: The signed-in actor, or 401 when nobody is.
- `POST /v1/auth/logout`: Revokes the session server-side and clears the cookie.
- `POST /v1/auth/password`: Changes the signed-in actor's own console password.

On a fresh database the auto-seeded demo operator gets `DEMO_OPERATOR_PASSWORD`
(default `zonegate-demo`), so the sign-in screen is not a dead end.

### Actors
- `POST /v1/actors`: Enrolls an actor and binds their device. Required before any request can pass the gateway. An optional `password` also gives them console access.
- `GET /v1/actors`: The enrolled roster with each actor's binding.
- `GET /v1/actors/{actor_id}`: Retrieves an enrolled actor with their active device binding.
- `PUT /v1/actors/{actor_id}/permissions`: Replaces an actor's permissions. `expected_permissions` is what the editor was showing; a mismatch is refused with 409 rather than overwriting a concurrent edit.

### Policy
- `GET /v1/policy/config`: The restricted-category map and operational window the engine is running with.
- `PUT /v1/policy/config`: Saves both, and rebinds the running engine.
- `GET /v1/policy/categories`: The cargo categories a request can carry, each marked with the authority it escalates to.
- `GET /v1/policy/zones`: The geofences the evidence gateway verifies device location against — the same circles the console map draws.

### Authorizations
- `POST /v1/authorizations`: Evaluates a transaction request through the full authorization pipeline. Returns the authoritative `PolicyDecision` and an audit `Receipt`.
- `POST /v1/authorizations/stream`: The same pipeline, reported as it runs. Server-sent events: a `stage` event at each of the six steps (IDENTITY, PLAN, VALIDATE, EVIDENCE, CONTEXT, POLICY) and a closing `result` event carrying exactly the payload the plain endpoint returns. A stage that is skipped says so; the stage that refused a request is the one marked FAILED.
- `GET /v1/authorizations`: Lists decisions newest first. Supports `?decision=HOLD`, `?pending=true` and `?limit=`.
- `GET /v1/authorizations/{decision_id}`: Retrieves an existing policy decision record from Zova.
- `GET /v1/authorizations/{decision_id}/context`: Returns the decision together with the transaction, canonical evidence, evidence plan and receipt behind it.
- `POST /v1/authorizations/{decision_id}/resolve`: Records the binding human decision on a HOLD.

### Receipts
- `GET /v1/receipts/{receipt_id}`: Retrieves an issued action receipt (including scoped authorization token if approved).
