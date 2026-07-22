"""Pytest suite for AetherGuard PoC engine and REST API."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from engine.collision import (
    CovarianceError,
    assess_collision,
    build_encounter_rotation,
    chan_poc,
    classify_risk,
    integrate_poc_disk,
)
from engine.frames import rtn_basis, rotate_covariance_rtn_to_teme, rotate_covariance_teme_to_rtn
from engine.propagator import PropagationError, propagate_tle, relative_state
from main import app

client = TestClient(app)

ISS_TLE = (
    "1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927",
    "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537",
)

HST_TLE = (
    "1 20580U 90037B   08264.51782528 -.00000640  00000-0  00000+0 0  9994",
    "2 20580  28.4690 247.4627 0006703 130.5360 325.0288 15.08699091400000",
)

TARGET_TIME = datetime(2008, 9, 20, 12, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Unit: encounter-plane / PoC math
# ---------------------------------------------------------------------------


def test_encounter_rotation_is_orthonormal_and_orthogonal_to_vrel() -> None:
    v_rel = np.array([1.0, 2.0, -0.5])
    r = build_encounter_rotation(v_rel)
    assert r.shape == (2, 3)
    assert abs(np.linalg.norm(r[0]) - 1.0) < 1e-12
    assert abs(np.linalg.norm(r[1]) - 1.0) < 1e-12
    assert abs(float(np.dot(r[0], r[1]))) < 1e-12
    v_hat = v_rel / np.linalg.norm(v_rel)
    assert abs(float(np.dot(r[0], v_hat))) < 1e-12
    assert abs(float(np.dot(r[1], v_hat))) < 1e-12


def test_zero_relative_velocity_uses_fallback_axis() -> None:
    r = build_encounter_rotation(np.zeros(3), fallback_axis=np.array([1.0, 0.0, 0.0]))
    assert r.shape == (2, 3)
    assert abs(float(np.dot(r[0], r[1]))) < 1e-12


def test_rtn_basis_orthonormal_right_handed() -> None:
    r = np.array([6778.0, 0.0, 0.0])
    v = np.array([0.0, 7.67, 0.0])
    q = rtn_basis(r, v)
    assert q.shape == (3, 3)
    assert np.allclose(q.T @ q, np.eye(3), atol=1e-12)
    assert np.isclose(np.linalg.det(q), 1.0, atol=1e-12)
    # Radial column aligns with position.
    assert np.allclose(q[:, 0], r / np.linalg.norm(r), atol=1e-12)


def test_rtn_teme_covariance_roundtrip() -> None:
    r = np.array([6778.0, 100.0, -50.0])
    v = np.array([-0.1, 7.5, 0.2])
    p_rtn = np.array(
        [
            [1.0e-2, 2.0e-3, 0.0],
            [2.0e-3, 4.0e-2, 1.0e-3],
            [0.0, 1.0e-3, 5.0e-3],
        ]
    )
    p_teme = rotate_covariance_rtn_to_teme(p_rtn, r, v)
    p_back = rotate_covariance_teme_to_rtn(p_teme, r, v)
    assert np.allclose(p_back, p_rtn, atol=1e-12)


def test_poc_high_when_objects_coincide() -> None:
    r_rel = np.zeros(3)
    v_rel = np.array([0.0, 0.0, 10.0])
    result = assess_collision(
        r_rel_km=r_rel,
        v_rel_km_s=v_rel,
        p1_diag_km2=(1e-6, 1e-6, 1e-6),
        p2_diag_km2=(1e-6, 1e-6, 1e-6),
        hbr_meters=10.0,
        method="chan",
    )
    assert result.dca_km == pytest.approx(0.0, abs=1e-15)
    assert result.poc > 0.99
    assert result.method == "chan"
    assert classify_risk(result.poc) == "CRITICAL"


def test_poc_low_when_miss_is_many_sigma() -> None:
    r_rel = np.array([5.0, 0.0, 0.0])
    v_rel = np.array([0.0, 7.5, 0.0])
    result = assess_collision(
        r_rel_km=r_rel,
        v_rel_km_s=v_rel,
        p1_diag_km2=(0.01, 0.01, 0.01),
        p2_diag_km2=(0.01, 0.01, 0.01),
        hbr_meters=20.0,
    )
    assert result.dca_km == pytest.approx(5.0)
    assert result.poc < 1e-6
    assert classify_risk(result.poc) == "LOW"


def test_chan_agrees_with_dblquad_centered() -> None:
    miss = np.array([0.0, 0.0])
    c2d = np.diag([1e-6, 1e-6])  # km²
    hbr = 0.01  # 10 m
    pc_chan = chan_poc(miss, c2d, hbr)
    pc_quad = integrate_poc_disk(miss, c2d, hbr)
    assert pc_chan == pytest.approx(pc_quad, rel=1e-3, abs=1e-4)


def test_chan_agrees_with_dblquad_offset_miss() -> None:
    miss = np.array([0.02, -0.01])  # km
    c2d = np.array([[4e-4, 1e-5], [1e-5, 9e-4]])
    hbr = 0.05
    pc_chan = chan_poc(miss, c2d, hbr)
    pc_quad = integrate_poc_disk(miss, c2d, hbr)
    # Chan's geometric-mean form is approximate for anisotropic σ; allow slack.
    assert abs(pc_chan - pc_quad) < max(5e-3, 0.25 * max(pc_quad, 1e-12))


def test_full_3x3_rtn_covariance_path() -> None:
    """Dense RTN covariances rotate into TEME before B-plane projection."""
    primary_r = np.array([6778.0, 0.0, 0.0])
    primary_v = np.array([0.0, 7.67, 0.0])
    secondary_r = primary_r + np.array([0.05, 0.0, 0.0])
    secondary_v = primary_v + np.array([0.0, 0.01, 0.0])
    r_rel = secondary_r - primary_r
    v_rel = secondary_v - primary_v

    # Correlated RTN covariances (radial / in-track coupling).
    p_rtn = [
        [1.0e-3, 5.0e-4, 0.0],
        [5.0e-4, 2.0e-3, 0.0],
        [0.0, 0.0, 5.0e-4],
    ]
    result = assess_collision(
        r_rel_km=r_rel,
        v_rel_km_s=v_rel,
        p1_km2=p_rtn,
        p2_km2=p_rtn,
        hbr_meters=50.0,
        covariance_frame="RTN",
        primary_position_km=primary_r,
        primary_velocity_km_s=primary_v,
        secondary_position_km=secondary_r,
        secondary_velocity_km_s=secondary_v,
        method="chan",
    )
    assert 0.0 <= result.poc <= 1.0
    assert result.dca_km == pytest.approx(0.05, rel=1e-9)
    assert result.c2d_km2.shape == (2, 2)


def test_singular_covariance_raises() -> None:
    miss = np.array([0.0, 0.0])
    c2d = np.array([[1.0, 0.0], [0.0, 0.0]])
    with pytest.raises(CovarianceError):
        chan_poc(miss, c2d, hbr_km=0.01)


def test_risk_classification_bands() -> None:
    assert classify_risk(2e-2) == "CRITICAL"
    assert classify_risk(5e-3) == "HIGH"
    assert classify_risk(5e-4) == "MEDIUM"
    assert classify_risk(1e-5) == "LOW"


# ---------------------------------------------------------------------------
# Unit: propagator
# ---------------------------------------------------------------------------


def test_propagate_iss_returns_teme_state() -> None:
    state = propagate_tle(ISS_TLE[0], ISS_TLE[1], TARGET_TIME, name="ISS")
    assert state.position_km.shape == (3,)
    assert state.velocity_km_s.shape == (3,)
    radius = float(np.linalg.norm(state.position_km))
    assert 6500.0 < radius < 7500.0
    speed = float(np.linalg.norm(state.velocity_km_s))
    assert 6.5 < speed < 8.5


def test_invalid_tle_raises_propagation_error() -> None:
    with pytest.raises(PropagationError):
        propagate_tle(
            "not a tle line one at all....................................",
            "not a tle line two at all....................................",
            TARGET_TIME,
        )


def test_relative_state_same_object_is_zero() -> None:
    a = propagate_tle(ISS_TLE[0], ISS_TLE[1], TARGET_TIME, name="A")
    b = propagate_tle(ISS_TLE[0], ISS_TLE[1], TARGET_TIME, name="B")
    r_rel, v_rel = relative_state(a, b)
    assert np.linalg.norm(r_rel) < 1e-9
    assert np.linalg.norm(v_rel) < 1e-12


# ---------------------------------------------------------------------------
# API integration
# ---------------------------------------------------------------------------


def test_health_endpoint() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "AetherGuard"


def test_mesh_node_sync_burn_triggers_reroute() -> None:
    response = client.post(
        "/api/v1/mesh/node-sync",
        json={
            "node_id": "NODE-ALPHA",
            "position": [6778.0, 0.0, 0.0],
            "velocity": [0.0, 7.67, 0.0],
            "burn_scheduled": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mesh_reroute_active"] is True
    assert body["recommended_next_hop"] == "NODE-BRAVO"
    assert body["status"] == "degraded"


def test_mesh_node_sync_nominal_no_reroute() -> None:
    response = client.post(
        "/api/v1/mesh/node-sync",
        json={
            "node_id": "NODE-BRAVO",
            "position": [6778.0, 100.0, 50.0],
            "velocity": [0.1, 7.5, 0.2],
            "burn_scheduled": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mesh_reroute_active"] is False
    assert body["recommended_next_hop"] is None


def test_assess_conjunction_self_encounter() -> None:
    response = client.post(
        "/api/v1/assess-conjunction",
        json={
            "primary_tle": {
                "name": "ISS",
                "line1": ISS_TLE[0],
                "line2": ISS_TLE[1],
            },
            "secondary_tle": {
                "name": "ISS-COPY",
                "line1": ISS_TLE[0],
                "line2": ISS_TLE[1],
            },
            "target_time": TARGET_TIME.isoformat(),
            "P1_diag": [1e-6, 1e-6, 1e-6],
            "P2_diag": [1e-6, 1e-6, 1e-6],
            "hbr_meters": 20.0,
            "poc_method": "chan",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dca_km"] == pytest.approx(0.0, abs=1e-6)
    assert body["poc"] > 0.9
    assert body["risk_level"] == "CRITICAL"
    assert body["action_required"] is True
    assert body["poc_method"] == "chan"


def test_assess_conjunction_full_rtn_matrix() -> None:
    p = [
        [1.0e-4, 1.0e-5, 0.0],
        [1.0e-5, 2.0e-4, 0.0],
        [0.0, 0.0, 5.0e-5],
    ]
    response = client.post(
        "/api/v1/assess-conjunction",
        json={
            "primary_tle": {"name": "ISS", "line1": ISS_TLE[0], "line2": ISS_TLE[1]},
            "secondary_tle": {
                "name": "ISS-COPY",
                "line1": ISS_TLE[0],
                "line2": ISS_TLE[1],
            },
            "target_time": TARGET_TIME.isoformat(),
            "P1": p,
            "P2": p,
            "hbr_meters": 20.0,
            "covariance_frame": "RTN",
            "poc_method": "chan",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["poc"] > 0.5
    assert body["poc_method"] == "chan"


def test_assess_conjunction_rejects_bad_tle() -> None:
    response = client.post(
        "/api/v1/assess-conjunction",
        json={
            "primary_tle": {
                "line1": "1 " + "x" * 67,
                "line2": "2 " + "y" * 67,
            },
            "secondary_tle": {
                "line1": ISS_TLE[0],
                "line2": ISS_TLE[1],
            },
            "target_time": TARGET_TIME.isoformat(),
            "P1_diag": [0.01, 0.01, 0.01],
            "P2_diag": [0.01, 0.01, 0.01],
            "hbr_meters": 10.0,
        },
    )
    assert response.status_code == 422


def test_assess_conjunction_distinct_objects() -> None:
    response = client.post(
        "/api/v1/assess-conjunction",
        json={
            "primary_tle": {"name": "ISS", "line1": ISS_TLE[0], "line2": ISS_TLE[1]},
            "secondary_tle": {
                "name": "HST",
                "line1": HST_TLE[0],
                "line2": HST_TLE[1],
            },
            "target_time": TARGET_TIME.isoformat(),
            "P1_diag": [0.1, 0.1, 0.1],
            "P2_diag": [0.1, 0.1, 0.1],
            "hbr_meters": 15.0,
        },
    )
    assert response.status_code in (200, 422)
    if response.status_code == 200:
        body = response.json()
        assert "dca_km" in body and "poc" in body
        assert body["risk_level"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
        assert isinstance(body["action_required"], bool)
        assert body["dca_km"] > 0.0


def test_sky_traffic_returns_full_catalog() -> None:
    response = client.get("/api/v1/sky-traffic")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 50
    assert body["catalog_size"] >= body["count"]
    assert len(body["satellites"]) == body["count"]
    sample = body["satellites"][0]
    assert {"id", "name", "lat_deg", "lon_deg", "alt_km", "line1", "line2"} <= set(sample)


def test_sky_traffic_track_for_selected_sat() -> None:
    traffic = client.get("/api/v1/sky-traffic").json()
    sat_id = traffic["satellites"][0]["id"]
    response = client.get(f"/api/v1/sky-traffic/{sat_id}/track")
    assert response.status_code == 200
    body = response.json()
    assert len(body["points"]) > 10
    assert "lat_deg" in body["points"][0]


def test_sky_traffic_track_covers_full_geo_orbit() -> None:
    """GEO sats must return a full inertial ring (frozen-ECEF), not a LEO stub."""
    traffic = client.get("/api/v1/sky-traffic").json()["satellites"]
    geo = next((s for s in traffic if s["alt_km"] > 30000), None)
    assert geo is not None
    response = client.get(f"/api/v1/sky-traffic/{geo['id']}/track")
    assert response.status_code == 200
    points = response.json()["points"]
    assert len(points) >= 100
    # Inertial GEO path in frozen ECEF should circumnavigate — wide X/Y span.
    xs = [p["position_km"][0] for p in points]
    ys = [p["position_km"][1] for p in points]
    assert max(xs) - min(xs) > 20000
    assert max(ys) - min(ys) > 20000
# --- POST /api/v1/plan-maneuver ----------------------------------------------

CO_LOCATED_PRIMARY = "25544"  # ISS (ZARYA)
CO_LOCATED_SECONDARY = "25575"  # ISS (UNITY) — shares the station's orbit


def _distant_geo_pair() -> tuple[str, str]:
    """Two GEO objects far enough apart that no burn is warranted."""
    sats = client.get("/api/v1/sky-traffic").json()["satellites"]
    geo = [s for s in sats if 35_000 < s["alt_km"] < 37_000]
    return geo[0]["id"], geo[1]["id"]


class TestPlanManeuver:
    def test_co_located_pair_gets_a_burn_that_clears_threshold(self) -> None:
        response = client.post(
            "/api/v1/plan-maneuver",
            json={
                "primary_id": CO_LOCATED_PRIMARY,
                "secondary_id": CO_LOCATED_SECONDARY,
                "hbr_meters": 20.0,
            },
        )
        assert response.status_code == 200
        body = response.json()

        assert body["poc_before"] > body["poc_after"]
        assert body["poc_after"] < 1e-6
        assert body["delta_v_magnitude_m_s"] > 0.0
        assert len(body["delta_v_m_s"]) == 3
        assert body["miss_distance_after_km"] > body["miss_distance_before_km"]

    def test_distant_pair_needs_no_burn(self) -> None:
        """A safe encounter's cheapest compliant maneuver is no maneuver."""
        primary, secondary = _distant_geo_pair()
        response = client.post(
            "/api/v1/plan-maneuver",
            json={"primary_id": primary, "secondary_id": secondary},
        )
        assert response.status_code == 200
        body = response.json()

        assert body["delta_v_magnitude_m_s"] == pytest.approx(0.0)
        assert body["risk_before"] == "LOW"

    def test_tracks_share_a_start_and_then_diverge(self) -> None:
        response = client.post(
            "/api/v1/plan-maneuver",
            json={
                "primary_id": CO_LOCATED_PRIMARY,
                "secondary_id": CO_LOCATED_SECONDARY,
            },
        )
        body = response.json()
        baseline = body["baseline_track"]
        maneuvered = body["maneuvered_track"]

        assert len(baseline) == len(maneuvered) > 2

        def separation(index: int) -> float:
            return float(
                np.linalg.norm(
                    np.array(baseline[index]["position_km"])
                    - np.array(maneuvered[index]["position_km"])
                )
            )

        # Both are sampled from the burn point with the same propagator, so any
        # separation is the burn alone and must grow monotonically from zero.
        assert separation(0) == pytest.approx(0.0, abs=1e-9)
        assert separation(len(baseline) // 2) > separation(0)
        assert separation(-1) > separation(len(baseline) // 2)

    def test_burn_lead_respects_the_cap(self) -> None:
        primary, secondary = _distant_geo_pair()
        response = client.post(
            "/api/v1/plan-maneuver",
            json={
                "primary_id": primary,
                "secondary_id": secondary,
                "max_burn_lead_hours": 3.0,
            },
        )
        assert response.json()["burn_lead_hours"] == pytest.approx(3.0, abs=1e-3)

    def test_identical_objects_rejected(self) -> None:
        response = client.post(
            "/api/v1/plan-maneuver",
            json={"primary_id": CO_LOCATED_PRIMARY, "secondary_id": CO_LOCATED_PRIMARY},
        )
        assert response.status_code == 422
        assert "different objects" in response.json()["detail"]

    def test_unknown_id_is_404(self) -> None:
        response = client.post(
            "/api/v1/plan-maneuver",
            json={"primary_id": "000000", "secondary_id": CO_LOCATED_SECONDARY},
        )
        assert response.status_code == 404

    def test_hard_body_radius_unit_slip_is_rejected(self) -> None:
        """hbr_meters=10000 means a 10 km object; fail loudly, not with PoC=1."""
        response = client.post(
            "/api/v1/plan-maneuver",
            json={
                "primary_id": CO_LOCATED_PRIMARY,
                "secondary_id": CO_LOCATED_SECONDARY,
                "hbr_meters": 10000.0,
            },
        )
        assert response.status_code == 422
        assert "physical band" in response.json()["detail"]

    def test_plan_reports_the_tca_it_targeted(self) -> None:
        """The UI assesses and plans at this moment; without it, it uses 'now'
        and a pair that conjuncts hours away reads as Pc 0."""
        response = client.post(
            "/api/v1/plan-maneuver",
            json={
                "primary_id": CO_LOCATED_PRIMARY,
                "secondary_id": CO_LOCATED_SECONDARY,
                "target_time": "2026-08-01T12:00:00Z",
            },
        )
        assert response.status_code == 200
        assert response.json()["tca"].startswith("2026-08-01T12:00:00")

    def test_testbed_pair_is_dangerous_at_its_tca_but_not_now(self) -> None:
        """Pins the bug where assessment ran at 'now' and reported Pc 0 while
        the planner, using the TCA, reported a burn was needed."""
        deployed = client.post("/api/v1/testbed/deploy", json={}).json()["deployed"]
        pair = deployed[1]

        at_tca = client.post(
            "/api/v1/plan-maneuver",
            json={
                "primary_id": pair["id"],
                "secondary_id": pair["target_id"],
                "target_time": pair["tca"],
            },
        ).json()
        assert at_tca["poc_before"] > 1e-6
        assert at_tca["delta_v_magnitude_m_s"] > 0.0
