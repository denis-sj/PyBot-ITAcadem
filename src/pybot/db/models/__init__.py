from ..base_class import Base

# Shared (общие) модели
from .achievement import Achievement
from .attendance import (
    AttendanceChangeHistory,
    AttendanceDate,
    AttendanceImport,
    AttendanceImportStatus,
    AttendanceRecord,
)
from .community_meetings import CommunityMeeting, CommunityMeetingInvitee, CommunityMeetingResponse
from .role_module import (
    Role,
    RoleEvent,
    RoleRequest,
    UserRole,
)
from .task_module import (
    Task,
    TaskSolution,
    TaskSolutionStatus,
)

# Доменные подпакеты
from .user_module import (
    Competence,
    Level,
    PointsTransaction,
    User,
    UserGoal,
    UserNotificationPreference,
    UserAchievement,
    UserAchievementAward,
    UserActivityStatus,
    UserCompetence,
    UserLevel,
    UserTechnologyStack,
    WeeklyCheckin,
    Valuation,
)

__all__ = [
    "Base",
    # shared
    "Achievement",
    "CommunityMeeting",
    "CommunityMeetingInvitee",
    "CommunityMeetingResponse",
    # user_module
    "User",
    "UserGoal",
    "UserNotificationPreference",
    "WeeklyCheckin",
    "UserActivityStatus",
    "Level",
    "UserLevel",
    "Competence",
    "UserCompetence",
    "UserAchievement",
    "UserAchievementAward",
    "UserTechnologyStack",
    "PointsTransaction",
    "Valuation",
    # role_module
    "Role",
    "UserRole",
    "RoleEvent",
    # task_module
    "Task",
    "TaskSolution",
    "TaskSolutionStatus",
    # attendance
    "AttendanceChangeHistory",
    "AttendanceDate",
    "AttendanceImport",
    "AttendanceImportStatus",
    "AttendanceRecord",
]
