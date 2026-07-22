import { useMemo, useState } from "react";
import type { AssessResponse, ManeuverPlan, SkyTrafficSat } from "../api";

type TrafficPanelProps = {
  traffic: SkyTrafficSat[];
  loading: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
  primaryId: string | null;
  secondaryId: string | null;
  onSetPrimary: (id: string) => void;
  onSetSecondary: (id: string) => void;
  onAssessPair: () => void;
  onSimulateManeuver: () => void;
  onDeselect: () => void;
  assessBusy: boolean;
  assessError: string | null;
  result: AssessResponse | null;
  maneuver: ManeuverPlan | null;
  maneuverBusy: boolean;
  maneuverError: string | null;
};

export default function TrafficPanel({
  traffic,
  loading,
  selectedId,
  onSelect,
  primaryId,
  secondaryId,
  onSetPrimary,
  onSetSecondary,
  onAssessPair,
  onSimulateManeuver,
  onDeselect,
  assessBusy,
  assessError,
  result,
  maneuver,
  maneuverBusy,
  maneuverError,
}: TrafficPanelProps) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "station" | "visual" | "active">("all");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return traffic
      .filter((s) => (filter === "all" ? true : s.object_type === filter))
      .filter(
        (s) =>
          !q ||
          s.name.toLowerCase().includes(q) ||
          String(s.norad_id).includes(q) ||
          s.id.includes(q),
      )
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [traffic, query, filter]);

  const selected = traffic.find((s) => s.id === selectedId) ?? null;
  const canDeselect = Boolean(selectedId || primaryId || secondaryId || result);

  return (
    <aside className="traffic">
      <header className="traffic-head">
        <h2>Sky traffic</h2>
        <p>
          {loading ? "Propagating catalog…" : `${traffic.length} objects on orbit`}
        </p>
      </header>

      <div className="traffic-tools">
        <input
          type="search"
          placeholder="Search name or NORAD…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search satellites"
        />
        <div className="filter-row">
          {(["all", "station", "visual", "active"] as const).map((key) => (
            <button
              key={key}
              type="button"
              className={`chip ${filter === key ? "on" : ""}`}
              onClick={() => setFilter(key)}
            >
              {key}
            </button>
          ))}
        </div>
      </div>

      <ul className="traffic-list" role="listbox" aria-label="Satellites">
        {filtered.map((sat) => {
          const active = sat.id === selectedId;
          const isPri = sat.id === primaryId;
          const isSec = sat.id === secondaryId;
          return (
            <li key={sat.id}>
              <button
                type="button"
                className={`traffic-item ${active ? "selected" : ""}`}
                onClick={() => onSelect(sat.id)}
              >
                <span className={`dot type-${sat.object_type}`} />
                <span className="traffic-meta">
                  <span className="traffic-name">
                    {sat.name}
                    {isPri && <em className="badge">P</em>}
                    {isSec && <em className="badge sec">S</em>}
                  </span>
                  <span className="traffic-sub">
                    #{sat.norad_id} · {sat.alt_km.toFixed(0)} km · {sat.speed_km_s.toFixed(2)} km/s
                  </span>
                </span>
              </button>
            </li>
          );
        })}
        {!loading && filtered.length === 0 && (
          <li className="traffic-empty">No objects match.</li>
        )}
      </ul>

      {selected && (
        <div className="sat-detail" key={selected.id}>
          <div className="sat-detail-top">
            <h3>{selected.name}</h3>
            <span className={`pill type-${selected.object_type}`}>{selected.object_type}</span>
          </div>
          <dl>
            <div>
              <dt>NORAD</dt>
              <dd>{selected.norad_id}</dd>
            </div>
            <div>
              <dt>Altitude</dt>
              <dd>{selected.alt_km.toFixed(1)} km</dd>
            </div>
            <div>
              <dt>Lat / Lon</dt>
              <dd>
                {selected.lat_deg.toFixed(2)}° / {selected.lon_deg.toFixed(2)}°
              </dd>
            </div>
            <div>
              <dt>Speed</dt>
              <dd>{selected.speed_km_s.toFixed(3)} km/s</dd>
            </div>
          </dl>
          <div className="sat-detail-actions">
            <button type="button" className="btn ghost" onClick={() => onSetPrimary(selected.id)}>
              Set primary
            </button>
            <button type="button" className="btn ghost" onClick={() => onSetSecondary(selected.id)}>
              Set secondary
            </button>
            <button type="button" className="btn ghost" onClick={() => onSelect("")}>
              Deselect
            </button>
          </div>
        </div>
      )}

      <div className="assess-strip">
        <p>
          Pair: <strong>{primaryId ? traffic.find((s) => s.id === primaryId)?.name ?? "—" : "—"}</strong>
          {" × "}
          <strong>{secondaryId ? traffic.find((s) => s.id === secondaryId)?.name ?? "—" : "—"}</strong>
        </p>
        <div className="assess-actions">
          <button
            type="button"
            className="btn primary"
            disabled={!primaryId || !secondaryId || assessBusy}
            onClick={onAssessPair}
          >
            {assessBusy ? "Assessing…" : "Assess pair"}
          </button>
          <button
            type="button"
            className="btn primary"
            disabled={!primaryId || !secondaryId || maneuverBusy}
            onClick={onSimulateManeuver}
          >
            {maneuverBusy ? "Planning…" : "Simulate maneuver"}
          </button>
          <button
            type="button"
            className="btn ghost"
            disabled={!canDeselect || assessBusy || maneuverBusy}
            onClick={onDeselect}
          >
            Clear pair
          </button>
        </div>
        {assessError && <p className="dock-error">{assessError}</p>}
        {maneuverError && <p className="dock-error">{maneuverError}</p>}
        {result && (
          <div className={`result risk-${result.risk_level.toLowerCase()}`}>
            <div className="result-risk">{result.risk_level}</div>
            <dl>
              <div>
                <dt>Pc</dt>
                <dd>{result.poc.toExponential(3)}</dd>
              </div>
              <div>
                <dt>DCA</dt>
                <dd>{result.dca_km.toFixed(3)} km</dd>
              </div>
            </dl>
          </div>
        )}

        {maneuver && (
          <div className="maneuver">
            {maneuver.delta_v_magnitude_m_s > 0 ? (
              <>
                <div className="maneuver-head">
                  <span className="maneuver-dv">
                    Δv {maneuver.delta_v_magnitude_m_s.toFixed(3)} m/s
                  </span>
                  <span className="maneuver-lead">
                    T−{maneuver.burn_lead_hours.toFixed(2)} h
                  </span>
                </div>
                <dl>
                  <div>
                    <dt>Pc before → after</dt>
                    <dd>
                      {maneuver.poc_before.toExponential(2)} →{" "}
                      {maneuver.poc_after.toExponential(2)}
                    </dd>
                  </div>
                  <div>
                    <dt>Miss before → after</dt>
                    <dd>
                      {maneuver.miss_distance_before_km.toFixed(3)} →{" "}
                      {maneuver.miss_distance_after_km.toFixed(3)} km
                    </dd>
                  </div>
                </dl>
                <p className="maneuver-legend">
                  <span className="swatch baseline" /> no burn
                  <span className="swatch burned" /> after burn
                </p>
                {maneuver.requires_mesh_rerouting && (
                  <p className="maneuver-mesh">Mesh rerouting required</p>
                )}
              </>
            ) : (
              <p className="maneuver-clear">
                No burn required — {maneuver.primary_name} clears{" "}
                {maneuver.secondary_name} by{" "}
                {maneuver.miss_distance_before_km.toFixed(0)} km.
              </p>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
