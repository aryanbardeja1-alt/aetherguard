"""Synthetic AetherGuard satellites for exercising the maneuver planner.

The live catalog contains no genuine close approaches. The only objects within
kilometres of each other are ISS modules sharing a single orbit, and those have
near-zero relative velocity, so they exercise neither the encounter-plane
geometry nor a realistic burn.

Each deployed satellite is derived from a real catalog object: same altitude and
epoch, orbit plane tilted, then phased so both reach the crossing point at the
same moment. That yields a true conjunction with real relative velocity, built
from a real TLE rather than invented state vectors.

Because the tilt preserves mean motion, both objects keep identical periods and
the encounter repeats once per revolution. Phasing is still solved against a
requested epoch, so redeploy if the demo sits idle for hours.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Sequence

import numpy as np
from scipy.optimize import minimize

from engine.propagator import PropagationError, propagate_tle

#: Plane tilt applied to each test satellite, degrees. Large enough for a
#: meaningful crossing velocity, small enough that the paths still nearly meet.
DEFAULT_PLANE_OFFSET_DEG: float = 5.0

#: Real objects the test satellites are aimed at, spanning LEO through deep HEO
#: so the planner is exercised across orbit regimes.
DEFAULT_TARGET_IDS: tuple[str, ...] = ("25544", "19548", "25867")

_MEAN_ANOMALY = slice(43, 51)
_INCLINATION = slice(8, 16)
_RAAN = slice(17, 25)


@dataclass(frozen=True, slots=True)
class TestbedPair:
    """One synthetic satellite and the real object it is set to approach."""

    satellite: dict[str, Any]
    target_id: str
    target_name: str
    tca: datetime
    miss_distance_km: float
    relative_speed_km_s: float


def tle_checksum(line: str) -> int:
    """NORAD checksum: digits summed mod 10, with each minus sign counting 1."""
    total = 0
    for char in line[:68]:
        if char.isdigit():
            total += int(char)
        elif char == "-":
            total += 1
    return total % 10


def _replace(line: str, span: slice, value: float) -> str:
    """Write an 8-wide fixed-point angle into a TLE field, keeping the width."""
    text = f"{value % 360.0:8.4f}"
    if len(text) != 8:
        raise ValueError(f"Formatted field {text!r} is not 8 characters wide.")
    return line[: span.start] + text + line[span.stop :]


def derive_line2(
    base_line2: str,
    *,
    inclination_deg: float,
    raan_deg: float,
    mean_anomaly_deg: float,
) -> str:
    """Rewrite a TLE line 2 with a new plane and phase, fixing the checksum."""
    line = base_line2.ljust(69)[:69]
    line = _replace(line, _INCLINATION, inclination_deg)
    line = _replace(line, _RAAN, raan_deg)
    line = _replace(line, _MEAN_ANOMALY, mean_anomaly_deg)
    body = line[:68]
    return body + str(tle_checksum(body))


def _read(line2: str, span: slice) -> float:
    return float(line2[span].strip())


def _period_minutes(line2: str) -> float:
    """Orbital period from the TLE mean motion (rev/day)."""
    mean_motion = float(line2[52:63].strip())
    return 1440.0 / mean_motion if mean_motion > 1e-6 else 92.0


def _minimise(fn, low: float, high: float, *, coarse: int, rounds: int = 6) -> float:
    """Coarse sweep then successive tightening — good enough, no derivatives."""
    grid = np.linspace(low, high, coarse)
    best = float(min(grid, key=fn))
    span = (high - low) / coarse
    for _ in range(rounds):
        best = float(min(np.linspace(best - span, best + span, 21), key=fn))
        span /= 8.0
    return best


def build_satellite(
    base_entry: dict[str, Any],
    search_from: datetime,
    *,
    name: str,
    sat_id: str,
    plane_offset_deg: float = DEFAULT_PLANE_OFFSET_DEG,
) -> TestbedPair:
    """Build a satellite that closely approaches ``base_entry``.

    Tilting the plane about the node line means the two orbits only ever
    intersect *at* that line, so the encounter time is not free: it is whenever
    the target next reaches its node. Solve for that first, then phase the new
    satellite to arrive at the same point. Phasing against an arbitrary epoch
    instead leaves a miss of up to ``2·a·sin(offset/2)`` — hundreds of km.
    """
    line1 = base_entry["line1"]
    line2 = base_entry["line2"]

    inclination = _read(line2, _INCLINATION) + plane_offset_deg
    raan = _read(line2, _RAAN)
    period_s = _period_minutes(line2) * 60.0

    # Both planes share the RAAN, so they cross along the ascending-node line.
    raan_rad = np.radians(raan)
    node_axis = np.array([np.cos(raan_rad), np.sin(raan_rad), 0.0])

    def target_at(offset_s: float):
        return propagate_tle(
            line1,
            line2,
            search_from + timedelta(seconds=float(offset_s)),
            validate_with_skyfield=False,
        )

    def node_misalignment(offset_s: float) -> float:
        position = target_at(offset_s).position_km
        norm = float(np.linalg.norm(position))
        if norm < 1e-9:
            return 1.0
        # Either node will do, hence the absolute value.
        return -abs(float(np.dot(position / norm, node_axis)))

    encounter_offset = _minimise(node_misalignment, 0.0, period_s, coarse=180)
    encounter_time = search_from + timedelta(seconds=encounter_offset)
    primary = target_at(encounter_offset)

    def candidate(mean_anomaly: float) -> str:
        return derive_line2(
            line2,
            inclination_deg=inclination,
            raan_deg=raan,
            mean_anomaly_deg=float(mean_anomaly),
        )

    def separation(mean_anomaly: float) -> float:
        try:
            state = propagate_tle(
                line1,
                candidate(mean_anomaly),
                encounter_time,
                validate_with_skyfield=False,
            )
        except (PropagationError, ValueError):
            return float("inf")
        return float(np.linalg.norm(state.position_km - primary.position_km))

    phase = _minimise(separation, 0.0, 360.0, coarse=720)

    # The node solution above assumes both orbits still share a node. They do
    # not: SGP4's J2 node regression goes as cos(i), and the tilt changed i, so
    # the planes drift apart by the TLE's age — 0.6 deg/day at ISS inclination.
    # Eccentric orbits amplify whatever angle remains, since the same error at
    # a 100,000 km apogee is tens of km. Solve jointly for the encounter time,
    # node, and phase that actually minimise the miss.
    def joint_miss(params: np.ndarray) -> float:
        offset_s, raan_delta, mean_anomaly = (float(x) for x in params)
        moment = encounter_time + timedelta(seconds=offset_s)
        try:
            primary_state = propagate_tle(
                line1, line2, moment, validate_with_skyfield=False
            )
            secondary_state = propagate_tle(
                line1,
                derive_line2(
                    line2,
                    inclination_deg=inclination,
                    raan_deg=raan + raan_delta,
                    mean_anomaly_deg=mean_anomaly,
                ),
                moment,
                validate_with_skyfield=False,
            )
        except (PropagationError, ValueError):
            return 1e12
        return float(
            np.linalg.norm(secondary_state.position_km - primary_state.position_km)
        )

    # Nelder-Mead's default simplex steps a zero-valued parameter by 0.00025.
    # For the time offset that is a quarter of a millisecond, which explores
    # nothing on a multi-hour orbit, so the search sits at its seed. Give each
    # parameter a step matched to its own scale instead.
    start = np.array([0.0, 0.0, phase])
    steps = np.array([period_s / 200.0, 0.5, 0.5])
    simplex = np.vstack([start, *(start + np.diag(steps))])

    solution = minimize(
        joint_miss,
        x0=start,
        method="Nelder-Mead",
        options={
            "initial_simplex": simplex,
            "xatol": 1e-8,
            "fatol": 1e-6,
            "maxiter": 6000,
            "maxfev": 6000,
        },
    )
    offset_s, raan_delta, phase = (float(x) for x in solution.x)
    encounter_time = encounter_time + timedelta(seconds=offset_s)
    raan = raan + raan_delta

    primary = propagate_tle(line1, line2, encounter_time, validate_with_skyfield=False)
    final_line2 = candidate(phase)
    state = propagate_tle(line1, final_line2, encounter_time, validate_with_skyfield=False)

    return TestbedPair(
        satellite={
            "id": sat_id,
            "name": name,
            "norad_id": 90000 + (int(sat_id.rsplit("-", 1)[-1]) if sat_id[-1].isdigit() else 0),
            "object_type": "debris",
            "line1": line1,
            "line2": final_line2,
        },
        target_id=str(base_entry["id"]),
        target_name=str(base_entry["name"]),
        tca=encounter_time,
        miss_distance_km=float(np.linalg.norm(state.position_km - primary.position_km)),
        relative_speed_km_s=float(
            np.linalg.norm(state.velocity_km_s - primary.velocity_km_s)
        ),
    )


def deploy(
    targets: Sequence[dict[str, Any]],
    target_time: datetime,
    *,
    plane_offset_deg: float = DEFAULT_PLANE_OFFSET_DEG,
) -> list[TestbedPair]:
    """Build one AETHERGUARD satellite per target object."""
    pairs: list[TestbedPair] = []
    for index, base in enumerate(targets, start=1):
        pairs.append(
            build_satellite(
                base,
                target_time,
                name=f"AETHERGUARD {index}",
                sat_id=f"AETHERGUARD-{index}",
                plane_offset_deg=plane_offset_deg,
            )
        )
    return pairs
