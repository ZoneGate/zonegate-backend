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
| 6 | Otherwise | APPROVE | — |

Rule 5 replaced a single monetary threshold: what makes a release sensitive is
what is in the container, not only what it is worth, and each kind of
sensitivity answers to a different role. Which categories are restricted, and
to whom, is configuration (`PUT /v1/policy/config`). Declared value is still
recorded on every transaction for the audit trail; it no longer decides
anything on its own. The hour a release is requested at does not decide
anything either: there is no operational window.

Rule 2 rejects `null` as well as `false`: evidence that was never collected is
not evidence that passed.

---

## Strict Hard Requirements

- **Python 3.14**: Explicitly pinned via `requires-python = "==3.14.*"`.
- **Zova 1.0.0**: Embedded records, objects, and persistence layer pinned to `zova = "1.0.0"` (officially supports Linux, macOS, and Windows via prebuilt `win_amd64` wheels).

---

## Current Integration Status

### Nokia / CAMARA Integration

There are two carrier surfaces, chosen with `CARRIER_MODE`, and they are not
interchangeable.

**`CARRIER_MODE=rest`** — `NokiaEvidenceClient` posts CAMARA-shaped requests to
`NOKIA_BASE_URL`, for a carrier that exposes the CAMARA REST APIs directly.

**`CARRIER_MODE=live`** — `NokiaLiveEvidenceClient` talks to the production
Nokia Network as Code gateway. That gateway is not plain CAMARA REST: it takes
JSON-RPC `tools/call` requests at `NOKIA_MCP_URL`, selects the product with an
`x-api-host` header, and authenticates with `x-api-key` rather than a bearer
token. The CAMARA operations sit behind tool names, several at a different
version than the specifications suggest:

| Check | Live tool | Path behind it |
| --- | --- | --- |
| Number verification | `phoneNumberVerify-NV-V2` | `/passthrough/camara/v1/number-verification/number-verification/v2/verify` |
| Location verification | `verifyLocation-LocV-V0` | `/location-verification/v0/verify` |
| SIM swap | `checkSimSwap` | `/passthrough/camara/v1/sim-swap/sim-swap/v0/check` |
| Device swap | `checkDeviceSwap` | `/passthrough/camara/v1/device-swap/device-swap/v1/check` |
| Reachability | `getReachabilityStatus` | `/device-status/device-reachability-status/v1/retrieve` |

Two shapes differ from the specifications and were observed on the live
gateway: `areaType` is the upper-case enum `CIRCLE`, and reachability answers
`{"reachable": true, "connectivity": ["DATA"]}` rather than a
`reachabilityStatus` enum. `CamaraReachabilityResponse.is_reachable` reads
both, so the difference stops at the integration boundary.

**Live is the default.** `CARRIER_MODE` defaults to `live`, so a clean
checkout brought up with `docker compose up` collects its evidence from the
real carrier. The demo operator is seeded on the carrier's own simulator
subscriber for the same reason: a number the carrier does not know is
*declined*, not answered, and a mandatory check that comes back unanswered
denies -- so seeding an arbitrary number would leave a fresh deployment unable
to release anything. No carrier is simulated inside the project: every
decision rests on what the carrier itself answered.

**A declining carrier must not read as a passing one.** The gateway records a
declined check as not collected, and the policy engine denies when any check
in the validated plan's mandatory set is missing -- whatever the reason for
the gap. Without that rule, an unknown subscriber produced an approval resting
on no network evidence at all.

**Number verification cannot be collected server-side.** CAMARA identifies the
subscriber from a three-legged token minted over the device's own mobile
connection, not from the phone number in the request body; called from a
server the gateway answers `MISSING_IDENTIFIER`. The live client therefore
declines the check rather than reporting `False`, which would read as the
carrier denying the number. The Evidence Gateway records it as **not
collected**, and because Rule 2 refuses to read missing evidence as a pass, a
check is declared unattestable for this carrier, which does two things: the
plan validator stops demanding evidence nobody can collect, and every
decision made without it carries a caveat naming what it does not rest on. It
is not a rule being switched off -- a carrier that *answers* the check with a
refusal still denies, and the other four checks are still enforced in full.
Collecting it for real requires the handset to complete the CAMARA
authorization flow and pass the resulting token up with the release request.

- In test environments, deterministic test doubles (`FakeNokiaClient`) verify all gateway authorization rules offline without external network dependency.
- `tests/test_nokia_live_client.py` pins the live tool names, argument shapes, and real response bodies, so a request shaped for the mock can no longer pass for a request the carrier would accept.

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
OLLAMA_MODEL=qwen2.5:7b

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
  ollama pull qwen2.5:7b
  ```
  `qwen2.5:7b` is the default: it asks for supplementary evidence where the
  context calls for it and writes a full assessment. `llama3.2` also runs and
  answers faster, but rarely asks for more than the mandatory baseline.
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

Docker is the only prerequisite:

```bash
docker compose up --build -d
```

- API — <http://127.0.0.1:8000>

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

For a carrier that exposes CAMARA REST directly rather than Network as Code:

```bash
CARRIER_MODE=rest CARRIER_URL=https://your-carrier.example.com CARRIER_KEY=... docker compose up -d
```

**Evaluating with your own Nokia key.** No key ships with the project. Live mode
(`CARRIER_MODE=live`, the default) sends every carrier check to the Nokia
Network as Code gateway using the key you provide, and refuses to start on an
empty or placeholder key rather than quietly deciding on nothing:

```bash
CARRIER_KEY=<your RapidAPI key for network-as-code> docker compose up -d
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

An unenrolled actor is denied by design. Enrolment is a console action, so sign
in as an authority first and send the session cookie with the request:

```bash
curl -c jar.txt -X POST http://127.0.0.1:8000/v1/auth/login -H 'Content-Type: application/json' -d '{"actor_id":"usr_cargo_supervisor_01","password":"zonegate-demo"}'
curl -b jar.txt -X POST http://127.0.0.1:8000/v1/actors -H 'Content-Type: application/json'   -d '{"actor":{"actor_id":"usr_cargo_operator_02","role":"ROLE_CARGO_OPERATOR","permissions":["cargo:release","cargo:inspect"],"registered_phone_number":"+14155550199","registered_device_id":"dev_imei_99887766","enrollment_status":"ACTIVE"}}'
```

There is also a seed script. Zova is an **embedded** database: if the API
server holds the file open, writes from a second process are silently lost with
no error. Stop the server first:

```bash
docker compose stop zonegate
docker compose run --rm zonegate python scripts/seed.py
docker compose start zonegate
```

### 2. Start the dashboard

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

### Who uses which surface
ZoneGate has two audiences. **Cargo personnel** (`CARGO_OPERATOR`) request
releases from the mobile app. **Authorities** -- supervisors and officers --
decide on the web console. The two never share a screen:

- A cargo operator cannot sign in to the console, even with a correct password.
  A session opened for one earlier stands for nobody.
- Enrolment, permission edits, policy changes and hold resolution need a
  signed-in console session (401 without one).
- A hold can only be resolved by the role the engine handed it to: a
  `ROLE_SECURITY_OFFICER` cannot settle a hold for `ROLE_CARGO_SUPERVISOR`
  (403). The resolution is recorded under the signed-in actor, never a name
  the client sends.
- Roles compare without their `ROLE_` prefix, so `CARGO_OPERATOR` and
  `ROLE_CARGO_OPERATOR` are the same job.

Reading decisions stays open, because the mobile app reads them without a
console session.

### Console sign-in
The console is reachable only to somebody already on the enrolled roster; there
is no registration endpoint and there will not be one. The session lives in an
httpOnly cookie, so no page script can read it.

- `POST /v1/auth/login`: Signs an enrolled authority in and sets the session cookie.
- `GET /v1/auth/session`: The signed-in actor, or 401 when nobody is.
- `POST /v1/auth/logout`: Revokes the session server-side and clears the cookie.
- `POST /v1/auth/password`: Changes the signed-in actor's own console password.

On a fresh database one console account is seeded for each authority the
default policy escalates to, all with `DEMO_OPERATOR_PASSWORD` (default
`zonegate-demo`):

| Account | Role | Settles |
|---|---|---|
| `usr_cargo_supervisor_01` | `ROLE_CARGO_SUPERVISOR` | HIGH_VALUE cargo |
| `usr_security_officer_01` | `ROLE_SECURITY_OFFICER` | WEAPONS, recent SIM swaps |
| `usr_safety_officer_01` | `ROLE_SAFETY_OFFICER` | HAZARDOUS |
| `usr_compliance_officer_01` | `ROLE_COMPLIANCE_OFFICER` | CONTROLLED_SUBSTANCE |

The seeded field operator `usr_cargo_operator_01` has no console access; it
signs in to the mobile app.

### Actors
- `POST /v1/actors`: Enrolls an actor and binds their device. Required before any request can pass the gateway. An optional `password` also gives them console access.
- `GET /v1/actors`: The enrolled roster with each actor's binding.
- `GET /v1/actors/{actor_id}`: Retrieves an enrolled actor with their active device binding.
- `PUT /v1/actors/{actor_id}/permissions`: Replaces an actor's permissions. `expected_permissions` is what the editor was showing; a mismatch is refused with 409 rather than overwriting a concurrent edit.

### Policy
- `GET /v1/policy/config`: The restricted-category map the engine is running with.
- `PUT /v1/policy/config`: Saves it, and rebinds the running engine. The retired `window_start_hour` / `window_end_hour` fields are accepted and ignored.
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
