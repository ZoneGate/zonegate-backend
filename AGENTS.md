# ZoneGate Agent Architecture & Operational Guidelines

This document provides operational rules, architectural boundaries, and safety invariants for AI agents and human developers maintaining or extending ZoneGate.

---

## 1. System Mission & Core Principle

ZoneGate is a **network-attested authorization system for critical human actions**. It binds telecommunications carrier network telemetry (CAMARA / Nokia Network As Code) to enterprise access control.

> [!IMPORTANT]
> **The AI is advisory only.**  
> Under no circumstances may an AI agent:
> - Directly authorize actions or emit final approval decisions.
> - Issue or sign authorization tokens.
> - Execute protected physical or operational actions.
> - Mutate or bypass deterministic policies.
> - Directly invoke Nokia/CAMARA carrier APIs or access raw network credentials.

---

## 2. End-to-End Pipeline & Security Boundaries

```
Transaction Request
  ↓
[ZoneGate AuthorizationService]
  ↓
1. AI Evidence Planning (Advisory)
  ↓
2. Evidence Plan Validation (Deterministic Gate: mandatory baseline enforced)
  ↓
3. Nokia Evidence Gateway (Security Boundary: actor-device binding, capability check)
  ↓
4. Carrier Network Invocations (CAMARA APIs)
  ↓
5. Canonical Evidence Normalization (Sanitized typed domain model)
  ↓
6. AI Context Evaluation (Advisory Risk Assessment)
  ↓
7. Deterministic Policy Engine (Authoritative Decision: LLM-Free)
  ↓
APPROVE / HOLD / DENY
  ↓
[APPROVE: Scoped Authorization Token]
[HOLD: Designated Operational Human Authority Transfer]
[DENY: Unoverrideable Rejection]
```

---

## 3. Strict Hard Version & Dependency Constraints

- **Python Runtime**: `CPython 3.14` (`requires-python = "==3.14.*"` in [pyproject.toml](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/pyproject.toml)). Do not downgrade Python.
- **Persistence Engine**: `Zova 1.0.0` (`zova = "1.0.0"`). Do not substitute SQLAlchemy, SQLite, Redis, or other databases.
- **HTTP Framework**: Litestar 2.x.
- **Domain Modeling**: Pydantic v2 with `ConfigDict(extra="forbid")`.
- **Package Manager**: `uv` exclusively.

---

## 4. Key Architectural Invariants

### Invariant A: Mandatory vs. Optional vs. Forbidden Evidence
Workflows define immutable evidence constraints ([`workflow_policy.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/evidence/workflow_policy.py)):
- For `RELEASE_CARGO`:
  - **Mandatory**: `NUMBER_VERIFICATION`, `LOCATION_VERIFICATION`. Always collected regardless of what the AI proposes or if the AI fails.
  - **Optional**: `SIM_SWAP`, `DEVICE_SWAP`, `REACHABILITY`. The AI Evidence Planner may select only from this list.
  - **Forbidden**: `KYC_MATCH`. If proposed by the AI, [`EvidencePlanValidator`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/evidence/plan_validator.py) raises [`ForbiddenEvidenceError`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/evidence/plan_validator.py#L9) and immediately halts with a `DENY`.

### Invariant B: AI Fails Safely
If an LLM runtime (Ollama or Gemini) is down, times out, or produces malformed structured output:
- It **never** causes an implicit `APPROVE`.
- Evidence planning safely falls back to the mandatory baseline.
- Context evaluation reports `AI_EVALUATION_UNAVAILABLE`.
- Deterministic policy evaluates network facts without AI recommendation.

### Invariant C: Policy Authority
Deterministic policy rules always override AI recommendations:
- `AI recommends APPROVE` + `location_verified == False` = **`DENY`**.
- `AI recommends APPROVE` + `number_verified != True` = **`DENY`**.
- `AI recommends APPROVE` + `permission mismatch` = **`DENY`**.
- `restricted category` = **`HOLD`** (requires the authority named for that category). The hour of a request never holds it.
- `recent_sim_swap` + `high_value` = **`HOLD`** (requires security officer transfer).

### Invariant D: Zova Thread Pinning
Zova's PyO3 database handle (`PyDatabase`) is unsendable across OS threads (`!Send`).  
[`ZoneGateStore`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/storage/zova.py) encapsulates the database handle inside a dedicated single-threaded worker executor (`ThreadPoolExecutor(max_workers=1)`). All database operations must be dispatched through this worker.

### Invariant E: Nokia MCP Static Allowlist
When the Context Evaluation Agent invokes carrier tools via the Nokia Network as Code MCP server (`mcp-remote`):
- All tool executions are validated against [`NokiaMCPClient.static_allowlist`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/integrations/nokia/mcp.py#L38-L44).
- If the allowlist is empty, all tools provided by the server are eligible.
- If non-empty, any call to an unlisted tool is blocked and raises [`UnauthorizedMCPToolError`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/integrations/nokia/mcp.py#L14-L16).

---

## 5. Codebase Map

| Subsystem | Key Files | Responsibility |
| :--- | :--- | :--- |
| **API** | [`app.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/app.py)<br>[`health.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/api/health.py)<br>[`authorization.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/api/authorization.py) | Litestar app, dependency injection, `/health`, and `/v1/authorizations`. |
| **Domain** | [`actors.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/domain/actors.py)<br>[`transactions.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/domain/transactions.py)<br>[`evidence.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/domain/evidence.py)<br>[`decisions.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/domain/decisions.py) | Strict Pydantic domain models (`Actor`, `DeviceBinding`, `TransactionRequest`, `CanonicalEvidence`, `PolicyDecision`). |
| **Evidence Gateway** | [`workflow_policy.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/evidence/workflow_policy.py)<br>[`plan_validator.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/evidence/plan_validator.py)<br>[`gateway.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/evidence/gateway.py)<br>[`canonical.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/evidence/canonical.py) | Enforces mandatory baseline, verifies actor-device bindings, dispatches carrier calls, normalizes telemetry. |
| **Nokia / CAMARA** | [`models.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/integrations/nokia/models.py)<br>[`client.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/integrations/nokia/client.py)<br>[`mcp.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/integrations/nokia/mcp.py) | CAMARA HTTP client (`httpx.AsyncClient`) and Nokia Network as Code MCP client with static allowlist. |
| **AI Agents** | [`base.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/agent/base.py)<br>[`ollama.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/agent/ollama.py)<br>[`gemini.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/agent/gemini.py)<br>[`evidence_planner.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/agent/evidence_planner.py)<br>[`context_evaluator.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/agent/context_evaluator.py)<br>[`graph.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/agent/graph.py) | Dual-provider AI runtime (local Ollama or Google GenAI SDK) driving LangGraph advisory stages and MCP tool calling. |
| **Policy Engine** | [`engine.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/policy/engine.py) | Deterministic, rule-based decision evaluator (`APPROVE`, `HOLD`, `DENY`). |
| **Authorization** | [`service.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/authorization/service.py)<br>[`token.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/authorization/token.py) | End-to-end pipeline coordinator and single-use scoped HMAC authorization token issuer. |
| **Persistence** | [`zova.py`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/storage/zova.py) | Zova 1.0.0 embedded KV store with namespaced partitions and atomic commits. |

---

## 6. Developer & Testing Workflow

```bash
# Sync environment dependencies
uv sync

# Run the test suite (offline test doubles, zero live external dependencies required)
uv run pytest -v

# Start the Litestar HTTP server
uv run litestar run --port 8000

# Probe service health and active AI runtime
curl http://127.0.0.1:8000/health
```

---

## 7. Rules for Agents Modifying this Codebase

1. **Do not create complex multi-agent graphs**: LangGraph is strictly an advisory runner for `EvidencePlanner` and `ContextEvaluator`. Keep the graphs small and deterministic.
2. **Never leak raw Nokia API payloads into domain models**: Always normalize carrier responses through [`CanonicalEvidence`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/domain/evidence.py#L41-L67).
3. **Never allow AI to decide `APPROVE` or `DENY`**: The AI only outputs [`RecommendedControl`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/domain/decisions.py#L8-L11). Only [`PolicyEngine`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/policy/engine.py) emits [`PolicyDecision`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/domain/decisions.py#L35-L53).
4. **Never bypass [`EvidenceGateway`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/src/zonegate/evidence/gateway.py)**: The Evidence Gateway is the only authorized entity capable of making carrier calls.
5. **Keep unit tests offline**: All tests in [`tests/`](file:///Users/atasesli/Desktop/VsCode/zonegate-backend/tests/) must pass without requiring live network access or active Ollama/Gemini daemons.
