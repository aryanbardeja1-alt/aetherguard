"""Tests for the synthetic AetherGuard conjunction satellites."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from engine import testbed
from engine.catalog import clear_runtime_objects, get_entry
from engine.propagator import propagate_tle
from main import app

client = TestClient(app)

NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)

#: A real conjunction must be close enough to actually warrant a burn. The
#: node-only solution left tens of km here, which reads as LOW risk.
MAX_ACCEPTABLE_MISS_KM = 2.0


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_runtime_objects()
    yield
    clear_runtime_objects()


class TestTleRewriting:
    def test_checksum_matches_the_source_line(self) -> None:
        """Rewriting must leave a line NORAD would still accept."""
        entry = get_entry("25544")
        line2 = entry["line2"]
        assert testbed.tle_checksum(line2) == int(line2[68])

    def test_derived_line_keeps_width_and_checksum(self) -> None:
        entry = get_entry("25544")
        derived = testbed.derive_line2(
            entry["line2"], inclination_deg=56.5, raan_deg=115.7, mean_anomaly_deg=200.0
        )
        assert len(derived) == 69
        assert testbed.tle_checksum(derived) == int(derived[68])

    def test_derived_line_is_propagable(self) -> None:
        entry = get_entry("25544")
        derived = testbed.derive_line2(
            entry["line2"], inclination_deg=56.5, raan_deg=115.7, mean_anomaly_deg=200.0
        )
        state = propagate_tle(entry["line1"], derived, NOW, validate_with_skyfield=False)
        assert np.all(np.isfinite(state.position_km))

    def test_angles_wrap_into_range(self) -> None:
        entry = get_entry("25544")
        derived = testbed.derive_line2(
            entry["line2"], inclination_deg=51.6, raan_deg=370.0, mean_anomaly_deg=-10.0
        )
        assert float(derived[17:25]) == pytest.approx(10.0, abs=1e-3)
        assert float(derived[43:51]) == pytest.approx(350.0, abs=1e-3)


class TestConjunctionGeometry:
    @pytest.mark.parametrize("target_id", testbed.DEFAULT_TARGET_IDS)
    def test_each_default_target_gets_a_real_close_approach(self, target_id: str) -> None:
        pair = testbed.build_satellite(
            get_entry(target_id), NOW, name="TEST", sat_id="TEST-1"
        )
        assert pair.miss_distance_km < MAX_ACCEPTABLE_MISS_KM

    def test_reported_miss_matches_propagated_states(self) -> None:
        """The advertised miss must be what the TLEs actually produce."""
        entry = get_entry("25544")
        pair = testbed.build_satellite(entry, NOW, name="TEST", sat_id="TEST-1")

        primary = propagate_tle(
            entry["line1"], entry["line2"], pair.tca, validate_with_skyfield=False
        )
        secondary = propagate_tle(
            pair.satellite["line1"],
            pair.satellite["line2"],
            pair.tca,
            validate_with_skyfield=False,
        )
        assert float(
            np.linalg.norm(secondary.position_km - primary.position_km)
        ) == pytest.approx(pair.miss_distance_km, abs=1e-6)

    def test_crossing_geometry_has_real_relative_velocity(self) -> None:
        """Co-orbital clones would be near zero and exercise nothing."""
        pair = testbed.build_satellite(
            get_entry("25544"), NOW, name="TEST", sat_id="TEST-1"
        )
        assert pair.relative_speed_km_s > 0.1

    def test_encounter_is_in_the_future(self) -> None:
        pair = testbed.build_satellite(
            get_entry("25544"), NOW, name="TEST", sat_id="TEST-1"
        )
        assert pair.tca >= NOW


class TestDeployEndpoint:
    def test_deploy_registers_satellites_in_the_catalog(self) -> None:
        response = client.post("/api/v1/testbed/deploy", json={})
        assert response.status_code == 200
        deployed = response.json()["deployed"]
        assert len(deployed) == len(testbed.DEFAULT_TARGET_IDS)

        names = [s["name"] for s in client.get("/api/v1/sky-traffic").json()["satellites"]]
        for entry in deployed:
            assert entry["name"] in names

    def test_deployed_satellites_are_named_in_sequence(self) -> None:
        deployed = client.post("/api/v1/testbed/deploy", json={}).json()["deployed"]
        assert [s["name"] for s in deployed] == [
            f"AETHERGUARD {i}" for i in range(1, len(deployed) + 1)
        ]

    def test_each_pair_yields_a_burn_at_its_tca(self) -> None:
        """The whole point: every deployed pair must exercise the planner."""
        deployed = client.post("/api/v1/testbed/deploy", json={}).json()["deployed"]

        for entry in deployed:
            plan = client.post(
                "/api/v1/plan-maneuver",
                json={
                    "primary_id": entry["id"],
                    "secondary_id": entry["target_id"],
                    "target_time": entry["tca"],
                    "hbr_meters": 20.0,
                },
            )
            assert plan.status_code == 200, entry["name"]
            body = plan.json()
            assert body["delta_v_magnitude_m_s"] > 0.0, entry["name"]
            assert body["poc_before"] > body["poc_after"], entry["name"]
            assert body["poc_after"] < 1e-6, entry["name"]

    def test_redeploy_replaces_rather_than_accumulates(self) -> None:
        client.post("/api/v1/testbed/deploy", json={})
        client.post("/api/v1/testbed/deploy", json={})
        names = [s["name"] for s in client.get("/api/v1/sky-traffic").json()["satellites"]]
        assert len([n for n in names if n.startswith("AETHERGUARD")]) == len(
            testbed.DEFAULT_TARGET_IDS
        )

    def test_clear_removes_them(self) -> None:
        client.post("/api/v1/testbed/deploy", json={})
        assert client.delete("/api/v1/testbed/deploy").status_code == 200
        names = [s["name"] for s in client.get("/api/v1/sky-traffic").json()["satellites"]]
        assert not [n for n in names if n.startswith("AETHERGUARD")]

    def test_unknown_target_is_404(self) -> None:
        response = client.post("/api/v1/testbed/deploy", json={"target_ids": ["000000"]})
        assert response.status_code == 404
