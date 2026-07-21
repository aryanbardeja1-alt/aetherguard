# AetherGuard - Claude Code Instructions

## WHAT & WHY: Project Overview
AetherGuard is an onboard framework for satellite collision avoidance and self-healing mesh networks. It merges ground-based conjunction data with onboard trajectory planning to generate evasive maneuvers without breaking optical mesh links.

## Architecture & Boundaries
- **API/Probability Engine:** Ingests Conjunction Data Messages (CDMs) and outputs a `ConjunctionEvent`.
- **Trajectory Engine (`trajectory_engine.py`):** Takes a `ConjunctionEvent` and calculates an optimal evasion burn, outputting a `ManeuverPlan`.

## HOW: Core Commands
- **Environment**: Python 3.10+
- **Install**: `pip install -r requirements.txt`
- **Test**: `pytest tests/` (Always run the test suite before proposing a commit)
- **Git**: Commit and push directly to `main`. Do not open feature branches. Because there is no review gate, `pytest tests/` must pass before every push.

## Repository Map
- `main.py`: FastAPI application entry point.
- `aether_core.py`: The critical shared data contract.
- `api/routes.py`: HTTP route handlers.
- `engine/`: Physics and astrodynamics.
  - `collision.py`: Encounter-plane probability of collision (Foster–Chan / Patera).
  - `frames.py`: RTN/RIC ↔ TEME covariance transforms.
  - `propagator.py`: SGP4 / Skyfield TLE propagation to state vectors.
  - `trajectory.py`: `TrajectoryOptimizer` for maneuver planning and self-healing evaluation.
- `schemas/conjunction.py`: Pydantic request/response models.
- `tests/`: Pytest unit testing suite.

## File Placement
Every new file goes in the directory matching its use case. Never add source
files to the repository root — `main.py` and `aether_core.py` are the only two
that belong there.

| Use case | Location |
| --- | --- |
| HTTP endpoint, request routing | `api/` |
| Orbital mechanics, probability, frame maths | `engine/` |
| Pydantic wire models for the API | `schemas/` |
| Tests, named `test_<module>.py` | `tests/` |

Before creating a file, check whether an existing module in the target
directory already covers the concern and extend it instead. When a file moves,
update its importers and the Repository Map above in the same commit.

## Strict Rules & Guardrails
- **IMMUTABLE CONTRACT**: You are strictly forbidden from modifying `aether_core.py` without explicit user permission. The `ConjunctionEvent` and `ManeuverPlan` dataclasses must remain exactly as defined to ensure cross-module compatibility with external APIs.
- **Physics & Math**: Rely entirely on `poliastro` and `astropy` for orbital mechanics and reference frame transformations. Do not attempt to hand-write standard astrodynamics equations if a library function exists.
- **Data Processing**: Utilize `numpy` for all state vector arrays and matrix manipulations. Ensure strict type hinting (e.g., `np.ndarray`) is maintained for all physics calculations.
