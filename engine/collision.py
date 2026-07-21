"""2D encounter-plane Probability of Collision (Foster–Chan / Patera / Chan series)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy.integrate import dblquad
from scipy.linalg import inv

from engine.frames import FrameError, rotate_covariance_rtn_to_teme

CovarianceFrame = Literal["TEME", "RTN"]


class CovarianceError(Exception):
    """Raised when a projected covariance is singular or ill-conditioned."""


@dataclass(frozen=True, slots=True)
class CollisionAssessment:
    """Result of a 2-D encounter-plane PoC evaluation."""

    poc: float
    dca_km: float
    miss_vector_km: np.ndarray  # 2-D B-plane miss (ξ, ζ)
    c2d_km2: np.ndarray  # 2×2 projected combined covariance
    method: str  # "chan" or "dblquad"


def build_encounter_rotation(
    v_rel_km_s: np.ndarray,
    *,
    fallback_axis: np.ndarray | None = None,
) -> np.ndarray:
    """Build the 2×3 rotation ``R`` that maps TEME → B-plane coordinates.

    The encounter (B) plane is orthogonal to the relative-velocity unit vector.
    Rows of ``R`` are an orthonormal basis spanning that plane
    (Foster / Chan construction).

    When ``||v_rel||`` is vanishingly small (co-orbital / identical-state
    degeneracy), ``fallback_axis`` — or the inertial z-axis — is used so the
    hard-body integral remains well-defined.
    """
    v_rel = np.asarray(v_rel_km_s, dtype=np.float64).reshape(3)
    speed = float(np.linalg.norm(v_rel))

    if speed < 1e-12:
        if fallback_axis is not None:
            axis = np.asarray(fallback_axis, dtype=np.float64).reshape(3)
            axis_norm = float(np.linalg.norm(axis))
            if axis_norm >= 1e-12:
                v_hat = axis / axis_norm
            else:
                v_hat = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        else:
            v_hat = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    else:
        v_hat = v_rel / speed

    ref = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    if abs(float(np.dot(v_hat, ref))) > 0.9:
        ref = np.array([1.0, 0.0, 0.0], dtype=np.float64)

    xi_hat = np.cross(v_hat, ref)
    xi_norm = float(np.linalg.norm(xi_hat))
    if xi_norm < 1e-12:
        raise CovarianceError("Failed to construct encounter-plane ξ-axis.")
    xi_hat /= xi_norm

    zeta_hat = np.cross(v_hat, xi_hat)
    zeta_hat /= float(np.linalg.norm(zeta_hat))

    return np.vstack([xi_hat, zeta_hat])


def project_covariance_2d(
    p1_km2: np.ndarray,
    p2_km2: np.ndarray,
    rotation: np.ndarray,
) -> np.ndarray:
    """Combine 3×3 position covariances and project into the B-plane.

    ``C_2D = R (P1 + P2) Rᵀ``
    """
    combined = np.asarray(p1_km2, dtype=np.float64) + np.asarray(p2_km2, dtype=np.float64)
    if combined.shape != (3, 3):
        raise CovarianceError(f"Expected 3×3 covariances, got {combined.shape}.")
    return rotation @ combined @ rotation.T


def _validate_c2d(c2d_km2: np.ndarray) -> tuple[float, np.ndarray]:
    """Return (det, inv(C)) after positive-definite / conditioning checks."""
    det = float(np.linalg.det(c2d_km2))
    if det <= 0.0 or not np.isfinite(det):
        raise CovarianceError(
            "Projected 2-D covariance is singular or non-positive-definite "
            f"(det={det})."
        )

    cond = float(np.linalg.cond(c2d_km2))
    if cond > 1e12:
        raise CovarianceError(
            f"Projected 2-D covariance is ill-conditioned (cond={cond:.2e})."
        )

    try:
        inv_c = inv(c2d_km2)
    except np.linalg.LinAlgError as exc:
        raise CovarianceError("Failed to invert projected 2-D covariance.") from exc
    return det, inv_c


def _gaussian_pdf_2d(
    x: float,
    y: float,
    mean: np.ndarray,
    inv_c: np.ndarray,
    norm_const: float,
) -> float:
    delta = np.array([x - mean[0], y - mean[1]], dtype=np.float64)
    exponent = -0.5 * float(delta @ inv_c @ delta)
    return norm_const * float(np.exp(exponent))


def integrate_poc_disk(
    miss_km: np.ndarray,
    c2d_km2: np.ndarray,
    hbr_km: float,
) -> float:
    """Numerically integrate the 2-D Gaussian over a hard-body disk (Patera / dblquad).

    Retained for cross-checks against Chan's analytical series.
    """
    if hbr_km <= 0.0:
        return 0.0

    det, inv_c = _validate_c2d(c2d_km2)
    norm_const = 1.0 / (2.0 * np.pi * np.sqrt(det))
    mean = np.asarray(miss_km, dtype=np.float64).reshape(2)

    def integrand(y: float, x: float) -> float:
        return _gaussian_pdf_2d(x, y, mean, inv_c, norm_const)

    def y_lower(x: float) -> float:
        return -np.sqrt(max(hbr_km**2 - x**2, 0.0))

    def y_upper(x: float) -> float:
        return np.sqrt(max(hbr_km**2 - x**2, 0.0))

    poc, _err = dblquad(
        integrand,
        -hbr_km,
        hbr_km,
        y_lower,
        y_upper,
        epsabs=1e-10,
        epsrel=1e-6,
    )
    return float(max(0.0, min(1.0, poc)))


def chan_poc(
    miss_km: np.ndarray,
    c2d_km2: np.ndarray,
    hbr_km: float,
    *,
    max_terms: int = 64,
    tol: float = 1e-15,
) -> float:
    """Chan analytical series for circular hard-body PoC (AAS / industry form).

    After rotating the miss vector into the principal axes of ``C_2D`` with
    eigenvalues ``σ_x², σ_y²``:

    .. math::

        u = R^2 / (σ_x σ_y),\\qquad
        v = (x_m/σ_x)^2 + (y_m/σ_y)^2

        P_c = e^{-v/2} \\sum_{m=0}^{∞}
            \\frac{(v/2)^m}{m!}
            \\left(1 - e^{-u/2}\\sum_{k=0}^{m}\\frac{(u/2)^k}{k!}\\right)

    Converges in a handful of terms for typical conjunction geometries and is
    orders of magnitude faster than ``dblquad``.
    """
    if hbr_km <= 0.0:
        return 0.0

    _validate_c2d(c2d_km2)

    eigvals, eigvecs = np.linalg.eigh(c2d_km2)
    if np.any(eigvals <= 0.0):
        raise CovarianceError("Projected covariance has non-positive eigenvalues.")

    sigma = np.sqrt(eigvals)
    miss_p = eigvecs.T @ np.asarray(miss_km, dtype=np.float64).reshape(2)

    u = (hbr_km * hbr_km) / (sigma[0] * sigma[1])
    v = (miss_p[0] / sigma[0]) ** 2 + (miss_p[1] / sigma[1]) ** 2

    # Degenerate / extreme guards.
    if not np.isfinite(u) or not np.isfinite(v):
        raise CovarianceError("Chan parameters u, v are non-finite.")

    exp_neg_half_v = float(np.exp(-0.5 * v))
    exp_neg_half_u = float(np.exp(-0.5 * u))

    poc = 0.0
    # Recurrence: term_m = (v/2)^m / m!
    half_v_pow = 1.0
    half_u_terms = [1.0]  # (u/2)^k / k! for k = 0..m cumulative list

    for m in range(max_terms):
        if m > 0:
            half_v_pow *= (0.5 * v) / m
            half_u_terms.append(half_u_terms[-1] * (0.5 * u) / m)

        inner = sum(half_u_terms)  # Σ_{k=0}^{m} (u/2)^k / k!
        bracket = 1.0 - exp_neg_half_u * inner
        term = half_v_pow * bracket
        poc += term

        if m > 2 and abs(term) < tol * max(1.0, abs(poc)):
            break

    return float(max(0.0, min(1.0, exp_neg_half_v * poc)))


def coerce_covariance_3x3(
    value: np.ndarray | list[float] | list[list[float]] | tuple[float, ...],
) -> np.ndarray:
    """Accept a 3-vector diagonal, flat 9-vector, or 3×3 matrix → 3×3 array."""
    arr = np.asarray(value, dtype=np.float64)
    if arr.shape == (3,):
        return np.diag(arr)
    if arr.shape == (9,):
        return arr.reshape(3, 3)
    if arr.shape == (3, 3):
        return arr.copy()
    raise CovarianceError(
        f"Covariance must be length-3 diag, length-9 flat, or 3×3; got {arr.shape}."
    )


def _to_teme_covariance(
    p_km2: np.ndarray,
    frame: CovarianceFrame,
    position_km: np.ndarray | None,
    velocity_km_s: np.ndarray | None,
    label: str,
) -> np.ndarray:
    if frame == "TEME":
        return p_km2
    if position_km is None or velocity_km_s is None:
        raise CovarianceError(
            f"{label}: RTN covariance requires the object's TEME position and velocity."
        )
    try:
        return rotate_covariance_rtn_to_teme(p_km2, position_km, velocity_km_s)
    except FrameError as exc:
        raise CovarianceError(str(exc)) from exc


def assess_collision(
    r_rel_km: np.ndarray,
    v_rel_km_s: np.ndarray,
    p1_km2: np.ndarray | list[float] | list[list[float]] | None = None,
    p2_km2: np.ndarray | list[float] | list[list[float]] | None = None,
    hbr_meters: float = 10.0,
    *,
    p1_diag_km2: tuple[float, float, float] | list[float] | np.ndarray | None = None,
    p2_diag_km2: tuple[float, float, float] | list[float] | np.ndarray | None = None,
    covariance_frame: CovarianceFrame = "TEME",
    primary_position_km: np.ndarray | None = None,
    primary_velocity_km_s: np.ndarray | None = None,
    secondary_position_km: np.ndarray | None = None,
    secondary_velocity_km_s: np.ndarray | None = None,
    method: Literal["chan", "dblquad"] = "chan",
) -> CollisionAssessment:
    """Compute DCA and Probability of Collision in the 2-D encounter plane.

    Covariances may be supplied as full 3×3 matrices (``p1_km2`` / ``p2_km2``)
    or as TEME diagonals (``p1_diag_km2`` / ``p2_diag_km2``). When
    ``covariance_frame="RTN"``, each matrix is rotated into TEME using that
    object's state before combination and B-plane projection.

    Default PoC evaluator is Chan's analytical series; pass ``method="dblquad"``
    for the classic double-integral cross-check.
    """
    r_rel = np.asarray(r_rel_km, dtype=np.float64).reshape(3)
    v_rel = np.asarray(v_rel_km_s, dtype=np.float64).reshape(3)

    if p1_km2 is None:
        if p1_diag_km2 is None:
            raise CovarianceError("Provide p1_km2 or p1_diag_km2.")
        p1_km2 = p1_diag_km2
    if p2_km2 is None:
        if p2_diag_km2 is None:
            raise CovarianceError("Provide p2_km2 or p2_diag_km2.")
        p2_km2 = p2_diag_km2

    p1 = coerce_covariance_3x3(p1_km2)
    p2 = coerce_covariance_3x3(p2_km2)

    p1_teme = _to_teme_covariance(
        p1, covariance_frame, primary_position_km, primary_velocity_km_s, "P1"
    )
    p2_teme = _to_teme_covariance(
        p2, covariance_frame, secondary_position_km, secondary_velocity_km_s, "P2"
    )

    # Symmetrise to kill tiny numeric asymmetry from rotations.
    p1_teme = 0.5 * (p1_teme + p1_teme.T)
    p2_teme = 0.5 * (p2_teme + p2_teme.T)

    rotation = build_encounter_rotation(v_rel, fallback_axis=r_rel)
    c2d = project_covariance_2d(p1_teme, p2_teme, rotation)
    miss = rotation @ r_rel
    dca_km = float(np.linalg.norm(r_rel))
    hbr_km = float(hbr_meters) / 1000.0

    if method == "chan":
        poc = chan_poc(miss, c2d, hbr_km)
    elif method == "dblquad":
        poc = integrate_poc_disk(miss, c2d, hbr_km)
    else:
        raise CovarianceError(f"Unknown PoC method '{method}'.")

    return CollisionAssessment(
        poc=poc,
        dca_km=dca_km,
        miss_vector_km=miss,
        c2d_km2=c2d,
        method=method,
    )


def classify_risk(poc: float) -> str:
    """Map Pc onto operational risk bands used by AetherGuard."""
    if poc >= 1e-2:
        return "CRITICAL"
    if poc >= 1e-3:
        return "HIGH"
    if poc >= 1e-4:
        return "MEDIUM"
    return "LOW"
