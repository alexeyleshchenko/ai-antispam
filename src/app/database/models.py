from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class ModerationMode(StrEnum):
    NOTIFY = "notify"
    DELETE = "delete"
    DELETE_SILENT = "delete_silent"


class GroupStatus(StrEnum):
    """Lifecycle status (deletion-policy E+C) — replaces hard DELETE for groups.

    active: bot in the chat, moderation running
    paused: bot left due to payment/no-rights, row retained for audit + rollback
    left:   bot removed / chat inaccessible, row retained for audit + rollback
    """

    ACTIVE = "active"
    PAUSED = "paused"
    LEFT = "left"


class Administrator(BaseModel):
    """Enhanced administrator model with validation"""

    admin_id: int
    username: str | None = None
    credits: int = Field(default=0, ge=0)
    is_active: bool = True
    moderation_mode: ModerationMode = ModerationMode.NOTIFY
    language_code: str | None = None  # ru or en
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
    admin_ids: list[int]
    moderation_enabled: bool = True
    status: GroupStatus = GroupStatus.ACTIVE
    member_ids: list[int] = []
    title: str | None = None
    username: str | None = None
    topic_description: str | None = None
    topic_description_short: str | None = None
    topic_updated_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    last_updated: datetime = Field(default_factory=datetime.now)
