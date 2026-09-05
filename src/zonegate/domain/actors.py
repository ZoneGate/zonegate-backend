from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class Actor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., description="Unique identifier of the human actor")
    role: str = Field(..., description="Organizational role of the actor")
    permissions: list[str] = Field(default_factory=list, description="Granted authorization scopes")
    registered_phone_number: str = Field(..., description="E.164 registered phone number for network identity")
    registered_device_id: str = Field(..., description="Hardware identifier / IMEI / device fingerprint")
    enrollment_status: str = Field(default="ACTIVE", description="Identity enrollment lifecycle status")


class DeviceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str
    phone_number: str
    device_id: str
    bound_at: datetime
    is_active: bool = True
