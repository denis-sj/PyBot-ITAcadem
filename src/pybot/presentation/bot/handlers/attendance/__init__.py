"""Combined attendance presentation router."""

from aiogram import Router

from .admin_commands import attendance_admin_router
from .edit_visits import attendance_edit_visits_router
from .photo_import import attendance_photo_router
from .user_commands import attendance_user_router

attendance_router = Router(name="attendance")
attendance_router.include_routers(
    attendance_photo_router,
    attendance_user_router,
    attendance_admin_router,
    attendance_edit_visits_router,
)

__all__ = ["attendance_router"]
