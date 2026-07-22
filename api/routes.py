"""FastAPI route handlers for AetherGuard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import APIRouter, HTTPException, Query

from aether_core import ConjunctionEvent
from engine import testbed
from engine.catalog import (
    clear_runtime_objects,
    get_entry,
    load_catalog,
    register_runtime_objects,
)
from engine.collision import CovarianceError, assess_collision, classify_risk
from engine.geo import gmst_radians, teme_to_ecef, teme_to_latlon_alt
from engine.propagator import PropagationError, propagate_tle, relative_state
from engine.sampling import sample_by_turn_angle
from engine.trajectory import ManeuverConstraintError, TrajectoryOptimizer
from schemas.conjunction import (
    ConjunctionAssessRequest,
    ConjunctionAssessResponse,
    GeoMarker,
    ManeuverPlanRequest,
    ManeuverPlanResponse,
    MeshNodeSyncRequest,
    MeshNodeSyncResponse,
    OrbitTrackRequest,
    OrbitTrackResponse,
    RiskLevel,
    SkyTrafficObject,
    SkyTrafficResponse,
    TestbedDeployRequest,
    TestbedDeployResponse,
    TestbedPairInfo,
)

router = APIRouter()

ACTION_PC_THRESHOLD = 1e-4

_MESH_NEIGHBORS: dict[str, list[str]] = {
    "NODE-ALPHA": ["NODE-BRAVO", "NODE-GATEWAY"],
    "NODE-BRAVO": ["NODE-ALPHA", "NODE-CHARLIE"],
    "NODE-CHARLIE": ["NODE-BRAVO", "NODE-GATEWAY"],
    "NODE-GATEWAY": ["NODE-ALPHA", "NODE-CHARLIE"],
}


def _marker_from_position(
    name: str,
    position_km: np.ndarray,
    epoch: datetime,
    *,
    gmst: float | None = None,
) -> GeoMarker:
    """Build a globe marker from a TEME position.

    Pass ``gmst`` to emit frozen-ECEF coordinates, which is what the globe's
    orbit paths expect — without it an inertial orbit collapses into a
    ground-track smear.
    """
    lat, lon, alt = teme_to_latlon_alt(position_km, epoch)
    cartesian = (
        teme_to_ecef(position_km, epoch, gmst=gmst) if gmst is not None else position_km
    )
    return GeoMarker(
        name=name,
        lat_deg=lat,
        lon_deg=lon,
        alt_km=alt,
        position_km=[float(x) for x in cartesian],
    )


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


def _orbital_period_minutes(line2: str) -> float:
    """Read mean motion (rev/day) from TLE line 2 → period in minutes."""
    try:
        mean_motion = float(line2[52:63].strip())
    except (TypeError, ValueError):
        return 92.0
    if mean_motion <= 1e-6:
        return 92.0
    return 1440.0 / mean_motion


@router.get("/api/v1/sky-traffic/{sat_id}/track", response_model=OrbitTrackResponse)
def sky_traffic_track(
    sat_id: str,
    epoch: datetime | None = Query(default=None),
    duration_minutes: float | None = Query(
        default=None,
        gt=0,
        le=72 * 60,
        description="Override track length. Default: one full orbit from TLE mean motion.",
    ),
    step_seconds: float | None = Query(
        default=None,
        gt=0,
        le=3600,
        description="Override sample step. Default: adaptive (~180 samples / orbit).",
    ),
) -> OrbitTrackResponse:
    """Orbit polyline for a single catalog object (on expand / select).

    Defaults to one full revolution so GEO/HEO orbits render completely instead
    of a LEO-length 92-minute stub.
    """
    entry = get_entry(sat_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown satellite id '{sat_id}'.")

    when = epoch or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    period_min = _orbital_period_minutes(entry["line2"])
    track_minutes = float(duration_minutes) if duration_minutes is not None else min(period_min * 1.05, 72 * 60)
    target_samples = 180
    sample_dt = (
        float(step_seconds)
        if step_seconds is not None
        else max(20.0, (track_minutes * 60.0) / target_samples)
    )

    # Freeze Earth rotation at the start epoch so GEO/HEO inertial orbits
    # render as complete rings instead of collapsing to a ground-track stub.
    gmst0 = gmst_radians(when)

    points: list[GeoMarker] = []
    try:
        def state_at(offset_s: float):
            return propagate_tle(
                entry["line1"],
                entry["line2"],
                when + timedelta(seconds=float(offset_s)),
                name=entry["name"],
                validate_with_skyfield=False,
            )

        span_s = track_minutes * 60.0
        if step_seconds is not None:
            # Explicit step requested: honour it rather than adapting.
            offsets = [
                i * sample_dt
                for i in range(min(int(span_s / sample_dt) + 1, 400))
            ]
        else:
            # Even time steps starve perigee on eccentric orbits, drawing a
            # chord straight across the fastest part of the pass. Put samples
            # where the path actually bends instead.
            offsets, _ = sample_by_turn_angle(
                lambda t: state_at(t).position_km, 0.0, span_s, initial=48
            )

        for offset in offsets:
            state = state_at(offset)
            lat, lon, alt = teme_to_latlon_alt(state.position_km, state.epoch)
            ecef = teme_to_ecef(state.position_km, state.epoch, gmst=gmst0)
            points.append(
                GeoMarker(
                    name=entry["name"],
                    lat_deg=lat,
                    lon_deg=lon,
                    alt_km=alt,
                    position_km=[float(x) for x in ecef],
                )
            )
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


@router.post("/api/v1/testbed/deploy", response_model=TestbedDeployResponse)
def testbed_deploy(payload: TestbedDeployRequest) -> TestbedDeployResponse:
    """Deploy AETHERGUARD satellites on conjunction courses with real objects.

    Nothing in the live catalog actually conjuncts, so the maneuver planner has
    nothing to act on. These are derived from real TLEs — same altitude, tilted
    plane, phased to meet — and exist only in memory.
    """
    when = payload.target_time or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    target_ids = payload.target_ids or list(testbed.DEFAULT_TARGET_IDS)
    targets: list[dict] = []
    for target_id in target_ids:
        entry = get_entry(target_id)
        if entry is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown target satellite id '{target_id}'."
            )
        targets.append(entry)

    try:
        clear_runtime_objects()
        pairs = testbed.deploy(
            targets, when, plane_offset_deg=payload.plane_offset_deg
        )
    except PropagationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Testbed deployment failed: {exc}"
        ) from exc

    register_runtime_objects([pair.satellite for pair in pairs])

    return TestbedDeployResponse(
        epoch=when,
        deployed=[
            TestbedPairInfo(
                id=str(pair.satellite["id"]),
                name=str(pair.satellite["name"]),
                target_id=pair.target_id,
                target_name=pair.target_name,
                tca=pair.tca,
                miss_distance_km=pair.miss_distance_km,
                relative_speed_km_s=pair.relative_speed_km_s,
            )
            for pair in pairs
        ],
    )


@router.delete("/api/v1/testbed/deploy")
def testbed_clear() -> dict[str, str]:
    """Remove every deployed AETHERGUARD satellite."""
    clear_runtime_objects()
    return {"status": "cleared"}


@router.post("/api/v1/plan-maneuver", response_model=ManeuverPlanResponse)
def plan_maneuver(payload: ManeuverPlanRequest) -> ManeuverPlanResponse:
    """Plan an avoidance burn for a selected pair and return both trajectories.

    The primary maneuvers; the secondary is treated as uncooperative. Both
    returned tracks start at the burn point so the divergence on the globe is
    the burn and nothing else.
    """
    if payload.primary_id == payload.secondary_id:
        raise HTTPException(
            status_code=422, detail="Primary and secondary must be different objects."
        )

    primary_entry = get_entry(payload.primary_id)
    secondary_entry = get_entry(payload.secondary_id)
    for label, entry, sat_id in (
        ("primary", primary_entry, payload.primary_id),
        ("secondary", secondary_entry, payload.secondary_id),
    ):
        if entry is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown {label} satellite id '{sat_id}'."
            )

    tca = payload.target_time or datetime.now(timezone.utc)
    if tca.tzinfo is None:
        tca = tca.replace(tzinfo=timezone.utc)

    p1_diag = payload.P1_diag or [0.045, 0.045, 0.045]
    p2_diag = payload.P2_diag or [0.045, 0.045, 0.045]

    try:
        primary = propagate_tle(
            primary_entry["line1"], primary_entry["line2"], tca,
            name=primary_entry["name"],
        )
        secondary = propagate_tle(
            secondary_entry["line1"], secondary_entry["line2"], tca,
            name=secondary_entry["name"],
        )
        r_rel, v_rel = relative_state(primary, secondary)

        before = assess_collision(
            r_rel_km=r_rel,
            v_rel_km_s=v_rel,
            p1_diag_km2=p1_diag,
            p2_diag_km2=p2_diag,
            hbr_meters=payload.hbr_meters,
        )

        # The optimizer works from a single combined covariance.
        optimizer = TrajectoryOptimizer(
            position_covariance_km2=np.diag(np.asarray(p1_diag) + np.asarray(p2_diag)),
            hard_body_radius_km=payload.hbr_meters / 1000.0,
            max_delta_v_mps=payload.max_delta_v_mps,
            max_burn_lead_hours=payload.max_burn_lead_hours,
        )
        event = ConjunctionEvent(
            event_id=f"{payload.primary_id}-{payload.secondary_id}",
            satellite_id=payload.primary_id,
            sat_state_vector=np.concatenate(
                [primary.position_km, primary.velocity_km_s]
            ),
            object_state_vector=np.concatenate(
                [secondary.position_km, secondary.velocity_km_s]
            ),
            tca=tca.replace(tzinfo=None),
            probability_of_collision=before.poc,
            mode="INDEPENDENT",
            mesh_neighbors=None,
        )

        plan = optimizer.calculate_independent_avoidance(event)
        baseline, maneuvered = optimizer.burn_comparison_tracks(
            event, plan.delta_v
        )
        post_burn_position = optimizer.position_at_tca(event, plan.delta_v)

        # Freeze Earth rotation at the burn epoch, matching sky-traffic tracks,
        # so these render in the same frame as every other orbit on the globe.
        gmst0 = gmst_radians(plan.burn_time.replace(tzinfo=timezone.utc))
    except ManeuverConstraintError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PropagationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CovarianceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Maneuver planning failed: {exc}"
        ) from exc

    burn_time = plan.burn_time.replace(tzinfo=timezone.utc)
    return ManeuverPlanResponse(
        primary_name=primary_entry["name"],
        secondary_name=secondary_entry["name"],
        delta_v_m_s=[float(x) for x in plan.delta_v],
        delta_v_magnitude_m_s=float(np.linalg.norm(plan.delta_v)),
        burn_time=burn_time,
        tca=tca,
        burn_lead_hours=(tca - burn_time).total_seconds() / 3600.0,
        poc_before=float(before.poc),
        poc_after=float(plan.new_probability),
        miss_distance_before_km=float(before.dca_km),
        miss_distance_after_km=float(
            np.linalg.norm(post_burn_position - secondary.position_km)
        ),
        risk_before=RiskLevel(classify_risk(before.poc)),
        burn_direction=(
            "none"
            if float(np.linalg.norm(plan.delta_v)) == 0.0
            else "prograde"
            if float(np.dot(plan.delta_v, primary.velocity_km_s)) > 0.0
            else "retrograde"
        ),
        requires_mesh_rerouting=plan.requires_mesh_rerouting,
        baseline_track=[
            _marker_from_position(primary_entry["name"], p, t, gmst=gmst0)
            for t, p in baseline
        ],
        maneuvered_track=[
            _marker_from_position(primary_entry["name"], p, t, gmst=gmst0)
            for t, p in maneuvered
        ],
    )


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
