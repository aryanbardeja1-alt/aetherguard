"""Request and response models for conjunction assessment and mesh sync."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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

Matrix3 = Annotated[
    list[list[float]],
    Field(
        min_length=3,
        max_length=3,
        description="Full 3×3 position covariance (km²), row-major",
    ),
]


class ConjunctionAssessRequest(BaseModel):
    """Payload for ``POST /api/v1/assess-conjunction``.

    Supply either full matrices (``P1`` / ``P2``) or diagonals (``P1_diag`` /
    ``P2_diag``). CDM-style covariances should use ``covariance_frame="RTN"``.
    """

    primary_tle: TLESet
    secondary_tle: TLESet
    target_time: datetime
    hbr_meters: Annotated[float, Field(gt=0, description="Hard-body radius (m)")]
    P1: Matrix3 | None = None
    P2: Matrix3 | None = None
    P1_diag: Diag3 | None = None
    P2_diag: Diag3 | None = None
    covariance_frame: Literal["TEME", "RTN"] = "TEME"
    poc_method: Literal["chan", "dblquad"] = "chan"

    @field_validator("P1", "P2")
    @classmethod
    def validate_matrix(cls, value: list[list[float]] | None) -> list[list[float]] | None:
        if value is None:
            return value
        for row in value:
            if len(row) != 3:
                raise ValueError("Each covariance row must have exactly 3 elements.")
        return value

    @field_validator("P1_diag", "P2_diag")
    @classmethod
    def positive_variances(cls, value: list[float] | None) -> list[float] | None:
        if value is not None and any(v <= 0 for v in value):
            raise ValueError("Covariance diagonal elements must be strictly positive.")
        return value

    @model_validator(mode="after")
    def require_covariance_pair(self) -> Self:
        has_full = self.P1 is not None and self.P2 is not None
        has_diag = self.P1_diag is not None and self.P2_diag is not None
        if not has_full and not has_diag:
            raise ValueError(
                "Provide either P1 & P2 (full 3×3) or P1_diag & P2_diag."
            )
        return self


class GeoMarker(BaseModel):
    """Object location for globe rendering."""

    name: str
    lat_deg: float
    lon_deg: float
    alt_km: float
    position_km: list[float]


class ConjunctionAssessResponse(BaseModel):
    """Conjunction assessment result."""

    dca_km: float = Field(description="Distance of closest approach (km)")
    poc: float = Field(description="Probability of collision")
    risk_level: RiskLevel
    action_required: bool = Field(
        description="True when Pc exceeds the 1e-4 operational threshold"
    )
    poc_method: Literal["chan", "dblquad"] = "chan"
    primary: GeoMarker | None = None
    secondary: GeoMarker | None = None


class OrbitTrackRequest(BaseModel):
    """Sample an orbit track for globe polylines."""

    tle: TLESet
    start_time: datetime
    duration_minutes: Annotated[float, Field(gt=0, le=24 * 60)] = 90.0
    step_seconds: Annotated[float, Field(gt=0, le=600)] = 60.0


class OrbitTrackResponse(BaseModel):
    name: str
    points: list[GeoMarker]


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
