from dataclasses import dataclass
import numpy as np
from datetime import datetime
from typing import Optional, List

@dataclass
class ConjunctionEvent:
    event_id: str
    satellite_id: str
    sat_state_vector: np.ndarray     # [x, y, z, vx, vy, vz] in km and km/s (ECI frame)
    object_state_vector: np.ndarray  # [x, y, z, vx, vy, vz] of the debris/secondary object
    tca: datetime                    # Time of Closest Approach
    probability_of_collision: float
    mode: str                        # "INDEPENDENT" or "CONSTELLATION"
    mesh_neighbors: Optional[List[str]] = None

@dataclass
class ManeuverPlan:
    event_id: str
    satellite_id: str
    delta_v: np.ndarray              # [dv_x, dv_y, dv_z] in m/s
    burn_time: datetime
    new_probability: float
    requires_mesh_rerouting: bool    # True if self-healing needs to trigger
