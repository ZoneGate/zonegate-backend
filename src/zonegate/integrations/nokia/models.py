"""CAMARA / Nokia Network As Code API request and response data models.

References:
- CAMARA Number Verification API (v0.2.0 / v1.0.0)
- CAMARA Location Verification API (v0.3.0 / v1.0.0)
- CAMARA SIM Swap API (v0.4.0 / v1.0.0)
- CAMARA Device Swap API (v0.1.0)
- CAMARA Device Reachability Status API (v0.2.0)
"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


# --- Number Verification ---

class CamaraNumberVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phoneNumber: str = Field(..., description="E.164 formatted subscriber phone number")


class CamaraNumberVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    devicePhoneNumberVerified: bool = Field(..., description="True if number matches caller identity")


# --- Location Verification ---

class CamaraCoordinates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    latitude: float
    longitude: float


class CamaraCircleArea(BaseModel):
    model_config = ConfigDict(extra="forbid")
    areaType: Literal["Circle"] = "Circle"
    center: CamaraCoordinates
    radius: int = Field(..., description="Radius in meters")


class CamaraDeviceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phoneNumber: str


class CamaraLocationVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: CamaraDeviceRef
    area: CamaraCircleArea


class CamaraLocationVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verificationResult: Literal["TRUE", "FALSE", "PARTIAL", "UNKNOWN"]
    matchRate: int | None = None


# --- SIM Swap ---

class CamaraSimSwapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phoneNumber: str
    maxAge: int = Field(default=240, description="Max check age in hours (e.g. 240 = 10 days)")


class CamaraSimSwapResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    swapped: bool
    latestSimChange: str | None = None


# --- Device Swap ---

class CamaraDeviceSwapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phoneNumber: str
    maxAge: int = Field(default=240, description="Max check age in hours")


class CamaraDeviceSwapResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    swapped: bool
    latestDeviceChange: str | None = None


# --- Reachability ---

class CamaraReachabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: CamaraDeviceRef


class CamaraReachabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reachabilityStatus: Literal["CONNECTED_DATA", "CONNECTED_SMS", "NOT_CONNECTED", "UNKNOWN"]
