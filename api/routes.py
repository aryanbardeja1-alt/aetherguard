"""FastAPI route handlers for AetherGuard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import APIRouter, HTTPException, Query

from engine.catalog import get_entry, load_catalog
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
    SkyTrafficObject,
    SkyTrafficResponse,
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


@router.get("/api/v1/sky-traffic", response_model=SkyTrafficResponse)
def sky_traffic(
    epoch: datetime | None = Query(
        default=None,
        description="Propagation epoch (UTC). Defaults to now.",
    ),
) -> SkyTrafficResponse:
    """Return the full catalog with every object propagated in one shot."""
    when = epoch or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    catalog = load_catalog()
    satellites: list[SkyTrafficObject] = []
    skipped = 0
    for entry in catalog:
        try:
            state = propagate_tle(
                entry["line1"],
                entry["line2"],
                when,
                name=entry["name"],
                validate_with_skyfield=False,
            )
        except PropagationError:
            skipped += 1
            continue
        lat, lon, alt = teme_to_latlon_alt(state.position_km, state.epoch)
        if not np.isfinite(lat) or not np.isfinite(lon) or not np.isfinite(alt):
            skipped += 1
            continue
        speed = float(np.linalg.norm(state.velocity_km_s))
        satellites.append(
            SkyTrafficObject(
                id=str(entry["id"]),
                name=entry["name"],
                norad_id=int(entry["norad_id"]),
                object_type=str(entry.get("object_type", "active")),
                lat_deg=lat,
                lon_deg=lon,
                alt_km=alt,
                speed_km_s=speed,
                position_km=[float(x) for x in state.position_km],
                velocity_km_s=[float(x) for x in state.velocity_km_s],
                line1=entry["line1"],
                line2=entry["line2"],
            )
        )

    return SkyTrafficResponse(
        epoch=when,
        count=len(satellites),
        catalog_size=len(catalog),
        skipped=skipped,
        satellites=satellites,
    )


@router.get("/api/v1/sky-traffic/{sat_id}/track", response_model=OrbitTrackResponse)
def sky_traffic_track(
    sat_id: str,
    epoch: datetime | None = Query(default=None),
    duration_minutes: float = Query(default=92.0, gt=0, le=24 * 60),
    step_seconds: float = Query(default=90.0, gt=0, le=600),
) -> OrbitTrackResponse:
    """Orbit polyline for a single catalog object (on expand / select)."""
    entry = get_entry(sat_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown satellite id '{sat_id}'.")

    when = epoch or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    points: list[GeoMarker] = []
    try:
        steps = min(int(duration_minutes * 60.0 / step_seconds) + 1, 500)
        for i in range(steps):
            t = when + timedelta(seconds=i * step_seconds)
            state = propagate_tle(
                entry["line1"],
                entry["line2"],
                t,
                name=entry["name"],
                validate_with_skyfield=False,
            )
            points.append(_marker_from_state(entry["name"], state))
    except PropagationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if len(points) < 2:
        raise HTTPException(status_code=422, detail="Orbit track produced too few points.")

    return OrbitTrackResponse(name=entry["name"], points=points)


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
