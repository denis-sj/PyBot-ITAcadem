from collections.abc import AsyncGenerator

import httpx2
from aiogram import Bot
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
    UserTechnologyStackRepository,
    ValuationRepository,
)
from ..infrastructure.adapters import PolzaVisionAdapter, PydanticAITechnologyStackParser
from ..infrastructure.adapters.agent_memory import RedisAgentMemory
from ..infrastructure.adapters.polza_agent import PolzaAgentModel
from ..infrastructure.adapters.pydantic_ai_checkin_response_parser import PydanticAICheckinResponseParser
from ..infrastructure.community_goal_repository import CommunityGoalRepository
from ..infrastructure.community_meeting_repository import CommunityMeetingRepository
from ..infrastructure.health import RedisPingProbe, SessionExecutor
from ..infrastructure.ports import LoggingNotificationService, TelegramNotificationService
from ..infrastructure.ports.logging_meeting_delivery import LoggingMeetingDelivery
from ..infrastructure.ports.logging_pulse_report_delivery import LoggingPulseReportDelivery
from ..infrastructure.ports.telegram_meeting_delivery import TelegramMeetingDelivery
from ..infrastructure.ports.telegram_pulse_report_delivery import TelegramPulseReportDelivery
from ..infrastructure.taskiq.taskiq_community_achievement_dispatcher import TaskIQCommunityAchievementDispatcher
from ..infrastructure.taskiq.taskiq_notification_dispatcher import TaskIQNotificationDispatcher
from ..infrastructure.telegram_proxy_session import create_telegram_proxy_session
from ..infrastructure.user_notification_preference_repository import UserNotificationPreferenceRepository
from ..infrastructure.weekly_checkin_repository import WeeklyCheckinRepository
from ..services import (
    AttendanceService,
    CommunityAchievementService,
    LeaderboardService,
    SystemRuntimeAlertsService,
    TechnologyStackParsingService,
    TechnologyStackService,
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
from ..services.community_checkins import CommunityCheckinService
from ..services.community_goals import CommunityGoalService
from ..services.community_meetings import CommunityMeetingService
from ..services.competence import CompetenceService
from ..services.health import HealthService
from ..services.levels import LevelService
from ..services.notification_facade import NotificationFacade
from ..services.points import PointsService
from ..services.ports import (
    AttendanceRecognitionPort,
    CheckinResponseParserPort,
    CommunityAchievementDispatchPort,
    MeetingDeliveryPort,
    NotificationDispatchPort,
    NotificationPort,
    PulseReportDeliveryPort,
    TechnologyStackParserPort,
)
from ..services.ports.agent import AgentMemoryPort, AgentModelPort
from ..services.pulse_report import PulseReportService
from ..services.role_request import RoleRequestService
from ..services.user_notification_preferences import UserNotificationPreferencesService

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

    @provide(scope=Scope.APP)
    def user_technology_stack_repository(self) -> UserTechnologyStackRepository:
        return UserTechnologyStackRepository()

    @provide(scope=Scope.APP)
    def community_goal_repository(self) -> CommunityGoalRepository:
        return CommunityGoalRepository()

    @provide(scope=Scope.APP)
    def community_meeting_repository(self) -> CommunityMeetingRepository:
        return CommunityMeetingRepository()

    @provide(scope=Scope.APP)
    def user_notification_preference_repository(self) -> UserNotificationPreferenceRepository:
        return UserNotificationPreferenceRepository()

    @provide(scope=Scope.APP)
    def weekly_checkin_repository(self) -> WeeklyCheckinRepository:
        return WeeklyCheckinRepository()


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
        user_notification_preference_repository: UserNotificationPreferenceRepository,
    ) -> CommunityAchievementService:
        return CommunityAchievementService(
            db=db,
            community_achievement_repository=community_achievement_repository,
            attendance_repository=attendance_repository,
            attendance_service=attendance_service,
            user_repository=user_repository,
            notification_service=notification_service,
            settings=settings,
            preference_repository=user_notification_preference_repository,
        )

    @provide(scope=Scope.REQUEST)
    def community_goal_service(
        self,
        db: AsyncSession,
        community_goal_repository: CommunityGoalRepository,
        community_achievement_service: CommunityAchievementService,
    ) -> CommunityGoalService:
        return CommunityGoalService(db, community_goal_repository, community_achievement_service)

    @provide(scope=Scope.REQUEST)
    def user_notification_preferences_service(
        self,
        db: AsyncSession,
        user_notification_preference_repository: UserNotificationPreferenceRepository,
        community_achievement_repository: CommunityAchievementRepository,
    ) -> UserNotificationPreferencesService:
        return UserNotificationPreferencesService(
            db,
            user_notification_preference_repository,
            community_achievement_repository,
        )

    @provide(scope=Scope.REQUEST)
    def community_checkin_service(  # noqa: PLR0913
        self,
        db: AsyncSession,
        weekly_checkin_repository: WeeklyCheckinRepository,
        attendance_repository: AttendanceRepository,
        user_notification_preference_repository: UserNotificationPreferenceRepository,
        community_goal_service: CommunityGoalService,
        technology_stack_service: TechnologyStackService,
        checkin_response_parser: CheckinResponseParserPort,
        notification_port: NotificationPort,
    ) -> CommunityCheckinService:
        return CommunityCheckinService(
            db,
            weekly_checkin_repository,
            attendance_repository,
            user_notification_preference_repository,
            community_goal_service,
            technology_stack_service,
            checkin_response_parser,
            notification_port,
        )

    @provide(scope=Scope.REQUEST)
    def community_meeting_service(
        self,
        db: AsyncSession,
        community_meeting_repository: CommunityMeetingRepository,
        settings: AppSettings,
        meeting_delivery: MeetingDeliveryPort,
    ) -> CommunityMeetingService:
        return CommunityMeetingService(db, community_meeting_repository, settings, meeting_delivery)

    @provide(scope=Scope.REQUEST)
    def pulse_report_service(  # noqa: PLR0913
        self,
        db: AsyncSession,
        weekly_checkin_repository: WeeklyCheckinRepository,
        attendance_repository: AttendanceRepository,
        community_achievement_repository: CommunityAchievementRepository,
        notification_port: NotificationPort,
        pulse_report_delivery_port: PulseReportDeliveryPort,
        technology_stack_service: TechnologyStackService,
    ) -> PulseReportService:
        return PulseReportService(
            db,
            weekly_checkin_repository,
            attendance_repository,
            community_achievement_repository,
            notification_port,
            pulse_report_delivery_port,
            technology_stack_service,
        )

    @provide(scope=Scope.REQUEST)
    def technology_stack_service(
        self,
        db: AsyncSession,
        user_technology_stack_repository: UserTechnologyStackRepository,
    ) -> TechnologyStackService:
        return TechnologyStackService(db, user_technology_stack_repository)


class AgentProvider(Provider):
    """Agent dependencies exist only in the bot runtime; no network at construction."""

    @provide(scope=Scope.APP)
    async def model(self, settings: AppSettings) -> AsyncGenerator[AgentModelPort]:
        async with httpx2.AsyncClient(timeout=settings.agent_timeout_s) as client:
            yield PolzaAgentModel(settings, client)

    @provide(scope=Scope.APP)
    def technology_stack_parser(self, model: AgentModelPort) -> TechnologyStackParserPort:
        return PydanticAITechnologyStackParser(model)

    @provide(scope=Scope.APP)
    def checkin_response_parser(self, model: AgentModelPort) -> CheckinResponseParserPort:
        return PydanticAICheckinResponseParser(model)

    @provide(scope=Scope.REQUEST)
    def technology_stack_parsing_service(
        self,
        parser: TechnologyStackParserPort,
    ) -> TechnologyStackParsingService:
        return TechnologyStackParsingService(parser)

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
        technology_stack: TechnologyStackService,
        community_goals: CommunityGoalService,
        community_meetings: CommunityMeetingService,
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
            technology_stack,
            community_goals,
            community_meetings,
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
        technology_stack: TechnologyStackService,
        settings: AppSettings,
        community_goals: CommunityGoalService,
        community_meetings: CommunityMeetingService,
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
            technology_stack,
            community_goals,
            community_meetings,
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
                session=create_telegram_proxy_session(settings.telegram_proxy_url),
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
    async def pulse_report_delivery_port(self, settings: AppSettings, bot: Bot) -> PulseReportDeliveryPort:
        if settings.notification_backend == "telegram":
            return TelegramPulseReportDelivery(bot)
        if settings.notification_backend == "logging":
            return LoggingPulseReportDelivery()
        raise ValueError(f"Unsupported NOTIFICATION_BACKEND value: {settings.notification_backend}")

    @provide(scope=Scope.APP)
    async def meeting_delivery(self, settings: AppSettings, bot: Bot) -> MeetingDeliveryPort:
        if settings.notification_backend == "telegram":
            return TelegramMeetingDelivery(bot)
        if settings.notification_backend == "logging":
            return LoggingMeetingDelivery()
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
        AgentProvider(),
        DomainServiceProvider(),
        BotProvider(),
        PortsProvider(),
        TaskiqProvider(),
        ConfigProvider(),
    )
