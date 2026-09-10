from contextlib import asynccontextmanager
import logging
from typing import AsyncGenerator
from litestar import Litestar
from litestar.datastructures import State
from litestar.di import Provide
from litestar.logging import LoggingConfig
from datetime import datetime, timezone
from zonegate.agent.base import LLMClientProtocol
from zonegate.agent.context_evaluator import ContextEvaluator
from zonegate.agent.evidence_planner import EvidencePlanner
from zonegate.agent.gemini import GeminiClient
from zonegate.agent.ollama import OllamaClient
from zonegate.api.actors import ActorsController, DeviceBindingsController
from zonegate.api.authorization import AuthorizationController
from zonegate.api.health import health_check
from zonegate.api.receipts import ReceiptsController
from zonegate.api.tokens import TokensController
from zonegate.authorization.service import AuthorizationService
from zonegate.authorization.token import TokenService
from zonegate.config import Settings, get_settings
from zonegate.domain.actors import Actor, DeviceBinding
from zonegate.evidence.gateway import EvidenceGateway
from zonegate.evidence.plan_validator import EvidencePlanValidator
from zonegate.integrations.nokia.client import NokiaEvidenceClient
from zonegate.integrations.nokia.mcp import NokiaMCPClient
from zonegate.policy.engine import PolicyEngine
from zonegate.storage.zova import ZoneGateStore

logger = logging.getLogger("zonegate")


def provide_store(state: State) -> ZoneGateStore:
    return state.store


def provide_llm(state: State) -> LLMClientProtocol:
    return state.llm_client


def provide_auth_service(state: State) -> AuthorizationService:
    return state.auth_service


def create_app(settings: Settings | None = None) -> Litestar:
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: Litestar) -> AsyncGenerator[None, None]:
        # Initialize Zova 1.0.0 embedded storage
        store = ZoneGateStore.open_or_create(cfg.ZOVA_DB_PATH)

        # Select LLM Provider: Gemini or Ollama
        llm_client: LLMClientProtocol
        if cfg.LLM_PROVIDER.lower() == "gemini":
            llm_client = GeminiClient(
                api_key=cfg.GEMINI_API_KEY,
                model=cfg.GEMINI_MODEL,
            )
            logger.info("Using Gemini AI provider with model '%s'", cfg.GEMINI_MODEL)
        else:
            llm_client = OllamaClient(
                base_url=cfg.OLLAMA_BASE_URL,
                model=cfg.OLLAMA_MODEL,
            )
            logger.info("Using Ollama AI provider at '%s'", cfg.OLLAMA_BASE_URL)

        nokia_client = NokiaEvidenceClient(
            base_url=cfg.NOKIA_BASE_URL,
            api_key=cfg.NOKIA_API_KEY,
        )

        # Initialize Nokia Network as Code MCP client with static allowlist
        nokia_mcp_client = None
        if cfg.NOKIA_MCP_ENABLED:
            allowlist = {s.strip() for s in cfg.NOKIA_MCP_STATIC_ALLOWLIST.split(",") if s.strip()}
            nokia_mcp_client = NokiaMCPClient(
                config_path=cfg.NOKIA_MCP_CONFIG_PATH,
                static_allowlist=allowlist,
            )

        gateway = EvidenceGateway(nokia_client=nokia_client)
        plan_validator = EvidencePlanValidator()
        policy_engine = PolicyEngine()
        token_service = TokenService(secret_key=cfg.TOKEN_SECRET_KEY)
        planner = EvidencePlanner(llm_client=llm_client)
        evaluator = ContextEvaluator(llm_client=llm_client, mcp_client=nokia_mcp_client)

        auth_service = AuthorizationService(
            store=store,
            gateway=gateway,
            plan_validator=plan_validator,
            policy_engine=policy_engine,
            token_service=token_service,
            evidence_planner=planner,
            context_evaluator=evaluator,
        )

        app.state.store = store
        app.state.llm_client = llm_client
        app.state.nokia_client = nokia_client
        app.state.nokia_mcp_client = nokia_mcp_client
        app.state.gateway = gateway
        app.state.auth_service = auth_service

        logger.info("ZoneGate backend initialized with Zova at '%s'", cfg.ZOVA_DB_PATH)

        # Ensure default demo actor & device binding exist if database is fresh
        if await store.get_actor("usr_cargo_operator_01") is None:
            demo_actor = Actor(
                actor_id="usr_cargo_operator_01",
                role="CARGO_OPERATOR",
                permissions=["cargo:release", "cargo:inspect"],
                registered_phone_number="+358501234567",
                registered_device_id="device_cargo_terminal_01",
                enrollment_status="ACTIVE",
            )
            demo_binding = DeviceBinding(
                actor_id=demo_actor.actor_id,
                phone_number=demo_actor.registered_phone_number,
                device_id=demo_actor.registered_device_id,
                bound_at=datetime.now(timezone.utc),
                is_active=True,
            )
            await store.save_actor(demo_actor)
            await store.save_device_binding(demo_binding)
            logger.info("Auto-seeded default demo actor 'usr_cargo_operator_01' into Zova storage")

        try:
            yield
        finally:
            store.close()
            logger.info("ZoneGate storage closed cleanly")

    logging_config = LoggingConfig(
        loggers={
            "zonegate": {
                "level": "INFO",
                "handlers": ["console"],
                "propagate": False,
            }
        }
    )

    return Litestar(
        route_handlers=[
            health_check,
            AuthorizationController,
            ReceiptsController,
            TokensController,
            ActorsController,
            DeviceBindingsController,
        ],
        dependencies={
            "store": Provide(provide_store, sync_to_thread=False),
            "llm_client": Provide(provide_llm, sync_to_thread=False),
            "auth_service": Provide(provide_auth_service, sync_to_thread=False),
        },
        lifespan=[lifespan],
        logging_config=logging_config,
    )


app = create_app()
