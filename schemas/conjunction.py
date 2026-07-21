"""Request and response models for conjunction assessment and mesh sync."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RiskLevel(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class TLESet(BaseModel):
    """NORAD two-line element set for a space object."""

    model_config = ConfigDict(str_strip_whitespace=True)

    line1: Annotated[str, Field(min_length=68, description="TLE line 1")]
    line2: Annotated[str, Field(min_length=68, description="TLE line 2")]
    name: str = "UNKNOWN"

    @field_validator("line1")
    @classmethod
    def validate_line1(cls, value: str) -> str:
        if not value.startswith("1 "):
            raise ValueError("TLE line1 must start with '1 '")
        return value

    @field_validator("line2")
    @classmethod
    def validate_line2(cls, value: str) -> str:
        if not value.startswith("2 "):
            raise ValueError("TLE line2 must start with '2 '")
        return value


Diag3 = Annotated[
    list[float],
    Field(min_length=3, max_length=3, description="Diagonal of 3×3 covariance (km²)"),
]


class ConjunctionAssessRequest(BaseModel):
    """Payload for ``POST /api/v1/assess-conjunction``."""

    primary_tle: TLESet
    secondary_tle: TLESet
    target_time: datetime
    P1_diag: Diag3
    P2_diag: Diag3
    hbr_meters: Annotated[float, Field(gt=0, description="Hard-body radius (m)")]

    @field_validator("P1_diag", "P2_diag")
    @classmethod
    def positive_variances(cls, value: list[float]) -> list[float]:
        if any(v <= 0 for v in value):
            raise ValueError("Covariance diagonal elements must be strictly positive.")
        return value


class ConjunctionAssessResponse(BaseModel):
    """Conjunction assessment result."""

    dca_km: float = Field(description="Distance of closest approach (km)")
    poc: float = Field(description="Probability of collision")
    risk_level: RiskLevel
    action_required: bool = Field(
        description="True when Pc exceeds the 1e-4 operational threshold"
    )


class MeshNodeSyncRequest(BaseModel):
    """Node telemetry ingested by the orbital mesh router."""

    node_id: Annotated[str, Field(min_length=1)]
    position: Annotated[list[float], Field(min_length=3, max_length=3)]
    velocity: Annotated[list[float], Field(min_length=3, max_length=3)]
    burn_scheduled: bool = False


class MeshNodeSyncResponse(BaseModel):
    """Mesh routing recommendation after node telemetry sync."""

    mesh_reroute_active: bool
    recommended_next_hop: str | None
    status: Literal["ok", "degraded"] = "ok"
