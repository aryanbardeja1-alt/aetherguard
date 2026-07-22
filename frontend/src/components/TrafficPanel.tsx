import { useMemo, useState } from "react";
import type { AssessResponse, SkyTrafficSat } from "../api";

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
  onDeselect: () => void;
  assessBusy: boolean;
  assessError: string | null;
  result: AssessResponse | null;
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
  onDeselect,
  assessBusy,
  assessError,
  result,
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
            className="btn ghost"
            disabled={!canDeselect || assessBusy}
            onClick={onDeselect}
          >
            Clear pair
          </button>
        </div>
        {assessError && <p className="dock-error">{assessError}</p>}
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
      </div>
    </aside>
  );
}
