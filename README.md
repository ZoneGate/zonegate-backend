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
GEMINI_MODEL=gemini-2.5-flash

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
To build and start the container with persistent storage and environment configuration automatically:
```bash
docker compose up --build -d
```

View application logs:
```bash
docker compose logs -f zonegate
```

Stop the container:
```bash
docker compose down
```

---

## API Reference

### Health
- `GET /health`: Returns service health and reachability of dependencies (Zova, Ollama, Nokia).

### Authorizations
- `POST /v1/authorizations`: Evaluates a transaction request through the full authorization pipeline. Returns the authoritative `PolicyDecision` and an audit `Receipt`.
- `GET /v1/authorizations/{decision_id}`: Retrieves an existing policy decision record from Zova.

### Receipts
- `GET /v1/receipts/{receipt_id}`: Retrieves an issued action receipt (including scoped authorization token if approved).
