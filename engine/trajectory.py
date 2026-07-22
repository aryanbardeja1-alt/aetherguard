"""Trajectory Engine: impulsive collision-avoidance planning for AetherGuard.

Takes a :class:`ConjunctionEvent` and produces a :class:`ManeuverPlan` describing
the minimum-magnitude impulsive burn that drops the probability of collision
below the safety threshold.

The burn is placed half an orbit before TCA, capped at
:data:`MAX_BURN_LEAD_HOURS`. The cap matters for the large Earth orbits in the
catalog: a half period is 12 hours at GEO and over 31 hours for Chandra-class
elliptical orbits, which is not a usable lead time.

Orbit construction and propagation are delegated to ``poliastro``/``astropy``,
and the collision-probability integral to :mod:`engine.collision`. The
line-of-sight occultation test lives here because no library provides it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Mapping, Optional, Tuple

import numpy as np
from astropy import units as u
from astropy.time import Time
from poliastro.bodies import Earth
from poliastro.twobody import Orbit
from scipy.optimize import brentq

from aether_core import ConjunctionEvent, ManeuverPlan
from engine.collision import build_encounter_rotation, chan_poc, project_covariance_2d

#: Collision probability that a maneuver must achieve.
SAFE_PROBABILITY: Final[float] = 1e-6

#: Maximum impulse the propulsion system can deliver in a single burn, m/s.
MAX_DELTA_V_MPS: Final[float] = 50.0

#: Minimum separation from a mesh neighbour before rerouting is required, km.
MESH_SAFE_SEPARATION_KM: Final[float] = 10.0

#: Ceiling on how far before TCA a burn may be scheduled, hours.
MAX_BURN_LEAD_HOURS: Final[float] = 12.0

_EARTH_RADIUS_KM: Final[float] = float(Earth.R.to(u.km).value)


class ManeuverConstraintError(Exception):
    """Raised when no physically achievable burn can satisfy the safety threshold."""


def _collision_probability(
    rel_position_km: np.ndarray,
    rel_velocity_km_s: np.ndarray,
    covariance_km2: np.ndarray,
    hard_body_radius_km: float,
) -> float:
    """Probability of collision over the B-plane, via :mod:`engine.collision`.

    ``covariance_km2`` is the *combined* uncertainty of both objects, so it is
    projected against a zero second covariance. Delegating keeps a single Chan
    implementation in the codebase and inherits its conditioning checks.
    """
    rotation = build_encounter_rotation(rel_velocity_km_s, fallback_axis=rel_position_km)
    c2d = project_covariance_2d(covariance_km2, np.zeros((3, 3)), rotation)
    miss = rotation @ np.asarray(rel_position_km, dtype=float).reshape(3)
    return chan_poc(miss, c2d, hard_body_radius_km)


def _line_of_sight_blocked(position_a_km: np.ndarray, position_b_km: np.ndarray) -> bool:
    """True if the Earth occults the straight path between two spacecraft."""
    segment = position_b_km - position_a_km
    length_squared = float(np.dot(segment, segment))
    if length_squared == 0.0:
        return False

    # Closest approach of the segment to the geocentre.
    t = float(np.clip(-np.dot(position_a_km, segment) / length_squared, 0.0, 1.0))
    closest = position_a_km + t * segment
    return bool(np.linalg.norm(closest) < _EARTH_RADIUS_KM)


class TrajectoryOptimizer:
    """Plans impulsive avoidance burns for a single spacecraft.

    ``ConjunctionEvent`` carries neither the state covariance nor the object
    sizes needed to recompute collision probability, and its ``mesh_neighbors``
    field holds bare identifiers rather than states. Because ``aether_core.py``
    is an immutable cross-team contract, that data is supplied here instead.

    Args:
        position_covariance_km2: Combined 3x3 ECI position covariance of the
            two objects at TCA. Defaults to an isotropic 300 m 1-sigma.
        hard_body_radius_km: Combined radius of the two objects.
        neighbor_states: Maps mesh neighbour ID to its ECI state vector at TCA,
            ``[x, y, z, vx, vy, vz]`` in km and km/s.
        max_delta_v_mps: Propulsion limit for a single impulsive burn.
        max_burn_lead_hours: Ceiling on how far before TCA the burn may sit.
            Below this the burn stays at half a period; above it the burn is
            pulled in, which is what keeps GEO and highly elliptical orbits
            practical.
    """

    def __init__(
        self,
        position_covariance_km2: Optional[np.ndarray] = None,
        hard_body_radius_km: float = 0.02,
        neighbor_states: Optional[Mapping[str, np.ndarray]] = None,
        max_delta_v_mps: float = MAX_DELTA_V_MPS,
        max_burn_lead_hours: float = MAX_BURN_LEAD_HOURS,
    ) -> None:
        self.position_covariance_km2: np.ndarray = (
            np.diag([0.09, 0.09, 0.09])
            if position_covariance_km2 is None
            else np.asarray(position_covariance_km2, dtype=float)
        )
        self.hard_body_radius_km: float = hard_body_radius_km
        self.neighbor_states: Mapping[str, np.ndarray] = neighbor_states or {}
        self.max_delta_v_mps: float = max_delta_v_mps
        self.max_burn_lead_hours: float = max_burn_lead_hours

    def calculate_independent_avoidance(self, event: ConjunctionEvent) -> ManeuverPlan:
        """Plan a burn for a satellite operating with no mesh obligations."""
        delta_v_mps, probability, burn_time = self._solve_avoidance_burn(event)
        return ManeuverPlan(
            event_id=event.event_id,
            satellite_id=event.satellite_id,
            delta_v=delta_v_mps,
            burn_time=burn_time,
            new_probability=probability,
            requires_mesh_rerouting=False,
        )

    def calculate_constellation_avoidance(self, event: ConjunctionEvent) -> ManeuverPlan:
        """Plan a burn and check whether it breaks the satellite's mesh links.

        Rerouting is flagged when the post-burn trajectory closes to within
        :data:`MESH_SAFE_SEPARATION_KM` of a neighbour, or when a link that had
        clear line of sight before the burn becomes occulted by the Earth.
        """
        delta_v_mps, probability, burn_time = self._solve_avoidance_burn(event)
        post_burn_position = self._position_at_tca(event, delta_v_mps)

        return ManeuverPlan(
            event_id=event.event_id,
            satellite_id=event.satellite_id,
            delta_v=delta_v_mps,
            burn_time=burn_time,
            new_probability=probability,
            requires_mesh_rerouting=self._mesh_is_disrupted(event, post_burn_position),
        )

    def _mesh_is_disrupted(
        self, event: ConjunctionEvent, post_burn_position_km: np.ndarray
    ) -> bool:
        """True if the burn violates separation or breaks a line-of-sight link."""
        pre_burn_position = np.asarray(event.sat_state_vector, dtype=float)[:3]

        for neighbor_id in event.mesh_neighbors or []:
            if neighbor_id not in self.neighbor_states:
                raise ValueError(
                    f"No state vector supplied for mesh neighbour {neighbor_id!r}; "
                    "cannot evaluate self-healing criteria."
                )
            neighbor_position = np.asarray(
                self.neighbor_states[neighbor_id], dtype=float
            )[:3]

            separation = float(
                np.linalg.norm(post_burn_position_km - neighbor_position)
            )
            if separation < MESH_SAFE_SEPARATION_KM:
                return True

            was_clear = not _line_of_sight_blocked(pre_burn_position, neighbor_position)
            now_blocked = _line_of_sight_blocked(
                post_burn_position_km, neighbor_position
            )
            if was_clear and now_blocked:
                return True

        return False

    def _burn_state(
        self, event: ConjunctionEvent
    ) -> Tuple[np.ndarray, np.ndarray, datetime, float]:
        """Back-propagate to the burn point: half an orbit before TCA, capped.

        Raises:
            ManeuverConstraintError: If the orbit is not closed, since an open
                trajectory has no period to take half of.
        """
        state = np.asarray(event.sat_state_vector, dtype=float)
        tca = Time(event.tca, scale="utc")

        orbit_at_tca = Orbit.from_vectors(
            Earth, state[:3] * u.km, state[3:] * u.km / u.s, epoch=tca
        )

        eccentricity = float(orbit_at_tca.ecc.value)
        if eccentricity >= 1.0:
            raise ManeuverConstraintError(
                f"Event {event.event_id}: orbit is not closed (e={eccentricity:.4f}), "
                "so it has no period and no half-orbit burn point."
            )

        lead_seconds = min(
            float((orbit_at_tca.period / 2.0).to(u.s).value),
            self.max_burn_lead_hours * 3600.0,
        )
        lead = lead_seconds * u.s
        orbit_at_burn = orbit_at_tca.propagate(-lead)

        return (
            np.asarray(orbit_at_burn.r.to(u.km).value, dtype=float),
            np.asarray(orbit_at_burn.v.to(u.km / u.s).value, dtype=float),
            (tca - lead).to_datetime(),
            lead_seconds,
        )

    def _position_at_tca(
        self, event: ConjunctionEvent, delta_v_mps: np.ndarray
    ) -> np.ndarray:
        """Satellite position at TCA after applying ``delta_v_mps`` at the burn point."""
        position, velocity, burn_time, lead_seconds = self._burn_state(event)
        boosted = velocity + np.asarray(delta_v_mps, dtype=float) / 1000.0

        orbit = Orbit.from_vectors(
            Earth,
            position * u.km,
            boosted * u.km / u.s,
            epoch=Time(burn_time, scale="utc"),
        )
        return np.asarray(
            orbit.propagate(lead_seconds * u.s).r.to(u.km).value, dtype=float
        )

    def _solve_avoidance_burn(
        self, event: ConjunctionEvent
    ) -> Tuple[np.ndarray, float, datetime]:
        """Find the smallest along-track impulse that reaches :data:`SAFE_PROBABILITY`.

        Along-track is the standard operational burn direction, so the search
        reduces to a signed scalar. ``scipy.optimize.brentq`` refines the
        smallest magnitude whose post-burn probability meets the threshold.

        Raises:
            ManeuverConstraintError: If no impulse within the propulsion limit
                reaches the threshold.
        """
        position, velocity, burn_time, lead_seconds = self._burn_state(event)
        along_track = velocity / float(np.linalg.norm(velocity))
        object_state = np.asarray(event.object_state_vector, dtype=float)
        burn_epoch = Time(burn_time, scale="utc")

        def probability_at(magnitude_mps: float) -> float:
            boosted = velocity + along_track * (magnitude_mps / 1000.0)
            orbit = Orbit.from_vectors(
                Earth, position * u.km, boosted * u.km / u.s, epoch=burn_epoch
            )
            at_tca = orbit.propagate(lead_seconds * u.s)
            return _collision_probability(
                np.asarray(at_tca.r.to(u.km).value, dtype=float) - object_state[:3],
                np.asarray(at_tca.v.to(u.km / u.s).value, dtype=float)
                - object_state[3:],
                self.position_covariance_km2,
                self.hard_body_radius_km,
            )

        def margin(magnitude_mps: float) -> float:
            return probability_at(magnitude_mps) - SAFE_PROBABILITY

        # Walk outwards from zero; the first magnitude that clears the threshold
        # in either direction is the minimum, then refine inside that bracket.
        steps = 60
        step_mps = self.max_delta_v_mps / steps
        for index in range(1, steps + 1):
            magnitude = index * step_mps
            for signed in (magnitude, -magnitude):
                if margin(signed) < 0.0:
                    previous = np.sign(signed) * (magnitude - step_mps)
                    root = float(brentq(margin, previous, signed, xtol=1e-6))

                    # brentq lands *on* the threshold, so the root itself may
                    # still sit fractionally above it. Close in from the
                    # known-safe end until the burn is strictly compliant.
                    unsafe, safe = root, signed
                    if probability_at(root) < SAFE_PROBABILITY:
                        safe = root
                    else:
                        for _ in range(40):
                            midpoint = 0.5 * (unsafe + safe)
                            if probability_at(midpoint) < SAFE_PROBABILITY:
                                safe = midpoint
                            else:
                                unsafe = midpoint

                    return along_track * safe, probability_at(safe), burn_time

        raise ManeuverConstraintError(
            f"Event {event.event_id}: no impulse within "
            f"{self.max_delta_v_mps:.1f} m/s reduces collision probability to "
            f"{SAFE_PROBABILITY:.1e} (currently {probability_at(0.0):.3e})."
        )
