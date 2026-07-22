import { useState } from "react";
import { OBJECT_TYPE, TRACK } from "../palette";

type LegendProps = {
  pairMode: boolean;
  hasManeuver: boolean;
};

type Row = { color: string; label: string; dashed?: boolean; dot?: boolean };

/**
 * Key for what is drawn on the globe. Only shows rows that are actually on
 * screen — a legend listing tracks that aren't there is just noise.
 */
export default function Legend({ pairMode, hasManeuver }: LegendProps) {
  const [open, setOpen] = useState(true);

  const paths: Row[] = pairMode
    ? [
        { color: TRACK.primary, label: "Primary orbit" },
        { color: TRACK.secondary, label: "Secondary orbit" },
      ]
    : [{ color: TRACK.primary, label: "Selected orbit" }];

  if (hasManeuver) {
    paths.push(
      { color: TRACK.baseline, label: "Predicted path, no burn", dashed: true },
      { color: TRACK.maneuvered, label: "Path after burn" },
      { color: TRACK.burn, label: "Burn point", dot: true },
    );
  }

  const objects: Row[] = [
    { color: OBJECT_TYPE.station, label: "Station", dot: true },
    { color: OBJECT_TYPE.visual, label: "Visual", dot: true },
    { color: OBJECT_TYPE.debris, label: "Debris / test", dot: true },
    { color: OBJECT_TYPE.active, label: "Active", dot: true },
  ];

  return (
    <div className={`legend ${open ? "open" : "closed"}`}>
      <button
        type="button"
        className="legend-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        Legend
        <span aria-hidden="true">{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="legend-body">
          <p className="legend-group">Paths</p>
          <ul>
            {paths.map((row) => (
              <li key={row.label}>
                <LegendSwatch row={row} />
                {row.label}
              </li>
            ))}
          </ul>

          <p className="legend-group">Objects</p>
          <ul>
            {objects.map((row) => (
              <li key={row.label}>
                <LegendSwatch row={row} />
                {row.label}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function LegendSwatch({ row }: { row: Row }) {
  if (row.dot) {
    return <span className="legend-dot" style={{ background: row.color }} />;
  }
  return (
    <span
      className="legend-line"
      style={{
        borderTopColor: row.color,
        borderTopStyle: row.dashed ? "dashed" : "solid",
      }}
    />
  );
}
