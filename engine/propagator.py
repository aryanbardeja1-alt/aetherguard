"""SGP4 / Skyfield TLE propagation to TEME state vectors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from sgp4.api import Satrec, jday
from skyfield.api import EarthSatellite, load


class PropagationError(Exception):
    """Raised when TLE parsing or SGP4 propagation fails."""


@dataclass(frozen=True, slots=True)
class StateVector:
    """Inertial TEME state at a single epoch.

    Positions are in kilometres; velocities are in kilometres per second.
    """

    position_km: np.ndarray  # shape (3,)
    velocity_km_s: np.ndarray  # shape (3,)
    epoch: datetime

    @property
    def x(self) -> float:
        return float(self.position_km[0])

    @property
    def y(self) -> float:
        return float(self.position_km[1])

    @property
    def z(self) -> float:
        return float(self.position_km[2])

    @property
    def vx(self) -> float:
        return float(self.velocity_km_s[0])

    @property
    def vy(self) -> float:
        return float(self.velocity_km_s[1])

    @property
    def vz(self) -> float:
        return float(self.velocity_km_s[2])


def _normalize_tle_lines(line1: str, line2: str) -> tuple[str, str]:
    """Strip and validate basic TLE line structure."""
    l1 = line1.strip()
    l2 = line2.strip()
    if not l1.startswith("1 ") or len(l1) < 68:
        raise PropagationError(
            "Invalid TLE line 1: expected NORAD line starting with '1 '."
        )
    if not l2.startswith("2 ") or len(l2) < 68:
        raise PropagationError(
            "Invalid TLE line 2: expected NORAD line starting with '2 '."
        )
    return l1, l2


def _to_utc(dt: datetime) -> datetime:
    """Ensure a timezone-aware UTC datetime."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def propagate_tle(
    line1: str,
    line2: str,
    target_time: datetime,
    *,
    name: str = "UNKNOWN",
) -> StateVector:
    """Propagate a two-line element set to ``target_time`` in the TEME frame.

    Uses ``sgp4`` for the core TEME ECI state and ``skyfield`` to cross-check
    that the TLE is parseable as an EarthSatellite (WGS72 / SGP4).

    Parameters
    ----------
    line1, line2:
        NORAD two-line element strings.
    target_time:
        Propagation epoch (naive datetimes are treated as UTC).
    name:
        Optional catalog name used when constructing the Skyfield satellite.

    Returns
    -------
    StateVector
        TEME position (km) and velocity (km/s) at ``target_time``.

    Raises
    ------
    PropagationError
        If the TLE is malformed or SGP4 reports a non-zero error code.
    """
    l1, l2 = _normalize_tle_lines(line1, line2)
    epoch = _to_utc(target_time)

    # Skyfield parse gate — surfaces malformed TLEs early with a clear error.
    try:
        ts = load.timescale()
        _ = EarthSatellite(l1, l2, name, ts)
    except Exception as exc:  # noqa: BLE001 — Skyfield raises varied types
        raise PropagationError(f"Skyfield failed to parse TLE '{name}': {exc}") from exc

    satellite = Satrec.twoline2rv(l1, l2)
    jd, fr = jday(
        epoch.year,
        epoch.month,
        epoch.day,
        epoch.hour,
        epoch.minute,
        epoch.second + epoch.microsecond * 1e-6,
    )
    error_code, position, velocity = satellite.sgp4(jd, fr)

    if error_code != 0:
        raise PropagationError(
            f"SGP4 propagation failed for '{name}' at {epoch.isoformat()} "
            f"(error code {error_code})."
        )

    return StateVector(
        position_km=np.asarray(position, dtype=np.float64),
        velocity_km_s=np.asarray(velocity, dtype=np.float64),
        epoch=epoch,
    )


def relative_state(
    primary: StateVector,
    secondary: StateVector,
) -> tuple[np.ndarray, np.ndarray]:
    """Return relative position (km) and velocity (km/s): secondary − primary."""
    r_rel = secondary.position_km - primary.position_km
    v_rel = secondary.velocity_km_s - primary.velocity_km_s
    return r_rel, v_rel
