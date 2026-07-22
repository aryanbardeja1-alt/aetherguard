"""Geodetic helpers for globe visualization (TEME → lat/lon/alt)."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

# WGS‑84 spherical Earth radius (km) — adequate for marker placement.
EARTH_RADIUS_KM = 6378.137


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def gmst_radians(epoch: datetime) -> float:
    """Greenwich mean sidereal time (rad) via Vallado approximation."""
    epoch = _to_utc(epoch)
    # Julian date
    y, m = epoch.year, epoch.month
    d = (
        epoch.day
        + (epoch.hour + (epoch.minute + (epoch.second + epoch.microsecond * 1e-6) / 60.0) / 60.0)
        / 24.0
    )
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    jd = int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5

    t_ut1 = (jd - 2451545.0) / 36525.0
    gmst_sec = (
        67310.54841
        + (876600.0 * 3600.0 + 8640184.812866) * t_ut1
        + 0.093104 * t_ut1**2
        - 6.2e-6 * t_ut1**3
    )
    gmst_deg = (gmst_sec / 240.0) % 360.0  # 240 s = 1 deg of Earth rotation
    return float(np.deg2rad(gmst_deg))


def teme_to_ecef(
    position_km: np.ndarray,
    epoch: datetime,
    *,
    gmst: float | None = None,
) -> np.ndarray:
    """Rotate TEME → ECEF/PEF about Z using GMST (or a frozen GMST value)."""
    r = np.asarray(position_km, dtype=np.float64).reshape(3)
    theta = gmst_radians(epoch) if gmst is None else float(gmst)
    c, s = np.cos(theta), np.sin(theta)
    return np.array(
        [c * r[0] + s * r[1], -s * r[0] + c * r[1], r[2]],
        dtype=np.float64,
    )


def teme_to_latlon_alt(
    position_km: np.ndarray,
    epoch: datetime,
) -> tuple[float, float, float]:
    """Convert TEME position (km) to geocentric latitude, longitude, altitude.

    Returns
    -------
    lat_deg, lon_deg, alt_km
    """
    ecef = teme_to_ecef(position_km, epoch)
    x, y, z = ecef
    lon = float(np.rad2deg(np.atan2(y, x)))
    lat = float(np.rad2deg(np.atan2(z, np.hypot(x, y))))
    alt = float(np.linalg.norm(ecef) - EARTH_RADIUS_KM)
    return lat, lon, alt


def latlon_to_ecef(lat_deg: float, lon_deg: float, alt_km: float = 0.0) -> np.ndarray:
    """Geocentric lat/lon/alt → ECEF kilometres (spherical Earth)."""
    lat = np.deg2rad(lat_deg)
    lon = np.deg2rad(lon_deg)
    radius = EARTH_RADIUS_KM + alt_km
    return np.array(
        [
            radius * np.cos(lat) * np.cos(lon),
            radius * np.cos(lat) * np.sin(lon),
            radius * np.sin(lat),
        ],
        dtype=np.float64,
    )
