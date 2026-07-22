import { useMemo } from "react";
import type { ManeuverPlan } from "../api";
import { TRACK } from "../palette";

const WIDTH = 260;
const HEIGHT = 76;
const PAD_L = 34;
const PAD_R = 8;
const PAD_T = 8;
const PAD_B = 18;

/**
 * Separation between the unmaneuvered and post-burn paths over time.
 *
 * The burn opens a gap of order a kilometre against a 6,378 km globe, which is
 * far below one pixel in the 3D view. Plotting the gap directly is the only
 * honest way to actually read the maneuver's effect.
 */
export default function SeparationChart({ plan }: { plan: ManeuverPlan }) {
  const model = useMemo(() => {
    const n = Math.min(plan.baseline_track.length, plan.maneuvered_track.length);
    if (n < 2) return null;

    const start = Date.parse(plan.burn_time);
    const tcaMs = Date.parse(plan.tca);

    const series: { t: number; sep: number }[] = [];
    for (let i = 0; i < n; i += 1) {
      const a = plan.baseline_track[i].position_km;
      const b = plan.maneuvered_track[i].position_km;
      series.push({
        t: i / (n - 1),
        sep: Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]),
      });
    }

    const maxSep = Math.max(...series.map((p) => p.sep), 1e-6);
    // Tracks run burn -> TCA -> as far again, so TCA sits at the midpoint.
    const spanMs = (tcaMs - start) * 2;
    const tcaFrac = spanMs > 0 ? (tcaMs - start) / spanMs : 0.5;

    const x = (t: number) => PAD_L + t * (WIDTH - PAD_L - PAD_R);
    const y = (s: number) =>
      HEIGHT - PAD_B - (s / maxSep) * (HEIGHT - PAD_T - PAD_B);

    return {
      maxSep,
      tcaFrac,
      path: series.map((p) => `${x(p.t).toFixed(1)},${y(p.sep).toFixed(1)}`).join(" "),
      tcaX: x(tcaFrac),
      baseY: y(0),
    };
  }, [plan]);

  if (!model) return null;

  const fmt = (km: number) => (km < 1 ? `${(km * 1000).toFixed(0)} m` : `${km.toFixed(1)} km`);

  return (
    <figure className="sep-chart">
      <figcaption>Separation from unmaneuvered path</figcaption>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label="Separation over time">
        <line
          x1={PAD_L}
          y1={model.baseY}
          x2={WIDTH - PAD_R}
          y2={model.baseY}
          stroke="rgba(154,168,181,0.3)"
          strokeWidth="1"
        />
        <line
          x1={model.tcaX}
          y1={PAD_T}
          x2={model.tcaX}
          y2={model.baseY}
          stroke={TRACK.baseline}
          strokeWidth="1"
          strokeDasharray="3 3"
        />
        <polyline points={model.path} fill="none" stroke={TRACK.maneuvered} strokeWidth="1.8" />
        <text x={PAD_L - 4} y={PAD_T + 6} textAnchor="end" className="sep-tick">
          {fmt(model.maxSep)}
        </text>
        <text x={PAD_L - 4} y={model.baseY + 3} textAnchor="end" className="sep-tick">
          0
        </text>
        <text x={PAD_L} y={HEIGHT - 5} className="sep-tick">
          burn
        </text>
        <text x={model.tcaX} y={HEIGHT - 5} textAnchor="middle" className="sep-tick">
          TCA
        </text>
      </svg>
    </figure>
  );
}
