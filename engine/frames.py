"""Orbital frame utilities — RTN/RIC ↔ TEME covariance transforms."""

from __future__ import annotations

import numpy as np


class FrameError(Exception):
    """Raised when an RTN/TEME basis cannot be constructed."""


def rtn_basis(position_km: np.ndarray, velocity_km_s: np.ndarray) -> np.ndarray:
    """Build the 3×3 DCM whose columns are R̂, T̂, N̂ in the TEME frame.

    RTN (Radial–Transverse–Normal), also called RIC:

    * **R** — unit radial (along position)
    * **N** — unit orbit normal (``r × v``)
    * **T** — unit transverse / in-track (``N × R``)

    Mapping: ``r_TEME = Q @ r_RTN`` and ``P_TEME = Q @ P_RTN @ Q.T``.
    """
    r = np.asarray(position_km, dtype=np.float64).reshape(3)
    v = np.asarray(velocity_km_s, dtype=np.float64).reshape(3)

    r_norm = float(np.linalg.norm(r))
    if r_norm < 1e-12:
        raise FrameError("Position vector is near zero; RTN radial axis undefined.")

    r_hat = r / r_norm
    h = np.cross(r, v)
    h_norm = float(np.linalg.norm(h))
    if h_norm < 1e-12:
        raise FrameError("Angular momentum is near zero; RTN normal axis undefined.")

    n_hat = h / h_norm
    t_hat = np.cross(n_hat, r_hat)
    t_hat /= float(np.linalg.norm(t_hat))

    # Columns: R, T, N expressed in TEME.
    return np.column_stack([r_hat, t_hat, n_hat])


def rotate_covariance_rtn_to_teme(
    p_rtn_km2: np.ndarray,
    position_km: np.ndarray,
    velocity_km_s: np.ndarray,
) -> np.ndarray:
    """Rotate a 3×3 position covariance from RTN into TEME."""
    q = rtn_basis(position_km, velocity_km_s)
    p = np.asarray(p_rtn_km2, dtype=np.float64).reshape(3, 3)
    return q @ p @ q.T


def rotate_covariance_teme_to_rtn(
    p_teme_km2: np.ndarray,
    position_km: np.ndarray,
    velocity_km_s: np.ndarray,
) -> np.ndarray:
    """Rotate a 3×3 position covariance from TEME into RTN."""
    q = rtn_basis(position_km, velocity_km_s)
    p = np.asarray(p_teme_km2, dtype=np.float64).reshape(3, 3)
    return q.T @ p @ q
