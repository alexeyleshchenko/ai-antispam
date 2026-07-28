from datetime import datetime
from enum import StrEnum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class ModerationMode(StrEnum):
    NOTIFY = "notify"
    DELETE = "delete"
    DELETE_SILENT = "delete_silent"


class Administrator(BaseModel):
    """Enhanced administrator model with validation"""

    admin_id: int
    username: Optional[str] = None
    credits: int = Field(default=0, ge=0)
    is_active: bool = True
    moderation_mode: ModerationMode = ModerationMode.NOTIFY
    language_code: Optional[str] = None  # ru or en
    created_at: datetime = Field(default_factory=datetime.now)
    last_updated: datetime = Field(default_factory=datetime.now)

    @property
    def auto_deletes_spam(self) -> bool:
        return self.moderation_mode in (
            ModerationMode.DELETE,
            ModerationMode.DELETE_SILENT,
        )

    @property
    def skips_auto_delete_notification(self) -> bool:
        return self.moderation_mode == ModerationMode.DELETE_SILENT

    @field_validator("credits")
    @classmethod
    def validate_credits(cls, v):
        if v < 0:
            raise ValueError("Credits cannot be negative")
        return v


class Group(BaseModel):
    """Enhanced Group model with validation"""

    group_id: int
    admin_ids: List[int]
    moderation_enabled: bool = True
    member_ids: List[int] = []
    title: Optional[str] = None
    username: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    last_updated: datetime = Field(default_factory=datetime.now)
