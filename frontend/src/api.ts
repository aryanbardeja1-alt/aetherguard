export type RiskLevel = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";

export type GeoMarker = {
  name: string;
  lat_deg: number;
  lon_deg: number;
  alt_km: number;
  position_km: number[];
};

export type AssessResponse = {
  dca_km: number;
  poc: number;
  risk_level: RiskLevel;
  action_required: boolean;
  poc_method: string;
  primary: GeoMarker | null;
  secondary: GeoMarker | null;
};

export type OrbitTrackResponse = {
  name: string;
  points: GeoMarker[];
};

export type TleInput = {
  name: string;
  line1: string;
  line2: string;
};

export type SkyTrafficSat = {
  id: string;
  name: string;
  norad_id: number;
  object_type: string;
  lat_deg: number;
  lon_deg: number;
  alt_km: number;
  speed_km_s: number;
  position_km: number[];
  velocity_km_s: number[];
  line1: string;
  line2: string;
};

export type SkyTrafficResponse = {
  epoch: string;
  count: number;
  catalog_size?: number;
  skipped?: number;
  satellites: SkyTrafficSat[];
};

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    return JSON.stringify(body.detail ?? body);
  } catch {
    return res.statusText;
  }
}

export async function fetchSkyTraffic(): Promise<SkyTrafficResponse> {
  const res = await fetch("/api/v1/sky-traffic");
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function fetchSatTrack(satId: string): Promise<OrbitTrackResponse> {
  const res = await fetch(`/api/v1/sky-traffic/${encodeURIComponent(satId)}/track`);
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function assessConjunction(payload: unknown): Promise<AssessResponse> {
  const res = await fetch("/api/v1/assess-conjunction", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch("/health");
    return res.ok;
  } catch {
    return false;
  }
}
