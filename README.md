# AetherGuard

Autonomous orbital safety platform for **conjunction assessment**, **evasive maneuver planning**, and **self-healing mesh routing** — with a live 3D globe UI.

AetherGuard propagates TLEs with SGP4, evaluates probability of collision (Pc) in the encounter (B) plane, plans avoidance burns, and visualizes catalog traffic so operators can pick a pair and act.

---

## Features

### 1. TLE orbit propagation
- Parse NORAD 2-line elements for primary assets and secondary / debris objects
- Propagate to any UTC epoch with **SGP4** (Skyfield used as an optional parse gate)
- Returns TEME position (km) and velocity (km/s)

### 2. Probability of collision (Pc)
- 2D **B-plane / encounter-plane** projection (Foster–Chan / Patera family)
- Combines covariances and integrates over the hard-body radius (HBR)
- Default evaluator: **Chan analytical series** (fast)
- Optional cross-check: `scipy.integrate.dblquad`
- Handles degenerate relative velocity (co-orbital / identical-state cases)

### 3. Full 3×3 covariances + RTN → TEME
- Accept diagonal TEME covariances **or** dense 3×3 matrices
- CDM-style **RTN / RIC** covariances rotated into TEME via the object state
- Ill-conditioned / singular projected covariances return clear HTTP 422 errors

### 4. Risk banding & action flag
| Band | Threshold |
| --- | --- |
| CRITICAL | Pc ≥ 1e-2 |
| HIGH | Pc ≥ 1e-3 |
| MEDIUM | Pc ≥ 1e-4 |
| LOW | below MEDIUM |

- `action_required = true` when Pc > **1e-4**

### 5. Sky traffic catalog (flight-sim style)
- Baked Celestrak-backed catalog (`data/sky_catalog.json`) — stations, visual, active
- One-shot `GET /api/v1/sky-traffic` propagates the full catalog
- Per-object orbit tracks via `GET /api/v1/sky-traffic/{id}/track`
- Full-period sampling for GEO/HEO (not a LEO-length stub)
- Frozen-ECEF orbit rings so distant satellites render completely

### 6. 3D globe operator UI
- React + Three.js Earth with stars, GEO reference belt, compressed altitude scale
- Search / filter traffic list; click globe or list to expand a satellite
- **Set primary / secondary** → globe focuses on **only that pair** (P/S labels + orbits)
- **Deselect** / **Clear pair** to pick a new conjunction pair
- Assess pair and (when available) simulate an evasive maneuver on-globe

### 7. Evasive maneuver planning
- `POST /api/v1/plan-maneuver` for a selected satellite pair
- Trajectory optimization toward reduced Pc while respecting mesh / link constraints
- Shared `ManeuverPlan` contract in `aether_core.py`

### 8. Mesh node sync (self-healing routing)
- Ingest node telemetry (`node_id`, position, velocity, `burn_scheduled`)
- Returns `mesh_reroute_active` and `recommended_next_hop` when a burn or high rate implies disruption

### 9. Shared domain contract
- `ConjunctionEvent` and `ManeuverPlan` dataclasses in `aether_core.py` for cross-module / collaborator integration

### 10. API + static UI serving
- FastAPI + Uvicorn, CORS for local Vite dev
- Built frontend served from `frontend/dist` when present
- OpenAPI docs at `/docs`

---

## Quick start

### Backend
```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

- Health: http://localhost:8000/health  
- Docs: http://localhost:8000/docs  
- API banner: http://localhost:8000/api  

### Frontend (dev)
```bash
cd frontend
npm install
npm run dev
```
Open http://localhost:5173 (Vite proxies `/api` and `/health` to `:8000`).

### Frontend (production build served by FastAPI)
```bash
cd frontend && npm install && npm run build
cd .. && uvicorn main:app --port 8000
```
Open http://localhost:8000.

### Tests
```bash
pytest tests/
```

---

## API overview

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Operational status |
| `GET` | `/api/v1/sky-traffic` | Full catalog with live positions |
| `GET` | `/api/v1/sky-traffic/{id}/track` | Full-orbit polyline for one object |
| `POST` | `/api/v1/assess-conjunction` | Propagate TLEs → DCA, Pc, risk, markers |
| `POST` | `/api/v1/orbit-track` | Sample a custom TLE orbit track |
| `POST` | `/api/v1/plan-maneuver` | Plan an evasive burn for a pair |
| `POST` | `/api/v1/mesh/node-sync` | Mesh reroute recommendation |

### Assess conjunction (sketch)
```json
{
  "primary_tle": { "name": "ISS", "line1": "1 ...", "line2": "2 ..." },
  "secondary_tle": { "name": "DEB", "line1": "1 ...", "line2": "2 ..." },
  "target_time": "2026-07-22T12:00:00Z",
  "hbr_meters": 20,
  "P1_diag": [0.01, 0.01, 0.01],
  "P2_diag": [0.01, 0.01, 0.01],
  "covariance_frame": "TEME",
  "poc_method": "chan"
}
```
Use `P1` / `P2` (3×3) with `covariance_frame: "RTN"` for CDM-style inputs.

---

## Repository map

| Path | Role |
| --- | --- |
| `main.py` | FastAPI app, CORS, static UI |
| `aether_core.py` | Shared `ConjunctionEvent` / `ManeuverPlan` contract |
| `api/routes.py` | HTTP routes |
| `engine/propagator.py` | SGP4 / Skyfield propagation |
| `engine/collision.py` | B-plane Pc (Chan / dblquad) |
| `engine/frames.py` | RTN ↔ TEME covariance transforms |
| `engine/geo.py` | TEME ↔ lat/lon / ECEF for the globe |
| `engine/catalog.py` | Sky catalog loader |
| `engine/trajectory.py` | Maneuver / trajectory optimization |
| `schemas/conjunction.py` | Pydantic request/response models |
| `data/sky_catalog.json` | Baked traffic catalog |
| `frontend/` | React + Three.js operator UI |
| `tests/` | Pytest suite |

---

## Typical operator flow

1. Open the UI — catalog loads onto the globe automatically  
2. Search / click a satellite → expand details + orbit  
3. **Set primary** and **Set secondary** → globe shows only that pair  
4. **Assess pair** → Pc, DCA, risk band, action flag  
5. Optionally **simulate maneuver** and inspect the burn path  
6. **Clear pair** to select a new conjunction  

---

## Stack

- **Python 3.10+** — FastAPI, Pydantic v2, NumPy, SciPy, SGP4, Skyfield, Astropy, Poliastro  
- **TypeScript** — React 18, Vite, Three.js, React Three Fiber / Drei  

---

## License / collaboration

Hackathon collaboration repo: [aryanbardeja1-alt/hackathon](https://github.com/aryanbardeja1-alt/hackathon). Commit and push to `main` after `pytest tests/` passes.
