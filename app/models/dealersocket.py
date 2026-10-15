from typing import Literal
from pydantic import BaseModel, ConfigDict


class RooftopAccess(BaseModel):
    id: str
    name: str
    verified: bool = False


class DealerSocketStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["disconnected", "connecting", "connected", "login_required", "error"]
    operation: Literal["login", "check"] | None = None
    lastCheckedAt: str | None = None
    lastConnectedAt: str | None = None
    errorCode: str | None = None
    message: str
    interactiveLoginEnabled: bool
    sessionExpiresAt: str | None = None
    rooftops: list[RooftopAccess]
