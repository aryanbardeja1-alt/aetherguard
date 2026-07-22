"""Tests for adaptive orbit sampling.

Uniform time steps drew Cluster II-FM8 (e=0.91) as a chord slicing across its
own perigee: 92 degrees of arc between consecutive samples, an 11,374 km jump.
These pin the fix.
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from engine.sampling import (
    DEFAULT_MAX_TURN_DEG,
    sample_by_turn_angle,
    worst_turn_degrees,
)
from main import app

client = TestClient(app)

#: No drawn segment may turn more than this, with slack for the point budget.
MAX_RENDERED_TURN_DEG = 8.0


def _ellipse(eccentricity: float):
    """Positions around a Kepler ellipse, parameterised by time-like mean anomaly."""

    def evaluate(mean_anomaly: float) -> np.ndarray:
        e = eccentricity
        anomaly = mean_anomaly
        for _ in range(60):
            step = (anomaly - e * np.sin(anomaly) - mean_anomaly) / (
                1 - e * np.cos(anomaly)
            )
            anomaly -= step
            if abs(step) < 1e-13:
                break
        a = 80000.0
        return np.array(
            [a * (np.cos(anomaly) - e), a * np.sqrt(1 - e**2) * np.sin(anomaly), 0.0]
        )

    return evaluate


class TestSampleByTurnAngle:
    @pytest.mark.parametrize("eccentricity", [0.0, 0.4, 0.78, 0.91])
    def test_no_segment_turns_more_than_the_limit(self, eccentricity: float) -> None:
        _, points = sample_by_turn_angle(
            _ellipse(eccentricity), 0.0, 2 * np.pi, max_points=600
        )
        assert worst_turn_degrees(points) <= DEFAULT_MAX_TURN_DEG + 1e-6

    def test_uniform_sampling_would_have_failed(self) -> None:
        """Establishes that the adaptive step is what fixes it, not luck."""
        evaluate = _ellipse(0.91)
        uniform = [evaluate(t) for t in np.linspace(0.0, 2 * np.pi, 181)]
        assert worst_turn_degrees(uniform) > 60.0

        _, adaptive = sample_by_turn_angle(evaluate, 0.0, 2 * np.pi, max_points=600)
        assert worst_turn_degrees(adaptive) < 5.0

    def test_eccentric_sampling_concentrates_near_perigee(self) -> None:
        """The point of the exercise: samples land where the path bends.

        Point *count* does not distinguish the two — a circular orbit also
        refines, since 48 seed points already turn 7.7 degrees a segment. What
        matters is that an eccentric orbit's samples bunch up near perigee
        while a circular one's stay evenly spread.
        """

        # Circular: every segment turns alike, so spacing stays uniform. A
        # radius split says nothing here, since every radius is the same.
        circular_times, _ = sample_by_turn_angle(
            _ellipse(0.0), 0.0, 2 * np.pi, max_points=600
        )
        circular_gaps = np.diff(np.array(circular_times))
        assert circular_gaps.std() / circular_gaps.mean() < 0.05

        # Eccentric: the near half of the orbit gets much finer spacing.
        times, points = sample_by_turn_angle(
            _ellipse(0.91), 0.0, 2 * np.pi, max_points=600
        )
        radii = np.array([float(np.linalg.norm(p)) for p in points])
        gaps = np.diff(np.array(times))
        segment_radius = 0.5 * (radii[:-1] + radii[1:])
        midpoint = float(np.median(segment_radius))
        inner = float(gaps[segment_radius <= midpoint].mean())
        outer = float(gaps[segment_radius > midpoint].mean())
        assert outer / inner > 3.0

    def test_respects_the_point_budget(self) -> None:
        times, _ = sample_by_turn_angle(
            _ellipse(0.95), 0.0, 2 * np.pi, max_points=120
        )
        assert len(times) <= 120

    def test_samples_stay_ordered_and_within_bounds(self) -> None:
        times, _ = sample_by_turn_angle(_ellipse(0.7), 0.0, 10.0)
        assert times == sorted(times)
        assert times[0] == pytest.approx(0.0)
        assert times[-1] == pytest.approx(10.0)


class TestRenderedTracks:
    def test_highly_eccentric_catalog_orbit_is_smooth(self) -> None:
        """Cluster II-FM8 (TANGO): e=0.91, the orbit that exposed this."""
        sats = client.get("/api/v1/sky-traffic").json()["satellites"]
        tango = next(s for s in sats if "TANGO" in s["name"])

        points = client.get(f"/api/v1/sky-traffic/{tango['id']}/track").json()["points"]
        positions = [np.array(p["position_km"]) for p in points]
        assert worst_turn_degrees(positions) < MAX_RENDERED_TURN_DEG

    def test_burn_tracks_are_index_aligned(self) -> None:
        """The chart, miss marker and exaggeration all pair the two by index."""
        deployed = client.post("/api/v1/testbed/deploy", json={}).json()["deployed"]
        pair = deployed[-1]
        plan = client.post(
            "/api/v1/plan-maneuver",
            json={
                "primary_id": pair["id"],
                "secondary_id": pair["target_id"],
                "target_time": pair["tca"],
            },
        ).json()
        assert len(plan["baseline_track"]) == len(plan["maneuvered_track"])

    def test_burn_track_covers_a_whole_revolution(self) -> None:
        """A 24 h span is 38% of a Chandra-class orbit and drew as an arc stub."""
        deployed = client.post("/api/v1/testbed/deploy", json={}).json()["deployed"]
        pair = deployed[-1]
        plan = client.post(
            "/api/v1/plan-maneuver",
            json={
                "primary_id": pair["id"],
                "secondary_id": pair["target_id"],
                "target_time": pair["tca"],
            },
        ).json()

        track = np.array([p["position_km"] for p in plan["baseline_track"]])
        unit = track / np.linalg.norm(track, axis=1)[:, None]
        swept = float(
            np.degrees(
                np.arccos(np.clip(np.einsum("ij,ij->i", unit[:-1], unit[1:]), -1, 1))
            ).sum()
        )
        assert swept > 350.0
