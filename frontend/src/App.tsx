import { useCallback, useEffect, useState } from "react";
import GlobeScene from "./components/GlobeScene";
import TrafficPanel from "./components/TrafficPanel";
import {
  assessConjunction,
  checkHealth,
  fetchSatTrack,
  fetchSkyTraffic,
  planManeuver,
  type AssessResponse,
  type GeoMarker,
  type ManeuverPlan,
  type SkyTrafficSat,
} from "./api";

export default function App() {
  const [online, setOnline] = useState(false);
  const [loading, setLoading] = useState(true);
  const [traffic, setTraffic] = useState<SkyTrafficSat[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedTrack, setSelectedTrack] = useState<GeoMarker[]>([]);
  const [primaryId, setPrimaryId] = useState<string | null>(null);
  const [secondaryId, setSecondaryId] = useState<string | null>(null);
  const [primaryTrack, setPrimaryTrack] = useState<GeoMarker[]>([]);
  const [secondaryTrack, setSecondaryTrack] = useState<GeoMarker[]>([]);
  const [assessBusy, setAssessBusy] = useState(false);
  const [assessError, setAssessError] = useState<string | null>(null);
  const [result, setResult] = useState<AssessResponse | null>(null);
  const [maneuver, setManeuver] = useState<ManeuverPlan | null>(null);
  const [maneuverBusy, setManeuverBusy] = useState(false);
  const [maneuverError, setManeuverError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [catalogMeta, setCatalogMeta] = useState<{ count: number; skipped: number } | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const ok = await checkHealth();
      if (alive) setOnline(ok);
    };
    tick();
    const id = window.setInterval(tick, 10000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const loadTraffic = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await fetchSkyTraffic();
      setTraffic(data.satellites);
      setCatalogMeta({ count: data.count, skipped: data.skipped ?? 0 });
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load sky traffic");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTraffic();
    const id = window.setInterval(loadTraffic, 60_000);
    return () => window.clearInterval(id);
  }, [loadTraffic]);

  const onSelect = useCallback(async (id: string) => {
    if (!id) {
      setSelectedId(null);
      setSelectedTrack([]);
      return;
    }
    setSelectedId(id);
    try {
      const track = await fetchSatTrack(id);
      setSelectedTrack(track.points);
    } catch (err) {
      setSelectedTrack([]);
      setLoadError(err instanceof Error ? `Orbit track: ${err.message}` : "Orbit track failed");
    }
  }, []);

  // When both pair roles are set, load only those two orbits for the globe.
  useEffect(() => {
    let cancelled = false;
    if (!primaryId || !secondaryId) {
      setPrimaryTrack([]);
      setSecondaryTrack([]);
      return;
    }
    (async () => {
      try {
        const [pTrack, sTrack] = await Promise.all([
          fetchSatTrack(primaryId),
          fetchSatTrack(secondaryId),
        ]);
        if (!cancelled) {
          setPrimaryTrack(pTrack.points);
          setSecondaryTrack(sTrack.points);
          setSelectedTrack([]);
        }
      } catch (err) {
        if (!cancelled) {
          setPrimaryTrack([]);
          setSecondaryTrack([]);
          setLoadError(err instanceof Error ? `Pair tracks: ${err.message}` : "Pair tracks failed");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [primaryId, secondaryId]);

  const onAssessPair = useCallback(async () => {
    const primary = traffic.find((s) => s.id === primaryId);
    const secondary = traffic.find((s) => s.id === secondaryId);
    if (!primary || !secondary) return;

    setAssessBusy(true);
    setAssessError(null);
    try {
      const variance = 1e-4;
      const assessment = await assessConjunction({
        primary_tle: { name: primary.name, line1: primary.line1, line2: primary.line2 },
        secondary_tle: {
          name: secondary.name,
          line1: secondary.line1,
          line2: secondary.line2,
        },
        target_time: new Date().toISOString(),
        hbr_meters: 20,
        P1_diag: [variance, variance, variance],
        P2_diag: [variance, variance, variance],
        covariance_frame: "TEME",
        poc_method: "chan",
      });
      setResult(assessment);
    } catch (err) {
      setAssessError(err instanceof Error ? err.message : "Assessment failed");
    } finally {
      setAssessBusy(false);
    }
  }, [traffic, primaryId, secondaryId]);

  const onSimulateManeuver = useCallback(async () => {
    if (!primaryId || !secondaryId) return;

    setManeuverBusy(true);
    setManeuverError(null);
    try {
      setManeuver(await planManeuver(primaryId, secondaryId));
    } catch (err) {
      setManeuver(null);
      setManeuverError(err instanceof Error ? err.message : "Maneuver planning failed");
    } finally {
      setManeuverBusy(false);
    }
  }, [primaryId, secondaryId]);

  const onDeselect = useCallback(() => {
    setSelectedId(null);
    setSelectedTrack([]);
    setPrimaryId(null);
    setSecondaryId(null);
    setPrimaryTrack([]);
    setSecondaryTrack([]);
    setResult(null);
    setAssessError(null);
    setManeuver(null);
    setManeuverError(null);
    setLoadError(null);
  }, []);

  return (
    <div className="app">
      <div className="globe-layer">
        <GlobeScene
          traffic={traffic}
          selectedId={selectedId}
          selectedTrack={selectedTrack}
          primaryId={primaryId}
          secondaryId={secondaryId}
          primaryTrack={primaryTrack}
          secondaryTrack={secondaryTrack}
          result={result}
          maneuver={maneuver}
          onSelect={onSelect}
        />
      </div>

      <div className="atmosphere" />

      <header className="hero">
        <p className={`status ${online ? "up" : "down"}`}>
          <span className="status-dot" />
          {online ? "Link live" : "API offline"}
          {loading ? " · updating" : ""}
        </p>
        <h1 className="brand">AetherGuard</h1>
        <p className="tagline">
          {primaryId && secondaryId
            ? "Pair locked — only primary and secondary are shown on the globe."
            : "Click any satellite to expand — set primary & secondary to focus the pair."}
          {catalogMeta
            ? ` ${catalogMeta.count} on orbit${catalogMeta.skipped ? ` (${catalogMeta.skipped} skipped)` : ""}.`
            : ""}
        </p>
        {loadError && <p className="hero-error">{loadError}</p>}
      </header>

      <TrafficPanel
        traffic={traffic}
        loading={loading}
        selectedId={selectedId}
        onSelect={onSelect}
        primaryId={primaryId}
        secondaryId={secondaryId}
        onSetPrimary={setPrimaryId}
        onSetSecondary={setSecondaryId}
        onAssessPair={onAssessPair}
        onSimulateManeuver={onSimulateManeuver}
        onDeselect={onDeselect}
        assessBusy={assessBusy}
        assessError={assessError}
        result={result}
        maneuver={maneuver}
        maneuverBusy={maneuverBusy}
        maneuverError={maneuverError}
      />
    </div>
  );
}
