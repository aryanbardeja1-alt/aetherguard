"""Guards on the probability-of-collision calculator.

These pin the failure modes that made PoC saturate to 1.0 (or, on the dblquad
path, collapse to 0.0) instead of failing loudly: degenerate covariances and
out-of-band hard-body radii.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.collision import (
    MAX_HBR_KM,
    MIN_SIGMA_KM,
    CovarianceError,
    assess_collision,
    chan_poc,
    integrate_poc_disk,
)

# 300 m isotropic 1-sigma, 30 m miss, 20 m combined hard body.
NOMINAL_C2D = np.diag([0.09, 0.09])
NOMINAL_MISS = np.array([0.03, 0.0])
NOMINAL_HBR_KM = 0.02


class TestNominalAccuracy:
    def test_known_analytic_value(self) -> None:
        """Small-HBR limit: Pc ~ (R^2 / 2*sigma^2) * exp(-d^2 / 2*sigma^2) = 2.21e-3."""
        assert chan_poc(NOMINAL_MISS, NOMINAL_C2D, NOMINAL_HBR_KM) == pytest.approx(
            2.2e-3, rel=0.02
        )

    def test_chan_still_agrees_with_dblquad(self) -> None:
        """The guards must not perturb the cross-check on valid input."""
        chan = chan_poc(NOMINAL_MISS, NOMINAL_C2D, NOMINAL_HBR_KM)
        quad = integrate_poc_disk(NOMINAL_MISS, NOMINAL_C2D, NOMINAL_HBR_KM)
        assert chan == pytest.approx(quad, rel=1e-6)


class TestDegenerateCovariance:
    """A vanishing covariance is missing knowledge, never certain knowledge."""

    @pytest.mark.parametrize(
        ("label", "c2d"),
        [
            ("zero", np.zeros((2, 2))),
            ("tiny but positive-definite", np.diag([1e-20, 1e-20])),
            ("one centimetre sigma", np.diag([1e-10, 1e-10])),
            ("singular in one axis", np.array([[1.0, 0.0], [0.0, 0.0]])),
        ],
    )
    def test_raises_rather_than_saturating(self, label: str, c2d: np.ndarray) -> None:
        with pytest.raises(CovarianceError):
            chan_poc(NOMINAL_MISS, c2d, NOMINAL_HBR_KM)

    @pytest.mark.parametrize(
        ("label", "c2d"),
        [
            ("zero", np.zeros((2, 2))),
            ("tiny but positive-definite", np.diag([1e-20, 1e-20])),
        ],
    )
    def test_dblquad_path_raises_too(self, label: str, c2d: np.ndarray) -> None:
        """dblquad silently returned 0.0 here, which reads as 'safe'."""
        with pytest.raises(CovarianceError):
            integrate_poc_disk(NOMINAL_MISS, c2d, NOMINAL_HBR_KM)

    def test_sigma_just_above_floor_is_accepted(self) -> None:
        sigma = MIN_SIGMA_KM * 1.01
        value = chan_poc(np.array([0.0, 0.0]), np.diag([sigma**2, sigma**2]), 1e-3)
        assert 0.0 <= value <= 1.0

    def test_error_names_the_floor(self) -> None:
        with pytest.raises(CovarianceError, match="physical floor"):
            chan_poc(NOMINAL_MISS, np.diag([1e-20, 1e-20]), NOMINAL_HBR_KM)


class TestHardBodyRadiusBand:
    def test_metres_mistaken_for_kilometres_raises(self) -> None:
        """hbr_meters=10000 -> 10 km, far outside any real combined radius."""
        with pytest.raises(CovarianceError, match="outside the physical band"):
            assess_collision(
                r_rel_km=np.array([0.03, 0.0, 0.0]),
                v_rel_km_s=np.array([0.0, 15.0, 0.0]),
                p1_diag_km2=(0.045, 0.045, 0.045),
                p2_diag_km2=(0.045, 0.045, 0.045),
                hbr_meters=10000.0,
            )

    @pytest.mark.parametrize("hbr_km", [0.0, -1.0, 1e-9, 1.0, float("nan")])
    def test_out_of_band_values_raise(self, hbr_km: float) -> None:
        with pytest.raises(CovarianceError):
            chan_poc(NOMINAL_MISS, NOMINAL_C2D, hbr_km)

    def test_error_mentions_units(self) -> None:
        with pytest.raises(CovarianceError, match="metres"):
            chan_poc(NOMINAL_MISS, NOMINAL_C2D, 50.0)

    def test_nominal_twenty_metres_is_in_band(self) -> None:
        assert chan_poc(NOMINAL_MISS, NOMINAL_C2D, NOMINAL_HBR_KM) > 0.0


class TestOverflowShortCircuit:
    def test_saturating_geometry_returns_one_not_nan(self) -> None:
        """Largest legal hard body against the tightest legal sigma: u = 1e4."""
        c2d = np.diag([MIN_SIGMA_KM**2, MIN_SIGMA_KM**2])
        value = chan_poc(np.array([0.0, 0.0]), c2d, MAX_HBR_KM)

        assert np.isfinite(value)
        assert value == pytest.approx(1.0)

    def test_saturating_geometry_agrees_with_dblquad(self) -> None:
        """The short-circuit must return what the integral would have."""
        c2d = np.diag([MIN_SIGMA_KM**2, MIN_SIGMA_KM**2])
        miss = np.array([0.0, 0.0])
        assert chan_poc(miss, c2d, MAX_HBR_KM) == pytest.approx(
            integrate_poc_disk(miss, c2d, MAX_HBR_KM), abs=1e-9
        )
