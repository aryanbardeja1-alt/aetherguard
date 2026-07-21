"""Unit tests for the AetherGuard trajectory engine."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from aether_core import ConjunctionEvent, ManeuverPlan
from trajectory_engine import (
    MESH_SAFE_SEPARATION_KM,
    SAFE_PROBABILITY,
    ManeuverConstraintError,
    TrajectoryOptimizer,
    _collision_probability,
    _line_of_sight_blocked,
)

TCA = datetime(2026, 8, 1, 12, 0, 0)

# Circular LEO at 7000 km, with a head-on secondary passing 60 m away at TCA.
SAT_STATE = np.array([7000.0, 0.0, 0.0, 0.0, 7.5461, 0.0])
OBJECT_STATE = np.array([7000.0, 0.0, 0.06, 0.0, -7.5461, 0.3])

# A mesh neighbour 30 degrees ahead in the same plane. Links stay clear of the
# Earth's limb out to ~48.7 degrees of central angle at this altitude.
VISIBLE_NEIGHBOUR = np.array([6062.18, 3500.0, 0.0, -3.773, 6.535, 0.0])


def make_event(mode: str = "INDEPENDENT", neighbors: list[str] | None = None) -> ConjunctionEvent:
    return ConjunctionEvent(
        event_id="CDM-0001",
        satellite_id="AETHER-07",
        sat_state_vector=SAT_STATE.copy(),
        object_state_vector=OBJECT_STATE.copy(),
        tca=TCA,
        probability_of_collision=1e-3,
        mode=mode,
        mesh_neighbors=neighbors,
    )


class TestCollisionProbability:
    def test_untreated_conjunction_is_dangerous(self) -> None:
        probability = _collision_probability(
            SAT_STATE[:3] - OBJECT_STATE[:3],
            SAT_STATE[3:] - OBJECT_STATE[3:],
            np.diag([0.09, 0.09, 0.09]),
            0.02,
        )
        assert probability > SAFE_PROBABILITY

    def test_probability_falls_as_miss_distance_grows(self) -> None:
        args = (np.diag([0.09, 0.09, 0.09]), 0.02)
        relative_velocity = np.array([0.0, 15.0, 0.0])
        close = _collision_probability(np.array([0.05, 0.0, 0.0]), relative_velocity, *args)
        far = _collision_probability(np.array([5.0, 0.0, 0.0]), relative_velocity, *args)
        assert far < close

    def test_zero_relative_velocity_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="encounter plane is undefined"):
            _collision_probability(
                np.array([1.0, 0.0, 0.0]),
                np.zeros(3),
                np.diag([0.09, 0.09, 0.09]),
                0.02,
            )


class TestLineOfSight:
    def test_adjacent_satellites_can_see_each_other(self) -> None:
        assert not _line_of_sight_blocked(
            np.array([7000.0, 0.0, 0.0]), np.array([6900.0, 1000.0, 0.0])
        )

    def test_earth_occults_antipodal_satellites(self) -> None:
        assert _line_of_sight_blocked(
            np.array([7000.0, 0.0, 0.0]), np.array([-7000.0, 0.0, 0.0])
        )


class TestIndependentAvoidance:
    def test_burn_reaches_the_safety_threshold(self) -> None:
        plan = TrajectoryOptimizer().calculate_independent_avoidance(make_event())
        assert plan.new_probability < SAFE_PROBABILITY

    def test_plan_matches_the_dataclass_contract(self) -> None:
        plan = TrajectoryOptimizer().calculate_independent_avoidance(make_event())

        assert isinstance(plan, ManeuverPlan)
        assert plan.event_id == "CDM-0001"
        assert plan.satellite_id == "AETHER-07"
        assert isinstance(plan.delta_v, np.ndarray)
        assert plan.delta_v.shape == (3,)
        assert plan.delta_v.dtype == np.float64
        assert isinstance(plan.burn_time, datetime)
        assert isinstance(plan.new_probability, float)
        assert isinstance(plan.requires_mesh_rerouting, bool)

    def test_burn_occurs_half_an_orbit_before_tca(self) -> None:
        plan = TrajectoryOptimizer().calculate_independent_avoidance(make_event())
        lead_time = TCA - plan.burn_time.replace(tzinfo=None)
        # Half of a ~97 minute orbit.
        assert timedelta(minutes=45) < lead_time < timedelta(minutes=55)

    def test_independent_mode_never_flags_rerouting(self) -> None:
        event = make_event(neighbors=["AETHER-06"])
        plan = TrajectoryOptimizer().calculate_independent_avoidance(event)
        assert plan.requires_mesh_rerouting is False

    def test_burn_respects_the_propulsion_limit(self) -> None:
        optimizer = TrajectoryOptimizer()
        plan = optimizer.calculate_independent_avoidance(make_event())
        assert 0.0 < np.linalg.norm(plan.delta_v) <= optimizer.max_delta_v_mps


class TestManeuverConstraints:
    def test_unreachable_threshold_raises(self) -> None:
        optimizer = TrajectoryOptimizer(max_delta_v_mps=1e-4)
        with pytest.raises(ManeuverConstraintError, match="no impulse within"):
            optimizer.calculate_independent_avoidance(make_event())

    def test_error_reports_the_untreated_probability(self) -> None:
        optimizer = TrajectoryOptimizer(max_delta_v_mps=1e-4)
        with pytest.raises(ManeuverConstraintError) as excinfo:
            optimizer.calculate_independent_avoidance(make_event())
        assert "currently" in str(excinfo.value)


class TestConstellationAvoidance:
    def test_distant_neighbour_leaves_mesh_intact(self) -> None:
        event = make_event(mode="CONSTELLATION", neighbors=["AETHER-06"])
        optimizer = TrajectoryOptimizer(
            neighbor_states={"AETHER-06": VISIBLE_NEIGHBOUR.copy()}
        )
        # The link is genuinely up beforehand, so a False result means the burn
        # preserved it rather than the link having been down all along.
        assert not _line_of_sight_blocked(SAT_STATE[:3], VISIBLE_NEIGHBOUR[:3])

        plan = optimizer.calculate_constellation_avoidance(event)
        assert plan.new_probability < SAFE_PROBABILITY
        assert plan.requires_mesh_rerouting is False

    def test_close_neighbour_triggers_rerouting(self) -> None:
        event = make_event(mode="CONSTELLATION", neighbors=["AETHER-06"])
        reference = TrajectoryOptimizer().calculate_independent_avoidance(event)
        post_burn = TrajectoryOptimizer()._position_at_tca(event, reference.delta_v)

        # Park the neighbour just inside the separation floor.
        neighbour = np.concatenate([post_burn + np.array([0.0, 0.0, 1.0]), np.zeros(3)])
        optimizer = TrajectoryOptimizer(neighbor_states={"AETHER-06": neighbour})
        plan = optimizer.calculate_constellation_avoidance(event)

        assert plan.requires_mesh_rerouting is True

    def test_newly_occulted_link_triggers_rerouting(self) -> None:
        event = make_event(mode="CONSTELLATION", neighbors=["AETHER-06"])
        neighbour = VISIBLE_NEIGHBOUR.copy()
        optimizer = TrajectoryOptimizer(neighbor_states={"AETHER-06": neighbour})

        # The link is clear from the pre-burn position...
        assert not _line_of_sight_blocked(SAT_STATE[:3], neighbour[:3])
        # ...but the Earth occults it from the far side of the orbit.
        post_burn = np.array([-7000.0, 0.0, 0.0])
        assert _line_of_sight_blocked(post_burn, neighbour[:3])

        assert optimizer._mesh_is_disrupted(event, post_burn) is True

    def test_already_occulted_link_does_not_trigger_rerouting(self) -> None:
        """Rerouting reacts to links the burn *breaks*, not ones already down."""
        event = make_event(mode="CONSTELLATION", neighbors=["AETHER-06"])
        neighbour = np.concatenate([-SAT_STATE[:3], np.zeros(3)])
        optimizer = TrajectoryOptimizer(neighbor_states={"AETHER-06": neighbour})

        assert _line_of_sight_blocked(SAT_STATE[:3], neighbour[:3])
        post_burn = SAT_STATE[:3] + np.array([0.0, 2.0, 0.0])
        assert optimizer._mesh_is_disrupted(event, post_burn) is False

    def test_no_neighbours_leaves_mesh_intact(self) -> None:
        event = make_event(mode="CONSTELLATION", neighbors=None)
        plan = TrajectoryOptimizer().calculate_constellation_avoidance(event)
        assert plan.requires_mesh_rerouting is False

    def test_missing_neighbour_state_is_rejected(self) -> None:
        event = make_event(mode="CONSTELLATION", neighbors=["AETHER-99"])
        with pytest.raises(ValueError, match="AETHER-99"):
            TrajectoryOptimizer().calculate_constellation_avoidance(event)

    def test_separation_floor_is_ten_kilometres(self) -> None:
        assert MESH_SAFE_SEPARATION_KM == 10.0
