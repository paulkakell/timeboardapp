from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .models import RecurrenceType, Theme


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserBase(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=256)
    email: Optional[str] = Field(default=None, max_length=255)
    is_admin: bool = False


class UserOut(UserBase):
    id: int
    email: Optional[str] = None
    is_admin: bool
    theme: str
    purge_days: int

    class Config:
        from_attributes = True


class UserMeUpdate(BaseModel):
    theme: Optional[str] = Field(default=None)
    purge_days: Optional[int] = Field(default=None, ge=1, le=3650)
    email: Optional[str] = Field(default=None, max_length=255)
    current_password: Optional[str] = Field(default=None)
    new_password: Optional[str] = Field(default=None, min_length=8, max_length=256)


class UserAdminUpdate(BaseModel):
    email: Optional[str] = Field(default=None, max_length=255)
    is_admin: Optional[bool] = Field(default=None)


class TagOut(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True


class TaskBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    task_type: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = None
    url: Optional[str] = Field(default=None, max_length=2048)

    # Allow tasks with no due date. If omitted, the server uses the creation time.
    due_date: Optional[datetime] = Field(default=None)

    recurrence_type: str = Field(default=RecurrenceType.none.value)
    recurrence_interval: Optional[str] = Field(
        default=None,
        description=(
            "For post_completion: a human duration like '8h', '30m', '1d 2h'. "
            "For fixed_clock: either a duration OR a calendar rule like 'Every Tuesday', 'Mon Wed Fri', "
            "'10th of every month', 'First Monday', 'January 5'."
        ),
    )
    recurrence_times: Optional[str] = Field(
        default=None,
        description=(
            "For multi_slot_daily: comma-separated list of daily times like '08:00, 15:00, 23:00' "
            "(or '8:00 am, 3:00 pm'). "
            "For fixed_clock calendar rules, the server stores an RRULE-like string here internally."
        ),
    )

    tags: List[str] = Field(default_factory=list)


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    task_type: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = None
    url: Optional[str] = Field(default=None, max_length=2048)
    due_date: Optional[datetime] = None

    recurrence_type: Optional[str] = None
    recurrence_interval: Optional[str] = None
    recurrence_times: Optional[str] = None

    tags: Optional[List[str]] = None


class TaskOut(BaseModel):
    id: int
    user_id: int
    name: str
    task_type: str
    description: Optional[str]
    url: Optional[str]
    due_date_utc: datetime

    recurrence_type: str
    recurrence_interval_seconds: Optional[int]
    recurrence_times: Optional[str]

    status: str
    completed_at_utc: Optional[datetime]
    deleted_at_utc: Optional[datetime]

    tags: List[TagOut] = []

    class Config:
        from_attributes = True


class TaskCompleteResponse(BaseModel):
    completed_task: TaskOut
    spawned_task: Optional[TaskOut] = None


class TaskSummaryOut(BaseModel):
    archived: int
    past_due: int
    all_upcoming_due: int
    due_in_0_8h: int
    due_in_8_24h: int
    due_in_over_24h: int


# ---- Notifications (service-based) -------------------------------------------------


class NotificationServiceBase(BaseModel):
    service_type: str = Field(..., description="Service type, e.g. browser/email/gotify/ntfy/webhook/generic_api/wns")
    name: Optional[str] = Field(default=None, max_length=128)
    enabled: bool = Field(default=True)
    config: Dict[str, Any] = Field(default_factory=dict, description="Service-specific configuration payload")


class NotificationServiceCreate(NotificationServiceBase):
    pass


class NotificationServiceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    enabled: Optional[bool] = None
    config: Optional[Dict[str, Any]] = None


class NotificationServiceOut(BaseModel):
    id: int
    user_id: int
    service_type: str
    name: Optional[str]
    enabled: bool
    tag: str
    config: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class NotificationEventOut(BaseModel):
    id: int
    user_id: int
    task_id: Optional[int]
    service_id: Optional[int]
    service_type: Optional[str]
    event_type: str
    event_key: Optional[str]
    title: str
    message: Optional[str]
    delivery_status: Optional[str] = None
    delivery_error: Optional[str] = None
    delivery_attempts: Optional[int] = None
    last_attempt_at_utc: Optional[datetime] = None
    delivered_at_utc: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ---- Admin settings ----------------------------------------------------------------


class AdminEmailSettingsOut(BaseModel):
    enabled: bool
    provider: str = Field(default="smtp", description="Email delivery provider: smtp | sendgrid")
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_from: str
    use_tls: bool
    sendgrid_api_key_set: bool = Field(default=False, description="True if a SendGrid API key is stored in DB (not returned).")
    reminder_interval_minutes: int
    reset_token_minutes: int
    smtp_password_set: bool = Field(default=False, description="True if a password is stored in DB (not returned).")


class AdminEmailSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    provider: Optional[str] = Field(default=None, description="Email delivery provider: smtp | sendgrid")
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = Field(default=None, description="If omitted/blank, existing password can be kept.")
    smtp_from: Optional[str] = None
    use_tls: Optional[bool] = None
    sendgrid_api_key: Optional[str] = Field(default=None, description="If omitted/blank, existing API key can be kept.")
    reminder_interval_minutes: Optional[int] = None
    reset_token_minutes: Optional[int] = None
    keep_existing_password: bool = True
    keep_existing_sendgrid_api_key: bool = True


class AdminLoggingSettingsOut(BaseModel):
    level: str
    retention_days: int


class AdminLoggingSettingsUpdate(BaseModel):
    level: Optional[str] = None
    retention_days: Optional[int] = None


class AdminWNSSettingsOut(BaseModel):
    enabled: bool
    package_sid: str
    client_secret_set: bool = Field(default=False, description="True if a client secret is stored in DB (not returned).")


class AdminWNSSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    package_sid: Optional[str] = None
    client_secret: Optional[str] = Field(default=None, description="If omitted/blank, existing secret can be kept.")
    keep_existing_secret: bool = True


class LogFileOut(BaseModel):
    filename: str
    size_bytes: int
    modified_at_iso: str
