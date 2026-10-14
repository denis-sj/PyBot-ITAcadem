from collections.abc import AsyncGenerator

import httpx2
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from dishka.integrations.aiogram import AiogramProvider
from dishka.integrations.taskiq import TaskiqProvider
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ..core import logger
from ..core.config import AppSettings, get_settings
from ..db.database import create_database_engine
from ..domain.services import AttendanceRankingCalculator, LevelCalculator
from ..infrastructure import (
    AttendanceRepository,
    CommunityAchievementRepository,
    CompetenceRepository,
    LevelRepository,
    PointsTransactionRepository,
    RoleRepository,
    RoleRequestRepository,
    UserRepository,
    ValuationRepository,
)
from ..infrastructure.adapters import PolzaVisionAdapter
from ..infrastructure.adapters.agent_memory import RedisAgentMemory
from ..infrastructure.adapters.polza_agent import PolzaAgentModel
from ..infrastructure.health import RedisPingProbe, SessionExecutor
from ..infrastructure.ports import LoggingNotificationService, TelegramNotificationService
from ..infrastructure.taskiq.taskiq_community_achievement_dispatcher import TaskIQCommunityAchievementDispatcher
from ..infrastructure.taskiq.taskiq_notification_dispatcher import TaskIQNotificationDispatcher
from ..services import (
    AttendanceService,
    CommunityAchievementService,
    LeaderboardService,
    SystemRuntimeAlertsService,
    UserCompetenceService,
    UserProfileService,
    UserRegistrationService,
    UserRolesService,
    UserService,
    WeeklyLeaderboardPublisherService,
)
from ..services.agent.actions import AgentActionService
from ..services.agent.reads import AgentReadService
from ..services.agent.runtime import AgentRuntime
from ..services.agent.service import AgentService
from ..services.agent.tools import AgentToolRegistry
from ..services.broadcast import BroadcastService
from ..services.competence import CompetenceService
from ..services.health import HealthService
from ..services.levels import LevelService
from ..services.notification_facade import NotificationFacade
from ..services.points import PointsService
from ..services.ports import (
    AttendanceRecognitionPort,
    CommunityAchievementDispatchPort,
    NotificationDispatchPort,
    NotificationPort,
)
from ..services.ports.agent import AgentMemoryPort, AgentModelPort
from ..services.role_request import RoleRequestService

global_engine: AsyncEngine | None = None


class DatabaseProvider(Provider):
    """Providers for database resources."""

    @provide(scope=Scope.APP)
    async def engine(self, settings: AppSettings) -> AsyncGenerator[AsyncEngine]:
        """Provide one SQLAlchemy engine for the whole app lifecycle."""
        engine = create_database_engine(settings.database_url) if global_engine is None else global_engine
        try:
            yield engine
        finally:
            await engine.dispose()
            logger.info("Database engine disposed")


class SessionProvider(Provider):
    """Provide one DB session per request/update."""

    @provide(scope=Scope.REQUEST)
    async def session(self, engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
        """Create request-scoped async SQLAlchemy session."""
        session_maker = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with session_maker() as session:
            yield session


class RepositoryProvider(Provider):
    """Stateless repositories with APP scope."""

    @provide(scope=Scope.APP)
    def user_repository(self) -> UserRepository:
        return UserRepository()

    @provide(scope=Scope.APP)
    def level_repository(self) -> LevelRepository:
        return LevelRepository()

    @provide(scope=Scope.APP)
    def valuation_repository(self) -> ValuationRepository:
        return ValuationRepository()

    @provide(scope=Scope.APP)
    def points_transaction_repository(self) -> PointsTransactionRepository:
        return PointsTransactionRepository()

    @provide(scope=Scope.APP)
    def role_repository(self) -> RoleRepository:
        return RoleRepository()

    @provide(scope=Scope.APP)
    def role_request_repository(self) -> RoleRequestRepository:
        return RoleRequestRepository()

    @provide(scope=Scope.APP)
    def competence_repository(self) -> CompetenceRepository:
        return CompetenceRepository()

    @provide(scope=Scope.APP)
    def attendance_repository(self) -> AttendanceRepository:
        return AttendanceRepository()

    @provide(scope=Scope.APP)
    def community_achievement_repository(self) -> CommunityAchievementRepository:
        return CommunityAchievementRepository()


class ServiceProvider(Provider):
    """Application services."""

    @provide(scope=Scope.REQUEST)
    def user_service(
        self,
        db: AsyncSession,
        user_repository: UserRepository,
    ) -> UserService:
        return UserService(db, user_repository)

    @provide(scope=Scope.REQUEST)
    def user_roles_service(  # noqa: PLR0913
        self,
        db: AsyncSession,
        user_repository: UserRepository,
        role_repository: RoleRepository,
        role_request_repository: RoleRequestRepository,
        notification_service: NotificationPort,
        achievement_dispatch_port: CommunityAchievementDispatchPort,
    ) -> UserRolesService:
        return UserRolesService(
            db=db,
            user_repository=user_repository,
            role_repository=role_repository,
            role_request_repository=role_request_repository,
            notification_service=notification_service,
            achievement_dispatch_port=achievement_dispatch_port,
        )

    @provide(scope=Scope.REQUEST)
    def user_competence_service(
        self,
        db: AsyncSession,
        user_repository: UserRepository,
        competence_repository: CompetenceRepository,
    ) -> UserCompetenceService:
        return UserCompetenceService(db, user_repository, competence_repository)

    @provide(scope=Scope.REQUEST)
    def points_service(
        self,
        db: AsyncSession,
        level_calculator: LevelCalculator,
        user_repository: UserRepository,
        level_repository: LevelRepository,
        points_transaction_repository: PointsTransactionRepository,
    ) -> PointsService:
        return PointsService(db, level_calculator, user_repository, level_repository, points_transaction_repository)

    @provide(scope=Scope.REQUEST)
    def leaderboard_service(
        self,
        db: AsyncSession,
        points_transaction_repository: PointsTransactionRepository,
    ) -> LeaderboardService:
        return LeaderboardService(db, points_transaction_repository)

    @provide(scope=Scope.REQUEST)
    def weekly_leaderboard_publisher_service(
        self,
        leaderboard_service: LeaderboardService,
        notification_service: NotificationPort,
    ) -> WeeklyLeaderboardPublisherService:
        return WeeklyLeaderboardPublisherService(leaderboard_service, notification_service)

    @provide(scope=Scope.REQUEST)
    def role_request_service(  # noqa: PLR0913
        self,
        db: AsyncSession,
        role_repository: RoleRepository,
        user_repository: UserRepository,
        role_request_repository: RoleRequestRepository,
        notification_service: NotificationPort,
        settings: AppSettings,
        achievement_dispatch_port: CommunityAchievementDispatchPort,
    ) -> RoleRequestService:
        return RoleRequestService(
            db,
            role_repository,
            user_repository,
            role_request_repository,
            notification_service,
            settings,
            achievement_dispatch_port,
        )

    @provide(scope=Scope.REQUEST)
    def broadcast_service(
        self,
        db: AsyncSession,
        user_repository: UserRepository,
        notification_service: NotificationPort,
        settings: AppSettings,
    ) -> BroadcastService:
        return BroadcastService(db, user_repository, notification_service, settings)

    @provide(scope=Scope.REQUEST)
    def competence_service(
        self,
        db: AsyncSession,
        competence_repository: CompetenceRepository,
    ) -> CompetenceService:
        return CompetenceService(db, competence_repository)

    @provide(scope=Scope.REQUEST)
    def level_service(self, db: AsyncSession, level_repository: LevelRepository) -> LevelService:
        return LevelService(level_repository, db)

    @provide(scope=Scope.REQUEST)
    def user_profile_service(
        self,
        level_service: LevelService,
        user_competence_service: UserCompetenceService,
        user_roles_service: UserRolesService,
        community_achievement_service: CommunityAchievementService,
    ) -> UserProfileService:
        return UserProfileService(
            level_service,
            user_competence_service,
            user_roles_service,
            community_achievement_service,
        )

    @provide(scope=Scope.REQUEST)
    def user_registration_service(  # noqa: PLR0913
        self,
        db: AsyncSession,
        user_repository: UserRepository,
        level_repository: LevelRepository,
        role_repository: RoleRepository,
        competence_repository: CompetenceRepository,
        settings: AppSettings,
    ) -> UserRegistrationService:
        return UserRegistrationService(
            db, user_repository, level_repository, role_repository, competence_repository, settings
        )

    @provide(scope=Scope.REQUEST)
    def attendance_service(  # noqa: PLR0913
        self,
        db: AsyncSession,
        attendance_repository: AttendanceRepository,
        user_repository: UserRepository,
        recognition_port: AttendanceRecognitionPort,
        settings: AppSettings,
        ranking_calculator: AttendanceRankingCalculator,
        notification_service: NotificationPort,
        achievement_dispatch_port: CommunityAchievementDispatchPort,
    ) -> AttendanceService:
        return AttendanceService(
            db=db,
            attendance_repo=attendance_repository,
            user_repo=user_repository,
            recognition_port=recognition_port,
            settings=settings,
            ranking_calculator=ranking_calculator,
            notification_service=notification_service,
            achievement_dispatch_port=achievement_dispatch_port,
        )

    @provide(scope=Scope.REQUEST)
    def community_achievement_service(  # noqa: PLR0913
        self,
        db: AsyncSession,
        community_achievement_repository: CommunityAchievementRepository,
        attendance_repository: AttendanceRepository,
        attendance_service: AttendanceService,
        user_repository: UserRepository,
        notification_service: NotificationPort,
        settings: AppSettings,
    ) -> CommunityAchievementService:
        return CommunityAchievementService(
            db=db,
            community_achievement_repository=community_achievement_repository,
            attendance_repository=attendance_repository,
            attendance_service=attendance_service,
            user_repository=user_repository,
            notification_service=notification_service,
            settings=settings,
        )


class AgentProvider(Provider):
    """Agent dependencies exist only in the bot runtime; no network at construction."""

    @provide(scope=Scope.APP)
    async def model(self, settings: AppSettings) -> AsyncGenerator[AgentModelPort]:
        async with httpx2.AsyncClient(timeout=settings.agent_timeout_s) as client:
            yield PolzaAgentModel(settings, client)

    @provide(scope=Scope.APP)
    def memory(self, redis_client: Redis, settings: AppSettings) -> AgentMemoryPort:
        return RedisAgentMemory(redis_client, settings)

    runtime = provide(AgentRuntime, scope=Scope.APP)

    @provide(scope=Scope.APP)
    def registry(self, settings: AppSettings) -> AgentToolRegistry:
        return AgentToolRegistry(broadcast_allowed_roles=frozenset(settings.broadcast_allowed_roles))

    @provide(scope=Scope.REQUEST)
    def reads(  # noqa: PLR0913
        self,
        profiles: UserProfileService,
        attendance: AttendanceService,
        leaderboard: LeaderboardService,
        roles: UserRolesService,
        user_competences: UserCompetenceService,
        competences: CompetenceService,
        users: UserService,
        community_achievements: CommunityAchievementService,
    ) -> AgentReadService:
        return AgentReadService(
            profiles,
            attendance,
            leaderboard,
            roles,
            user_competences,
            competences,
            users,
            community_achievements,
        )

    @provide(scope=Scope.REQUEST)
    def actions(  # noqa: PLR0913
        self,
        role_request_service: RoleRequestService,
        users: UserService,
        points: PointsService,
        roles: UserRolesService,
        user_competences: UserCompetenceService,
        competences: CompetenceService,
        attendance: AttendanceService,
        notifications: NotificationFacade,
        broadcasts: BroadcastService,
        settings: AppSettings,
    ) -> AgentActionService:
        return AgentActionService(
            role_request_service,
            users,
            points,
            roles,
            user_competences,
            competences,
            attendance,
            notifications,
            broadcasts,
            settings,
        )

    @provide(scope=Scope.REQUEST)
    def service(  # noqa: PLR0913
        self,
        users: UserService,
        model: AgentModelPort,
        memory: AgentMemoryPort,
        runtime: AgentRuntime,
        registry: AgentToolRegistry,
        settings: AppSettings,
        reads: AgentReadService,
        actions: AgentActionService,
    ) -> AgentService:
        return AgentService(users, model, memory, runtime, registry, settings, reads, actions)


class HealthProvider(Provider):
    """Health API services."""

    @provide(scope=Scope.REQUEST)
    def health_service(self, db: AsyncSession, redis_client: Redis) -> HealthService:
        return HealthService(SessionExecutor(db), RedisPingProbe(redis_client))


class RedisProvider(Provider):
    """Provide Redis resources for runtimes that need a direct client."""

    @provide(scope=Scope.APP)
    async def redis_client(self, settings: AppSettings) -> AsyncGenerator[Redis]:
        client = Redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        try:
            yield client
        finally:
            await client.aclose()
            logger.info("Redis client disposed")


class DomainServiceProvider(Provider):
    """Domain services."""

    @provide(scope=Scope.APP)
    def level_calculator(self) -> LevelCalculator:
        return LevelCalculator()

    @provide(scope=Scope.APP)
    def attendance_ranking_calculator(self) -> AttendanceRankingCalculator:
        return AttendanceRankingCalculator()


class ConfigProvider(Provider):
    """Configuration object."""

    @provide(scope=Scope.APP)
    def config(self) -> AppSettings:
        return get_settings()


class BotProvider(Provider):
    """Telegram Bot provider with APP scope."""

    @provide(scope=Scope.APP)
    async def bot(self, settings: AppSettings) -> AsyncGenerator[Bot]:
        if settings.telegram_proxy_url is not None:
            bot = Bot(
                settings.active_bot_token,
                session=AiohttpSession(proxy=settings.telegram_proxy_url),
            )
        else:
            bot = Bot(settings.active_bot_token)
        try:
            yield bot
        finally:
            await bot.session.close()
            logger.info("Bot session closed")


class PortsProvider(Provider):
    @provide(scope=Scope.APP)
    async def notification_port(self, settings: AppSettings, bot: Bot) -> NotificationPort:
        if settings.notification_backend == "telegram":
            return TelegramNotificationService(bot, settings)
        elif settings.notification_backend == "logging":
            return LoggingNotificationService(settings)
        else:
            raise ValueError(f"Unsupported NOTIFICATION_BACKEND value: {settings.notification_backend}")

    @provide(scope=Scope.APP)
    async def notification_dispatch_port(self) -> NotificationDispatchPort:
        return TaskIQNotificationDispatcher()

    @provide(scope=Scope.APP)
    def community_achievement_dispatch_port(self) -> CommunityAchievementDispatchPort:
        return TaskIQCommunityAchievementDispatcher()

    @provide(scope=Scope.APP)
    def attendance_recognition_port(self, settings: AppSettings) -> AttendanceRecognitionPort:
        return PolzaVisionAdapter(
            api_key=settings.polza_api_key.get_secret_value() if settings.polza_api_key is not None else None,
            base_url=settings.polza_base_url,
            model=settings.polza_vision_model,
            timeout_s=settings.polza_timeout_s,
        )


class FacadeProvider(Provider):
    @provide(scope=Scope.APP)
    async def notification_facade(self, notification_port: NotificationDispatchPort) -> NotificationFacade:
        return NotificationFacade(notification_port)

    @provide(scope=Scope.APP)
    async def system_runtime_alerts_service(
        self,
        notification_facade: NotificationFacade,
        notification_service: NotificationPort,
        settings: AppSettings,
    ) -> SystemRuntimeAlertsService:
        return SystemRuntimeAlertsService(notification_facade, notification_service, settings)


async def setup_container() -> AsyncContainer:
    """Build the app DI container."""
    return make_async_container(
        DatabaseProvider(),
        SessionProvider(),
        RepositoryProvider(),
        ServiceProvider(),
        AiogramProvider(),
        AgentProvider(),
        DomainServiceProvider(),
        BotProvider(),
        PortsProvider(),
        FacadeProvider(),
        ConfigProvider(),
        RedisProvider(),
    )


def setup_health_container() -> AsyncContainer:
    """Build DI container for health API (minimal set)."""
    return make_async_container(
        DatabaseProvider(),
        SessionProvider(),
        RedisProvider(),
        HealthProvider(),
        ConfigProvider(),
    )


def setup_taskiq_container() -> AsyncContainer:
    """Build DI container for TaskIQ worker runtime."""
    return make_async_container(
        DatabaseProvider(),
        SessionProvider(),
        RepositoryProvider(),
        ServiceProvider(),
        DomainServiceProvider(),
        BotProvider(),
        PortsProvider(),
        TaskiqProvider(),
        ConfigProvider(),
    )
