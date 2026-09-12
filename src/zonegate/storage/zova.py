import asyncio
import concurrent.futures
import json
import logging
from pathlib import Path
from typing import Callable, TypeVar
import zova
from pydantic import ValidationError
from zonegate.authorization.token import ScopedAuthorizationToken
from zonegate.domain.actors import Actor, DeviceBinding
from zonegate.domain.credentials import ConsoleSession, OperatorCredential
from zonegate.domain.policy_config import PolicyConfig
from zonegate.domain.decisions import ContextEvaluation, PolicyDecision
from zonegate.domain.evidence import CanonicalEvidence, ValidatedEvidencePlan
from zonegate.domain.receipts import Receipt
from zonegate.domain.transactions import TransactionRequest

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ZoneGateStore:
    """Narrow ZoneGate-specific storage boundary backed strictly by Zova 1.0.0.

    Translates between ZoneGate domain objects and Zova's native embedded storage.
    Note on Zova 1.0.0 Architecture:
    `zova_python::database::PyDatabase` is unsendable across OS threads (!Send).
    ZoneGateStore pins all Database lifecycle and operations to a dedicated single-threaded
    worker, ensuring thread safety and preventing cross-thread drop panics.
    """

    NS_ACTORS = b"actors"
    NS_BINDINGS = b"device_bindings"
    NS_CREDENTIALS = b"console_credentials"
    NS_SESSIONS = b"console_sessions"
    NS_TRANSACTIONS = b"transactions"
    NS_PLANS = b"evidence_plans"
    NS_EVIDENCE = b"evidence"
    NS_EVALUATIONS = b"context_evaluations"
    NS_DECISIONS = b"decisions"
    NS_DECISION_TX_INDEX = b"decision_tx_index"
    NS_RECEIPTS = b"receipts"
    NS_RECEIPT_TX_INDEX = b"receipt_tx_index"
    NS_TOKENS = b"tokens"
    NS_TOKEN_SIG_INDEX = b"token_sig_index"
    NS_INDEXES = b"indexes"
    KEY_DECISION_LIST = b"decisions"
    KEY_ACTOR_LIST = b"actors"
    KEY_POLICY_CONFIG = b"policy_config"

    def __init__(self, path_str: str) -> None:
        self._path_str = path_str
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="zova-worker",
        )
        self._db: zova.Database | None = None
        # Initialize DB on the dedicated worker thread
        self._pool.submit(self._init_db).result()

    def _init_db(self) -> None:
        if self._path_str == ":memory:":
            self._db = zova.Database.create_memory()
            return

        if not self._path_str.endswith(".zova"):
            raise ValueError(f"Zova database path must end in '.zova', got: {self._path_str}")

        path = Path(self._path_str)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self._db = zova.Database.open(str(path))
        else:
            self._db = zova.Database.create(str(path))

    @classmethod
    def create_in_memory(cls) -> "ZoneGateStore":
        return cls(":memory:")

    @classmethod
    def open_or_create(cls, path_str: str) -> "ZoneGateStore":
        return cls(path_str)

    def _run_sync(self, fn: Callable[..., T], *args) -> T:
        return self._pool.submit(fn, *args).result()

    async def _run(self, fn: Callable[..., T], *args) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, fn, *args)

    def _close_db(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    def close(self) -> None:
        try:
            self._pool.submit(self._close_db).result()
        finally:
            self._pool.shutdown(wait=True)

    # --- Actors ---

    def _get_actor_sync(self, actor_id: str) -> Actor | None:
        assert self._db is not None
        raw = self._db.kv_get(self.NS_ACTORS, actor_id.encode("utf-8"))
        if raw is None:
            return None
        return Actor.model_validate_json(raw.decode("utf-8"))

    async def get_actor(self, actor_id: str) -> Actor | None:
        return await self._run(self._get_actor_sync, actor_id)

    def _read_actor_ids(self) -> list[str]:
        assert self._db is not None
        raw = self._db.kv_get(self.NS_INDEXES, self.KEY_ACTOR_LIST)
        if raw is None:
            return []
        return json.loads(raw.decode("utf-8"))

    def _save_actor_sync(self, actor: Actor) -> None:
        assert self._db is not None
        raw = actor.model_dump_json().encode("utf-8")
        # The actor record is keyed by id, so enrolment order lives in its own
        # index; without it the roster cannot be listed, only looked up.
        ids = self._read_actor_ids()
        if actor.actor_id in ids:
            ids.remove(actor.actor_id)
        ids.insert(0, actor.actor_id)

        self._db.begin()
        try:
            self._db.kv_put(self.NS_ACTORS, actor.actor_id.encode("utf-8"), raw)
            self._db.kv_put(
                self.NS_INDEXES,
                self.KEY_ACTOR_LIST,
                json.dumps(ids).encode("utf-8"),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    async def save_actor(self, actor: Actor) -> None:
        await self._run(self._save_actor_sync, actor)

    def _list_actors_sync(self, limit: int) -> list[Actor]:
        assert self._db is not None
        found: list[Actor] = []

        for actor_id in self._read_actor_ids():
            if len(found) >= limit:
                break

            raw = self._db.kv_get(self.NS_ACTORS, actor_id.encode("utf-8"))
            if raw is None:
                continue

            found.append(Actor.model_validate_json(raw.decode("utf-8")))

        return found

    async def list_actors(self, limit: int = 200) -> list[Actor]:
        """Returns enrolled actors, most recently enrolled first."""
        return await self._run(self._list_actors_sync, limit)

    # --- Device Bindings ---

    def _get_device_binding_sync(self, actor_id: str) -> DeviceBinding | None:
        assert self._db is not None
        raw = self._db.kv_get(self.NS_BINDINGS, actor_id.encode("utf-8"))
        if raw is None:
            return None
        return DeviceBinding.model_validate_json(raw.decode("utf-8"))

    async def get_device_binding(self, actor_id: str) -> DeviceBinding | None:
        return await self._run(self._get_device_binding_sync, actor_id)

    def _save_device_binding_sync(self, binding: DeviceBinding) -> None:
        assert self._db is not None
        raw = binding.model_dump_json().encode("utf-8")
        self._db.begin()
        try:
            self._db.kv_put(self.NS_BINDINGS, binding.actor_id.encode("utf-8"), raw)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    async def save_device_binding(self, binding: DeviceBinding) -> None:
        await self._run(self._save_device_binding_sync, binding)

    # --- Console credentials & sessions ---

    def _get_credential_sync(self, actor_id: str) -> OperatorCredential | None:
        assert self._db is not None
        raw = self._db.kv_get(self.NS_CREDENTIALS, actor_id.encode("utf-8"))
        if raw is None:
            return None
        return OperatorCredential.model_validate_json(raw.decode("utf-8"))

    async def get_credential(self, actor_id: str) -> OperatorCredential | None:
        """The console password record, or None for an actor who cannot sign in."""
        return await self._run(self._get_credential_sync, actor_id)

    def _save_credential_sync(self, credential: OperatorCredential) -> None:
        assert self._db is not None
        self._db.begin()
        try:
            self._db.kv_put(
                self.NS_CREDENTIALS,
                credential.actor_id.encode("utf-8"),
                credential.model_dump_json().encode("utf-8"),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    async def save_credential(self, credential: OperatorCredential) -> None:
        await self._run(self._save_credential_sync, credential)

    def _get_session_sync(self, session_id: str) -> ConsoleSession | None:
        assert self._db is not None
        raw = self._db.kv_get(self.NS_SESSIONS, session_id.encode("utf-8"))
        if raw is None:
            return None
        return ConsoleSession.model_validate_json(raw.decode("utf-8"))

    async def get_console_session(self, session_id: str) -> ConsoleSession | None:
        return await self._run(self._get_session_sync, session_id)

    def _save_session_sync(self, session: ConsoleSession) -> None:
        assert self._db is not None
        self._db.begin()
        try:
            self._db.kv_put(
                self.NS_SESSIONS,
                session.session_id.encode("utf-8"),
                session.model_dump_json().encode("utf-8"),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    async def save_console_session(self, session: ConsoleSession) -> None:
        await self._run(self._save_session_sync, session)

    def _delete_session_sync(self, session_id: str) -> None:
        assert self._db is not None
        self._db.begin()
        try:
            self._db.kv_delete(self.NS_SESSIONS, session_id.encode("utf-8"))
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    async def delete_console_session(self, session_id: str) -> None:
        """Signing out has to actually revoke: the cookie alone is not the session."""
        await self._run(self._delete_session_sync, session_id)

    # --- Policy configuration ---

    def _get_policy_config_sync(self) -> PolicyConfig | None:
        assert self._db is not None
        raw = self._db.kv_get(self.NS_INDEXES, self.KEY_POLICY_CONFIG)
        if raw is None:
            return None

        try:
            return PolicyConfig.model_validate_json(raw.decode("utf-8"))
        except ValidationError:
            # A database written before the restricted-category map replaced
            # the value threshold. Refusing to start would strand every
            # existing deployment; the engine falls back to its defaults and
            # the console can save the shape it wants.
            logger.warning(
                "Stored policy configuration is not in the current shape and was "
                "ignored; the engine is running on defaults until it is saved again"
            )
            return None

    async def get_policy_config(self) -> PolicyConfig | None:
        """Returns the stored thresholds, or None when never configured."""
        return await self._run(self._get_policy_config_sync)

    def _save_policy_config_sync(self, config: PolicyConfig) -> None:
        assert self._db is not None
        self._db.kv_put(
            self.NS_INDEXES,
            self.KEY_POLICY_CONFIG,
            config.model_dump_json().encode("utf-8"),
        )

    async def save_policy_config(self, config: PolicyConfig) -> None:
        await self._run(self._save_policy_config_sync, config)

    # --- Transactions ---

    def _get_transaction_sync(self, transaction_id: str) -> TransactionRequest | None:
        assert self._db is not None
        raw = self._db.kv_get(self.NS_TRANSACTIONS, transaction_id.encode("utf-8"))
        if raw is None:
            return None
        return TransactionRequest.model_validate_json(raw.decode("utf-8"))

    async def get_transaction(self, transaction_id: str) -> TransactionRequest | None:
        return await self._run(self._get_transaction_sync, transaction_id)

    def _save_transaction_sync(self, tx: TransactionRequest) -> None:
        assert self._db is not None
        raw = tx.model_dump_json().encode("utf-8")
        self._db.begin()
        try:
            self._db.kv_put(self.NS_TRANSACTIONS, tx.transaction_id.encode("utf-8"), raw)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    async def save_transaction(self, tx: TransactionRequest) -> None:
        await self._run(self._save_transaction_sync, tx)

    # --- Evidence Plans ---

    def _get_evidence_plan_sync(self, transaction_id: str) -> ValidatedEvidencePlan | None:
        assert self._db is not None
        raw = self._db.kv_get(self.NS_PLANS, transaction_id.encode("utf-8"))
        if raw is None:
            return None
        return ValidatedEvidencePlan.model_validate_json(raw.decode("utf-8"))

    async def get_evidence_plan(self, transaction_id: str) -> ValidatedEvidencePlan | None:
        return await self._run(self._get_evidence_plan_sync, transaction_id)

    def _save_evidence_plan_sync(self, transaction_id: str, plan: ValidatedEvidencePlan) -> None:
        assert self._db is not None
        raw = plan.model_dump_json().encode("utf-8")
        self._db.begin()
        try:
            self._db.kv_put(self.NS_PLANS, transaction_id.encode("utf-8"), raw)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    async def save_evidence_plan(self, transaction_id: str, plan: ValidatedEvidencePlan) -> None:
        await self._run(self._save_evidence_plan_sync, transaction_id, plan)

    # --- Canonical Evidence ---

    def _get_evidence_sync(self, transaction_id: str) -> CanonicalEvidence | None:
        assert self._db is not None
        raw = self._db.kv_get(self.NS_EVIDENCE, transaction_id.encode("utf-8"))
        if raw is None:
            return None
        return CanonicalEvidence.model_validate_json(raw.decode("utf-8"))

    async def get_evidence(self, transaction_id: str) -> CanonicalEvidence | None:
        return await self._run(self._get_evidence_sync, transaction_id)

    def _save_evidence_sync(self, transaction_id: str, evidence: CanonicalEvidence) -> None:
        assert self._db is not None
        raw = evidence.model_dump_json().encode("utf-8")
        self._db.begin()
        try:
            self._db.kv_put(self.NS_EVIDENCE, transaction_id.encode("utf-8"), raw)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    async def save_evidence(self, transaction_id: str, evidence: CanonicalEvidence) -> None:
        await self._run(self._save_evidence_sync, transaction_id, evidence)

    # --- Context Evaluation ---

    def _get_context_evaluation_sync(self, transaction_id: str) -> ContextEvaluation | None:
        assert self._db is not None
        raw = self._db.kv_get(self.NS_EVALUATIONS, transaction_id.encode("utf-8"))
        if raw is None:
            return None
        return ContextEvaluation.model_validate_json(raw.decode("utf-8"))

    async def get_context_evaluation(self, transaction_id: str) -> ContextEvaluation | None:
        return await self._run(self._get_context_evaluation_sync, transaction_id)

    def _save_context_evaluation_sync(self, transaction_id: str, evaluation: ContextEvaluation) -> None:
        assert self._db is not None
        raw = evaluation.model_dump_json().encode("utf-8")
        self._db.begin()
        try:
            self._db.kv_put(self.NS_EVALUATIONS, transaction_id.encode("utf-8"), raw)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    async def save_context_evaluation(self, transaction_id: str, evaluation: ContextEvaluation) -> None:
        await self._run(self._save_context_evaluation_sync, transaction_id, evaluation)

    # --- Decisions ---

    def _get_decision_sync(self, decision_id: str) -> PolicyDecision | None:
        assert self._db is not None
        raw = self._db.kv_get(self.NS_DECISIONS, decision_id.encode("utf-8"))
        if raw is None:
            return None
        return PolicyDecision.model_validate_json(raw.decode("utf-8"))

    async def get_decision(self, decision_id: str) -> PolicyDecision | None:
        return await self._run(self._get_decision_sync, decision_id)

    def _get_decision_by_tx_sync(self, transaction_id: str) -> PolicyDecision | None:
        assert self._db is not None
        decision_id_raw = self._db.kv_get(self.NS_DECISION_TX_INDEX, transaction_id.encode("utf-8"))
        if decision_id_raw is None:
            return None
        raw = self._db.kv_get(self.NS_DECISIONS, decision_id_raw)
        if raw is None:
            return None
        return PolicyDecision.model_validate_json(raw.decode("utf-8"))

    async def get_decision_by_transaction_id(self, transaction_id: str) -> PolicyDecision | None:
        return await self._run(self._get_decision_by_tx_sync, transaction_id)

    def _read_decision_ids(self) -> list[str]:
        assert self._db is not None
        raw = self._db.kv_get(self.NS_INDEXES, self.KEY_DECISION_LIST)
        if raw is None:
            return []
        return json.loads(raw.decode("utf-8"))

    def _save_decision_sync(self, decision: PolicyDecision) -> None:
        assert self._db is not None
        raw = decision.model_dump_json().encode("utf-8")
        decision_id_b = decision.decision_id.encode("utf-8")
        tx_id_b = decision.transaction_id.encode("utf-8")

        ids = self._read_decision_ids()
        if decision.decision_id in ids:
            ids.remove(decision.decision_id)
        ids.insert(0, decision.decision_id)

        self._db.begin()
        try:
            self._db.kv_put(self.NS_DECISIONS, decision_id_b, raw)
            self._db.kv_put(self.NS_DECISION_TX_INDEX, tx_id_b, decision_id_b)
            self._db.kv_put(
                self.NS_INDEXES,
                self.KEY_DECISION_LIST,
                json.dumps(ids).encode("utf-8"),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    async def save_decision(self, decision: PolicyDecision) -> None:
        await self._run(self._save_decision_sync, decision)

    def _list_decisions_sync(
        self,
        outcome: str | None,
        limit: int,
    ) -> list[PolicyDecision]:
        assert self._db is not None
        found: list[PolicyDecision] = []

        for decision_id in self._read_decision_ids():
            if len(found) >= limit:
                break

            raw = self._db.kv_get(self.NS_DECISIONS, decision_id.encode("utf-8"))
            if raw is None:
                continue

            decision = PolicyDecision.model_validate_json(raw.decode("utf-8"))
            if outcome and decision.decision != outcome:
                continue

            found.append(decision)

        return found

    async def list_decisions(
        self,
        outcome: str | None = None,
        limit: int = 100,
    ) -> list[PolicyDecision]:
        """Returns decisions newest first, optionally filtered by outcome."""
        return await self._run(self._list_decisions_sync, outcome, limit)

    # --- Receipts ---

    def _get_receipt_sync(self, receipt_id: str) -> Receipt | None:
        assert self._db is not None
        raw = self._db.kv_get(self.NS_RECEIPTS, receipt_id.encode("utf-8"))
        if raw is None:
            return None
        return Receipt.model_validate_json(raw.decode("utf-8"))

    async def get_receipt(self, receipt_id: str) -> Receipt | None:
        return await self._run(self._get_receipt_sync, receipt_id)

    def _save_receipt_sync(self, receipt: Receipt) -> None:
        assert self._db is not None
        raw = receipt.model_dump_json().encode("utf-8")
        receipt_id_b = receipt.receipt_id.encode("utf-8")
        tx_id_b = receipt.transaction_id.encode("utf-8")

        self._db.begin()
        try:
            self._db.kv_put(self.NS_RECEIPTS, receipt_id_b, raw)
            self._db.kv_put(self.NS_RECEIPT_TX_INDEX, tx_id_b, receipt_id_b)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    async def save_receipt(self, receipt: Receipt) -> None:
        await self._run(self._save_receipt_sync, receipt)

    def _get_receipt_by_tx_sync(self, transaction_id: str) -> Receipt | None:
        assert self._db is not None
        receipt_id = self._db.kv_get(
            self.NS_RECEIPT_TX_INDEX, transaction_id.encode("utf-8")
        )
        if receipt_id is None:
            return None
        return self._get_receipt_sync(receipt_id.decode("utf-8"))

    async def get_receipt_by_transaction_id(self, transaction_id: str) -> Receipt | None:
        return await self._run(self._get_receipt_by_tx_sync, transaction_id)

    # --- Scoped Authorization Tokens ---

    def _get_token_sync(self, token_identifier: str) -> ScopedAuthorizationToken | None:
        assert self._db is not None
        key_b = token_identifier.encode("utf-8")
        raw = self._db.kv_get(self.NS_TOKENS, key_b)
        if raw is None:
            # Check secondary signature index
            token_id_b = self._db.kv_get(self.NS_TOKEN_SIG_INDEX, key_b)
            if token_id_b is not None:
                raw = self._db.kv_get(self.NS_TOKENS, token_id_b)
        if raw is None:
            return None
        return ScopedAuthorizationToken.model_validate_json(raw.decode("utf-8"))

    async def get_token(self, token_identifier: str) -> ScopedAuthorizationToken | None:
        return await self._run(self._get_token_sync, token_identifier)

    def _save_token_sync(self, token: ScopedAuthorizationToken) -> None:
        assert self._db is not None
        raw = token.model_dump_json().encode("utf-8")
        token_id_b = token.token_id.encode("utf-8")
        sig_b = token.signature.encode("utf-8")

        self._db.begin()
        try:
            self._db.kv_put(self.NS_TOKENS, token_id_b, raw)
            self._db.kv_put(self.NS_TOKEN_SIG_INDEX, sig_b, token_id_b)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    async def save_token(self, token: ScopedAuthorizationToken) -> None:
        await self._run(self._save_token_sync, token)
