"""Validated periods accepted by attendance read tools."""

from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from ..core.constants import YEKATERINBURG_TZ
from .base_dto import BaseDTO


class AgentMonth(BaseDTO):
    """An explicit calendar month, never a future attendance period."""

    year: int = Field(ge=1, le=9999)
    month: int = Field(ge=1, le=12)

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        current = datetime.now(YEKATERINBURG_TZ)
        if (self.year, self.month) > (current.year, current.month):
            raise ValueError("Choose a current or past month")
        return self

    @classmethod
    def current(cls) -> Self:
        """Use the same business timezone as attendance commands."""
        current = datetime.now(YEKATERINBURG_TZ)
        return cls(year=current.year, month=current.month)
