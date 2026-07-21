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
- **Git**: Always create and work on `feature/` branches. Never push directly to the `main` branch.

## Repository Map
- `aether_core.py`: The critical shared data contract.
- `trajectory_engine.py`: Contains the `TrajectoryOptimizer` for maneuver planning and self-healing evaluation.
- `tests/`: Pytest unit testing suite.

## Strict Rules & Guardrails
- **IMMUTABLE CONTRACT**: You are strictly forbidden from modifying `aether_core.py` without explicit user permission. The `ConjunctionEvent` and `ManeuverPlan` dataclasses must remain exactly as defined to ensure cross-module compatibility with external APIs.
- **Physics & Math**: Rely entirely on `poliastro` and `astropy` for orbital mechanics and reference frame transformations. Do not attempt to hand-write standard astrodynamics equations if a library function exists.
- **Data Processing**: Utilize `numpy` for all state vector arrays and matrix manipulations. Ensure strict type hinting (e.g., `np.ndarray`) is maintained for all physics calculations.
