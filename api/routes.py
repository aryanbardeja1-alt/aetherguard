"""FastAPI route handlers for AetherGuard."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException

from engine.collision import CovarianceError, assess_collision, classify_risk
from engine.geo import teme_to_latlon_alt
from engine.propagator import PropagationError, propagate_tle, relative_state
from schemas.conjunction import (
    ConjunctionAssessRequest,
    ConjunctionAssessResponse,
    GeoMarker,
    MeshNodeSyncRequest,
    MeshNodeSyncResponse,
    OrbitTrackRequest,
    OrbitTrackResponse,
    RiskLevel,
)

router = APIRouter()

ACTION_PC_THRESHOLD = 1e-4

_MESH_NEIGHBORS: dict[str, list[str]] = {
    "NODE-ALPHA": ["NODE-BRAVO", "NODE-GATEWAY"],
    "NODE-BRAVO": ["NODE-ALPHA", "NODE-CHARLIE"],
    "NODE-CHARLIE": ["NODE-BRAVO", "NODE-GATEWAY"],
    "NODE-GATEWAY": ["NODE-ALPHA", "NODE-CHARLIE"],
}


def _marker_from_state(name: str, state) -> GeoMarker:
    lat, lon, alt = teme_to_latlon_alt(state.position_km, state.epoch)
    return GeoMarker(
        name=name,
        lat_deg=lat,
        lon_deg=lon,
        alt_km=alt,
        position_km=[float(x) for x in state.position_km],
    )


@router.get("/health")
def health() -> dict[str, str]:
    """Basic operational status check."""
    return {"status": "ok", "service": "AetherGuard"}


@router.post("/api/v1/assess-conjunction", response_model=ConjunctionAssessResponse)
def assess_conjunction(payload: ConjunctionAssessRequest) -> ConjunctionAssessResponse:
    """Propagate TLEs, rotate/project covariances, and compute Pc (Chan by default)."""
    try:
        primary = propagate_tle(
            payload.primary_tle.line1,
            payload.primary_tle.line2,
            payload.target_time,
            name=payload.primary_tle.name,
        )
        secondary = propagate_tle(
            payload.secondary_tle.line1,
            payload.secondary_tle.line2,
            payload.target_time,
            name=payload.secondary_tle.name,
        )
        r_rel, v_rel = relative_state(primary, secondary)
        result = assess_collision(
            r_rel_km=r_rel,
            v_rel_km_s=v_rel,
            p1_km2=payload.P1,
            p2_km2=payload.P2,
            p1_diag_km2=payload.P1_diag,
            p2_diag_km2=payload.P2_diag,
            hbr_meters=payload.hbr_meters,
            covariance_frame=payload.covariance_frame,
            primary_position_km=primary.position_km,
            primary_velocity_km_s=primary.velocity_km_s,
            secondary_position_km=secondary.position_km,
            secondary_velocity_km_s=secondary.velocity_km_s,
            method=payload.poc_method,
        )
    except PropagationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CovarianceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Conjunction assessment failed: {exc}",
        ) from exc

    risk = classify_risk(result.poc)
    return ConjunctionAssessResponse(
        dca_km=result.dca_km,
        poc=result.poc,
        risk_level=RiskLevel(risk),
        action_required=result.poc > ACTION_PC_THRESHOLD,
        poc_method=result.method,  # type: ignore[arg-type]
        primary=_marker_from_state(payload.primary_tle.name, primary),
        secondary=_marker_from_state(payload.secondary_tle.name, secondary),
    )


@router.post("/api/v1/orbit-track", response_model=OrbitTrackResponse)
def orbit_track(payload: OrbitTrackRequest) -> OrbitTrackResponse:
    """Sample geodetic points along a TLE orbit for globe polylines."""
    points: list[GeoMarker] = []
    try:
        steps = int(payload.duration_minutes * 60.0 / payload.step_seconds) + 1
        steps = min(steps, 500)
        for i in range(steps):
            epoch = payload.start_time + timedelta(seconds=i * payload.step_seconds)
            state = propagate_tle(
                payload.tle.line1,
                payload.tle.line2,
                epoch,
                name=payload.tle.name,
            )
            points.append(_marker_from_state(payload.tle.name, state))
    except PropagationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return OrbitTrackResponse(name=payload.tle.name, points=points)


@router.post("/api/v1/mesh/node-sync", response_model=MeshNodeSyncResponse)
def mesh_node_sync(payload: MeshNodeSyncRequest) -> MeshNodeSyncResponse:
    """Ingest node telemetry and return mesh routing guidance."""
    neighbors = _MESH_NEIGHBORS.get(payload.node_id, ["NODE-GATEWAY"])
    speed = sum(v * v for v in payload.velocity) ** 0.5

    next_hop = neighbors[0] if neighbors else "NODE-GATEWAY"
    reroute = bool(payload.burn_scheduled or speed > 10.0)

    if not reroute:
        next_hop = None

    return MeshNodeSyncResponse(
        mesh_reroute_active=reroute,
        recommended_next_hop=next_hop,
        status="degraded" if reroute else "ok",
    )
