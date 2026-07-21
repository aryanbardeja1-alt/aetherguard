"""Trajectory Engine: impulsive collision-avoidance planning for AetherGuard.

Takes a :class:`ConjunctionEvent` and produces a :class:`ManeuverPlan` describing
the minimum-magnitude impulsive burn that drops the probability of collision
below the safety threshold, executed half an orbit before TCA.

Orbit construction and propagation are delegated to ``poliastro``/``astropy``.
The collision-probability integral (Chan's method) and the line-of-sight
occultation test are implemented here because neither library provides them.
"""

from __future__ import annotations

from datetime import datetime
from math import exp, factorial
from typing import Final, Mapping, Optional, Tuple

import numpy as np
from astropy import units as u
from astropy.time import Time
from poliastro.bodies import Earth
from poliastro.twobody import Orbit
from scipy.optimize import brentq

from aether_core import ConjunctionEvent, ManeuverPlan

#: Collision probability that a maneuver must achieve.
SAFE_PROBABILITY: Final[float] = 1e-6

#: Maximum impulse the propulsion system can deliver in a single burn, m/s.
MAX_DELTA_V_MPS: Final[float] = 50.0

#: Minimum separation from a mesh neighbour before rerouting is required, km.
MESH_SAFE_SEPARATION_KM: Final[float] = 10.0

_EARTH_RADIUS_KM: Final[float] = float(Earth.R.to(u.km).value)


class ManeuverConstraintError(Exception):
    """Raised when no physically achievable burn can satisfy the safety threshold."""


def _collision_probability(
    rel_position_km: np.ndarray,
    rel_velocity_km_s: np.ndarray,
    covariance_km2: np.ndarray,
    hard_body_radius_km: float,
) -> float:
    """Probability of collision via Chan's series over the B-plane.

    The combined position uncertainty and the miss vector are projected onto the
    encounter plane (normal to the relative velocity), then the 2-D Gaussian is
    integrated over a disk of radius ``hard_body_radius_km``.
    """
    speed = float(np.linalg.norm(rel_velocity_km_s))
    if speed == 0.0:
        raise ValueError("Relative velocity is zero; the encounter plane is undefined.")

    normal = rel_velocity_km_s / speed

    # Orthonormal basis spanning the encounter plane.
    in_plane = rel_position_km - np.dot(rel_position_km, normal) * normal
    if np.linalg.norm(in_plane) < 1e-12:
        seed = np.array([1.0, 0.0, 0.0])
        if abs(normal[0]) > 0.9:
            seed = np.array([0.0, 1.0, 0.0])
        in_plane = seed - np.dot(seed, normal) * normal
    axis_1 = in_plane / np.linalg.norm(in_plane)
    axis_2 = np.cross(normal, axis_1)

    basis = np.column_stack((axis_1, axis_2))
    miss_2d = basis.T @ rel_position_km
    cov_2d = basis.T @ covariance_km2 @ basis

    # Principal axes of the projected covariance.
    variances, rotation = np.linalg.eigh(cov_2d)
    variances = np.clip(variances, 1e-18, None)
    miss_principal = rotation.T @ miss_2d

    sigma_x, sigma_y = np.sqrt(variances)
    scaled_area = hard_body_radius_km**2 / (sigma_x * sigma_y)
    scaled_miss = float(
        miss_principal[0] ** 2 / variances[0] + miss_principal[1] ** 2 / variances[1]
    )

    total = 0.0
    for m in range(50):
        inner = 1.0 - exp(-scaled_area / 2.0) * sum(
            (scaled_area / 2.0) ** k / factorial(k) for k in range(m + 1)
        )
        term = ((scaled_miss / 2.0) ** m / factorial(m)) * inner
        total += term
        if m > 5 and term < 1e-18:
            break

    return float(np.clip(exp(-scaled_miss / 2.0) * total, 0.0, 1.0))


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
    """

    def __init__(
        self,
        position_covariance_km2: Optional[np.ndarray] = None,
        hard_body_radius_km: float = 0.02,
        neighbor_states: Optional[Mapping[str, np.ndarray]] = None,
        max_delta_v_mps: float = MAX_DELTA_V_MPS,
    ) -> None:
        self.position_covariance_km2: np.ndarray = (
            np.diag([0.09, 0.09, 0.09])
            if position_covariance_km2 is None
            else np.asarray(position_covariance_km2, dtype=float)
        )
        self.hard_body_radius_km: float = hard_body_radius_km
        self.neighbor_states: Mapping[str, np.ndarray] = neighbor_states or {}
        self.max_delta_v_mps: float = max_delta_v_mps

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
        """Back-propagate the satellite half an orbit to locate the burn point."""
        state = np.asarray(event.sat_state_vector, dtype=float)
        tca = Time(event.tca, scale="utc")

        orbit_at_tca = Orbit.from_vectors(
            Earth, state[:3] * u.km, state[3:] * u.km / u.s, epoch=tca
        )
        half_period = orbit_at_tca.period / 2.0
        orbit_at_burn = orbit_at_tca.propagate(-half_period)

        return (
            np.asarray(orbit_at_burn.r.to(u.km).value, dtype=float),
            np.asarray(orbit_at_burn.v.to(u.km / u.s).value, dtype=float),
            (tca - half_period).to_datetime(),
            float(half_period.to(u.s).value),
        )

    def _position_at_tca(
        self, event: ConjunctionEvent, delta_v_mps: np.ndarray
    ) -> np.ndarray:
        """Satellite position at TCA after applying ``delta_v_mps`` at the burn point."""
        position, velocity, burn_time, half_period_s = self._burn_state(event)
        boosted = velocity + np.asarray(delta_v_mps, dtype=float) / 1000.0

        orbit = Orbit.from_vectors(
            Earth,
            position * u.km,
            boosted * u.km / u.s,
            epoch=Time(burn_time, scale="utc"),
        )
        return np.asarray(
            orbit.propagate(half_period_s * u.s).r.to(u.km).value, dtype=float
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
        position, velocity, burn_time, half_period_s = self._burn_state(event)
        along_track = velocity / float(np.linalg.norm(velocity))
        object_state = np.asarray(event.object_state_vector, dtype=float)
        burn_epoch = Time(burn_time, scale="utc")

        def probability_at(magnitude_mps: float) -> float:
            boosted = velocity + along_track * (magnitude_mps / 1000.0)
            orbit = Orbit.from_vectors(
                Earth, position * u.km, boosted * u.km / u.s, epoch=burn_epoch
            )
            at_tca = orbit.propagate(half_period_s * u.s)
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
