import { useCallback, useEffect, useState } from "react";
import GlobeScene from "./components/GlobeScene";
import AssessDock from "./components/AssessDock";
import {
  assessConjunction,
  checkHealth,
  fetchOrbitTrack,
  type AssessResponse,
  type GeoMarker,
  type TleInput,
} from "./api";

export default function App() {
  const [online, setOnline] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AssessResponse | null>(null);
  const [primaryTrack, setPrimaryTrack] = useState<GeoMarker[]>([]);
  const [secondaryTrack, setSecondaryTrack] = useState<GeoMarker[]>([]);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const ok = await checkHealth();
      if (alive) setOnline(ok);
    };
    tick();
    const id = window.setInterval(tick, 8000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const onAssess = useCallback(
    async (payload: {
      primary: TleInput;
      secondary: TleInput;
      targetTime: string;
      hbr: number;
      sigmaKm: number;
    }) => {
      setBusy(true);
      setError(null);
      try {
        const variance = payload.sigmaKm * payload.sigmaKm;
        const assessment = await assessConjunction({
          primary_tle: payload.primary,
          secondary_tle: payload.secondary,
          target_time: payload.targetTime,
          hbr_meters: payload.hbr,
          P1_diag: [variance, variance, variance],
          P2_diag: [variance, variance, variance],
          covariance_frame: "TEME",
          poc_method: "chan",
        });
        setResult(assessment);

        const [trackA, trackB] = await Promise.all([
          fetchOrbitTrack(payload.primary, payload.targetTime),
          fetchOrbitTrack(payload.secondary, payload.targetTime),
        ]);
        setPrimaryTrack(trackA.points);
        setSecondaryTrack(trackB.points);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Assessment failed");
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  return (
    <div className="app">
      <div className="globe-layer" aria-hidden={!result}>
        <GlobeScene result={result} primaryTrack={primaryTrack} secondaryTrack={secondaryTrack} />
      </div>

      <div className="atmosphere" />

      <header className="hero">
        <p className={`status ${online ? "up" : "down"}`}>
          <span className="status-dot" />
          {online ? "Link live" : "API offline"}
        </p>
        <h1 className="brand">AetherGuard</h1>
        <p className="tagline">Autonomous orbital safety — see the encounter, measure the risk.</p>
      </header>

      <AssessDock busy={busy} error={error} result={result} onAssess={onAssess} />
    </div>
  );
}
