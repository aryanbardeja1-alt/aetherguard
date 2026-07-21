"""2D encounter-plane (B-plane) Probability of Collision (Foster–Chan / Patera)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.integrate import dblquad
from scipy.linalg import inv


class CovarianceError(Exception):
    """Raised when a projected covariance is singular or ill-conditioned."""


@dataclass(frozen=True, slots=True)
class CollisionAssessment:
    """Result of a Foster–Chan / Patera 2D PoC evaluation."""

    poc: float
    dca_km: float
    miss_vector_km: np.ndarray  # 2-D B-plane miss (ξ, ζ)
    c2d_km2: np.ndarray  # 2×2 projected combined covariance


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

    # Pick a reference axis least aligned with the plane normal.
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
    c2d = rotation @ combined @ rotation.T
    return c2d


def _gaussian_pdf_2d(
    x: float,
    y: float,
    mean: np.ndarray,
    inv_c: np.ndarray,
    norm_const: float,
) -> float:
    """Evaluate the 2-D Gaussian PDF at (x, y)."""
    delta = np.array([x - mean[0], y - mean[1]], dtype=np.float64)
    exponent = -0.5 * float(delta @ inv_c @ delta)
    return norm_const * float(np.exp(exponent))


def integrate_poc_disk(
    miss_km: np.ndarray,
    c2d_km2: np.ndarray,
    hbr_km: float,
) -> float:
    """Numerically integrate the 2-D Gaussian over a hard-body disk (Patera).

    Uses ``scipy.integrate.dblquad`` over the circular domain of radius
    ``hbr_km`` centred on the origin of the encounter plane (primary hard-body
    frame), with the miss vector locating the secondary mean.
    """
    if hbr_km <= 0.0:
        return 0.0

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

    norm_const = 1.0 / (2.0 * np.pi * np.sqrt(det))
    mean = np.asarray(miss_km, dtype=np.float64).reshape(2)

    def integrand(y: float, x: float) -> float:
        # dblquad calls g(y, x) with outer variable x.
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
    # Numerical noise can produce tiny negatives.
    return float(max(0.0, min(1.0, poc)))


def assess_collision(
    r_rel_km: np.ndarray,
    v_rel_km_s: np.ndarray,
    p1_diag_km2: tuple[float, float, float] | list[float] | np.ndarray,
    p2_diag_km2: tuple[float, float, float] | list[float] | np.ndarray,
    hbr_meters: float,
) -> CollisionAssessment:
    """Compute DCA and Probability of Collision in the 2-D encounter plane.

    Parameters
    ----------
    r_rel_km:
        Relative position vector (km), secondary − primary, at the assessment
        epoch (ideally TCA).
    v_rel_km_s:
        Relative velocity (km/s), secondary − primary.
    p1_diag_km2, p2_diag_km2:
        Diagonal elements of the 3×3 position covariance matrices (km²).
        Off-diagonal terms are taken as zero (uncorrelated TEME axes).
    hbr_meters:
        Combined hard-body radius in metres.

    Returns
    -------
    CollisionAssessment
        PoC, DCA (km), B-plane miss vector, and projected covariance.
    """
    r_rel = np.asarray(r_rel_km, dtype=np.float64).reshape(3)
    v_rel = np.asarray(v_rel_km_s, dtype=np.float64).reshape(3)

    p1 = np.diag(np.asarray(p1_diag_km2, dtype=np.float64).reshape(3))
    p2 = np.diag(np.asarray(p2_diag_km2, dtype=np.float64).reshape(3))

    rotation = build_encounter_rotation(v_rel, fallback_axis=r_rel)
    c2d = project_covariance_2d(p1, p2, rotation)

    # B-plane miss vector (projected relative position).
    miss = rotation @ r_rel
    # Geometric range at the supplied epoch; at true TCA this equals the
    # encounter-plane miss distance.
    dca_km = float(np.linalg.norm(r_rel))

    hbr_km = float(hbr_meters) / 1000.0
    poc = integrate_poc_disk(miss, c2d, hbr_km)

    return CollisionAssessment(
        poc=poc,
        dca_km=dca_km,
        miss_vector_km=miss,
        c2d_km2=c2d,
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
