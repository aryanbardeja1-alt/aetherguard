import { useState } from "react";
import type { FormEvent } from "react";
import type { AssessResponse, TleInput } from "../api";

const ISS: TleInput = {
  name: "ISS",
  line1: "1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927",
  line2: "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537",
};

const DEFAULT_TIME = "2008-09-20T12:30:00Z";

type AssessDockProps = {
  busy: boolean;
  error: string | null;
  result: AssessResponse | null;
  onAssess: (payload: {
    primary: TleInput;
    secondary: TleInput;
    targetTime: string;
    hbr: number;
    sigmaKm: number;
  }) => void;
};

export default function AssessDock({ busy, error, result, onAssess }: AssessDockProps) {
  const [primaryName, setPrimaryName] = useState(ISS.name);
  const [primaryL1, setPrimaryL1] = useState(ISS.line1);
  const [primaryL2, setPrimaryL2] = useState(ISS.line2);
  const [secondaryName, setSecondaryName] = useState("ISS-COPY");
  const [secondaryL1, setSecondaryL1] = useState(ISS.line1);
  const [secondaryL2, setSecondaryL2] = useState(ISS.line2);
  const [targetTime, setTargetTime] = useState(DEFAULT_TIME);
  const [hbr, setHbr] = useState(20);
  const [sigmaKm, setSigmaKm] = useState(0.001);

  function submit(e: FormEvent) {
    e.preventDefault();
    onAssess({
      primary: { name: primaryName, line1: primaryL1, line2: primaryL2 },
      secondary: { name: secondaryName, line1: secondaryL1, line2: secondaryL2 },
      targetTime,
      hbr,
      sigmaKm,
    });
  }

  function loadSelfEncounter() {
    setPrimaryName("ISS");
    setPrimaryL1(ISS.line1);
    setPrimaryL2(ISS.line2);
    setSecondaryName("ISS-COPY");
    setSecondaryL1(ISS.line1);
    setSecondaryL2(ISS.line2);
    setTargetTime(DEFAULT_TIME);
    setSigmaKm(0.001);
    setHbr(20);
  }

  return (
    <aside className="dock">
      <header className="dock-head">
        <h2>Conjunction desk</h2>
        <p>Propagate TLEs, project RTN/TEME covariances, compute Pc with Chan.</p>
      </header>

      <form className="dock-form" onSubmit={submit}>
        <div className="field-row">
          <label>
            Target epoch (UTC)
            <input
              type="text"
              value={targetTime}
              onChange={(e) => setTargetTime(e.target.value)}
              spellCheck={false}
            />
          </label>
        </div>

        <fieldset>
          <legend>Primary</legend>
          <label>
            Name
            <input value={primaryName} onChange={(e) => setPrimaryName(e.target.value)} />
          </label>
          <label>
            Line 1
            <input value={primaryL1} onChange={(e) => setPrimaryL1(e.target.value)} spellCheck={false} />
          </label>
          <label>
            Line 2
            <input value={primaryL2} onChange={(e) => setPrimaryL2(e.target.value)} spellCheck={false} />
          </label>
        </fieldset>

        <fieldset>
          <legend>Secondary</legend>
          <label>
            Name
            <input value={secondaryName} onChange={(e) => setSecondaryName(e.target.value)} />
          </label>
          <label>
            Line 1
            <input
              value={secondaryL1}
              onChange={(e) => setSecondaryL1(e.target.value)}
              spellCheck={false}
            />
          </label>
          <label>
            Line 2
            <input
              value={secondaryL2}
              onChange={(e) => setSecondaryL2(e.target.value)}
              spellCheck={false}
            />
          </label>
        </fieldset>

        <div className="field-grid">
          <label>
            HBR (m)
            <input
              type="number"
              min={0.1}
              step={0.1}
              value={hbr}
              onChange={(e) => setHbr(Number(e.target.value))}
            />
          </label>
          <label>
            σ diag (km)
            <input
              type="number"
              min={1e-6}
              step={0.001}
              value={sigmaKm}
              onChange={(e) => setSigmaKm(Number(e.target.value))}
            />
          </label>
        </div>

        <div className="dock-actions">
          <button type="button" className="btn ghost" onClick={loadSelfEncounter}>
            Load ISS demo
          </button>
          <button type="submit" className="btn primary" disabled={busy}>
            {busy ? "Assessing…" : "Assess conjunction"}
          </button>
        </div>
      </form>

      {error && <p className="dock-error">{error}</p>}

      {result && (
        <div className={`result risk-${result.risk_level.toLowerCase()}`} role="status">
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
            <div>
              <dt>Action</dt>
              <dd>{result.action_required ? "Required" : "None"}</dd>
            </div>
            <div>
              <dt>Method</dt>
              <dd>{result.poc_method}</dd>
            </div>
          </dl>
        </div>
      )}
    </aside>
  );
}
