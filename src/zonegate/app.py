from contextlib import asynccontextmanager
import logging
from typing import AsyncGenerator
from litestar import Litestar
from litestar.datastructures import State
from litestar.di import Provide
from litestar.logging import LoggingConfig
from zonegate.agent.base import LLMClientProtocol
from zonegate.agent.context_evaluator import ContextEvaluator
from zonegate.agent.evidence_planner import EvidencePlanner
from zonegate.agent.gemini import GeminiClient
from zonegate.agent.ollama import OllamaClient
from zonegate.api.authorization import AuthorizationController
from zonegate.api.health import health_check
from zonegate.api.receipts import ReceiptsController
from zonegate.authorization.service import AuthorizationService
from zonegate.authorization.token import TokenService
from zonegate.config import Settings, get_settings
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
        # Initialize Zova 1.0.0-rc2 embedded storage
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
        token_service = TokenService()
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
            }
        }
    )

    return Litestar(
        route_handlers=[health_check, AuthorizationController, ReceiptsController],
        dependencies={
            "store": Provide(provide_store, sync_to_thread=False),
            "llm_client": Provide(provide_llm, sync_to_thread=False),
            "auth_service": Provide(provide_auth_service, sync_to_thread=False),
        },
        lifespan=[lifespan],
        logging_config=logging_config,
    )


app = create_app()
