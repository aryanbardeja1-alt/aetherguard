"""Adaptive trajectory sampling for smooth orbit rendering.

Uniform time steps are the obvious way to sample an orbit and the wrong one for
eccentric ones. A satellite sweeps most of its arc near perigee, so evenly
spaced times leave that stretch barely sampled: Cluster II-FM8 at e=0.91 turns
92 degrees between consecutive samples, which draws as a straight chord slicing
across the perigee pass, while apogee gets samples it does not need.

Refining by *turned angle* puts samples where the path actually curves, and
works against any propagator since it only needs positions back.
"""

from __future__ import annotations

from typing import Callable, List, Sequence, Tuple, TypeVar

import numpy as np

#: No drawn segment should turn more than this, in degrees.
DEFAULT_MAX_TURN_DEG: float = 3.0

T = TypeVar("T", bound=float)


def _turn_degrees(a: np.ndarray, b: np.ndarray) -> float:
    """Angle subtended at the focus by two consecutive positions."""
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    cosine = float(np.dot(a, b) / (na * nb))
    return float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))


def sample_by_turn_angle(
    evaluate: Callable[[float], np.ndarray],
    start: float,
    end: float,
    *,
    initial: int = 48,
    max_points: int = 400,
    max_turn_deg: float = DEFAULT_MAX_TURN_DEG,
) -> Tuple[List[float], List[np.ndarray]]:
    """Sample ``evaluate`` between ``start`` and ``end``, splitting sharp turns.

    Begins with a uniform grid, then repeatedly bisects whichever segment turns
    the most until every segment is under ``max_turn_deg`` or ``max_points`` is
    reached. Returns the parameter values and their positions.
    """
    count = max(4, initial)
    times: List[float] = [
        start + (end - start) * index / (count - 1) for index in range(count)
    ]
    points: List[np.ndarray] = [evaluate(t) for t in times]
    angles: List[float] = [
        _turn_degrees(points[i], points[i + 1]) for i in range(len(points) - 1)
    ]

    while len(points) < max_points and angles:
        worst = max(range(len(angles)), key=angles.__getitem__)
        if angles[worst] <= max_turn_deg:
            break

        midpoint = 0.5 * (times[worst] + times[worst + 1])
        position = evaluate(midpoint)

        times.insert(worst + 1, midpoint)
        points.insert(worst + 1, position)
        angles[worst] = _turn_degrees(points[worst], position)
        angles.insert(worst + 1, _turn_degrees(position, points[worst + 2]))

    return times, points


def worst_turn_degrees(positions: Sequence[np.ndarray]) -> float:
    """Largest angle between consecutive positions — how kinked a path looks."""
    if len(positions) < 2:
        return 0.0
    return max(
        _turn_degrees(positions[i], positions[i + 1])
        for i in range(len(positions) - 1)
    )
