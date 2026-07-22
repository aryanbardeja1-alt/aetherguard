import { useEffect, useMemo, useRef } from "react";
import type { RefObject } from "react";
import { Canvas, ThreeEvent, useFrame, useThree } from "@react-three/fiber";
import { Html, Line, OrbitControls, Stars } from "@react-three/drei";
import * as THREE from "three";
import type { AssessResponse, GeoMarker, ManeuverPlan, SkyTrafficSat } from "../api";
import { OBJECT_TYPE, TRACK } from "../palette";

const EARTH_R = 1;
const EARTH_KM = 6378.137;

/** How far above the surface the reference altitude sits, in Earth radii. */
const VIEW_LIFT = 2.6;
/** Below this, altitude is spread generously so LEO clears the surface. */
const SOFT_KM = 1500;
/** Altitude mapped to the full lift; beyond it the curve keeps rising slowly. */
const REFERENCE_ALT_KM = 140000;

/**
 * Compress true altitude so LEO through deep HEO share one view shell.
 *
 * Smooth and strictly monotonic by construction. The previous piecewise
 * version kinked at its 2,000 km and 36,000 km seams and clamped everything
 * above 136,000 km to a single radius, which flattened the top of any orbit
 * reaching that high — a Chandra-class ellipse came out as neither ellipse nor
 * circle. Radial compression still distorts proportions, unavoidably so when
 * one view spans a 400 km orbit and a 137,000 km one, but shapes stay smooth
 * closed curves.
 */
export function displayAltitudeKm(altKm: number): number {
  const alt = Math.max(altKm, 120);
  const t = Math.log1p(alt / SOFT_KM) / Math.log1p(REFERENCE_ALT_KM / SOFT_KM);
  return EARTH_KM * VIEW_LIFT * t;
}

export function latLonToVec3(lat: number, lon: number, altKm: number, scale = EARTH_R): THREE.Vector3 {
  const radius = scale * (1 + displayAltitudeKm(altKm) / EARTH_KM);
  const phi = THREE.MathUtils.degToRad(90 - lat);
  const theta = THREE.MathUtils.degToRad(lon + 180);
  return new THREE.Vector3(
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta),
  );
}

/** Map ECEF kilometres → Three.js Y-up scene with compressed radius. */
export function ecefKmToVec3(positionKm: number[], scale = EARTH_R): THREE.Vector3 {
  const ex = positionKm[0] ?? 0;
  const ey = positionKm[1] ?? 0;
  const ez = positionKm[2] ?? 0;
  const mag = Math.hypot(ex, ey, ez);
  if (mag < 1e-6) return new THREE.Vector3();
  const alt = mag - EARTH_KM;
  const displayR = scale * (1 + displayAltitudeKm(alt) / EARTH_KM);
  const s = displayR / mag;
  // Match latLonToVec3 convention: (X, Z, -Y) in Three.js.
  return new THREE.Vector3(ex * s, ez * s, -ey * s);
}

function typeColor(objectType: string): string {
  return OBJECT_TYPE[objectType as keyof typeof OBJECT_TYPE] ?? OBJECT_TYPE.active;
}

function markerSize(altKm: number, selected: boolean): number {
  // Farther objects get larger screen presence so they don't vanish.
  let base = 0.015;
  if (altKm > 5000) base = 0.022;
  if (altKm > 20000) base = 0.032;
  if (altKm > 50000) base = 0.04;
  return selected ? base * 1.7 : base;
}

function Earth() {
  return (
    <group>
      <mesh>
        <sphereGeometry args={[EARTH_R, 64, 64]} />
        <meshStandardMaterial
          color="#1c4a5c"
          roughness={0.82}
          metalness={0.12}
          emissive="#0a1c24"
          emissiveIntensity={0.15}
        />
      </mesh>
      <mesh scale={1.012}>
        <sphereGeometry args={[EARTH_R, 48, 48]} />
        <meshStandardMaterial
          color="#8ec8c0"
          transparent
          opacity={0.08}
          roughness={1}
          metalness={0}
          depthWrite={false}
        />
      </mesh>
      <mesh scale={1.004}>
        <sphereGeometry args={[EARTH_R, 48, 48]} />
        <meshBasicMaterial color="#d4a574" wireframe transparent opacity={0.04} />
      </mesh>
      <mesh scale={1.045}>
        <sphereGeometry args={[EARTH_R, 32, 32]} />
        <meshBasicMaterial
          color="#6a9eab"
          transparent
          opacity={0.12}
          side={THREE.BackSide}
          depthWrite={false}
        />
      </mesh>
      {/* GEO reference shell — helps far sats read as a complete belt */}
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <ringGeometry
          args={[
            1 + displayAltitudeKm(35786) / EARTH_KM - 0.01,
            1 + displayAltitudeKm(35786) / EARTH_KM + 0.01,
            96,
          ]}
        />
        <meshBasicMaterial color="#d4a574" transparent opacity={0.16} side={THREE.DoubleSide} />
      </mesh>
    </group>
  );
}

/**
 * Baseline (no burn) against post-burn trajectory. Both come from the same
 * propagator starting at the burn point, so they leave together and the gap
 * that opens is the maneuver.
 */
function ManeuverPaths({ plan, exaggeration }: { plan: ManeuverPlan; exaggeration: number }) {
  const burned = plan.delta_v_magnitude_m_s > 0;

  // A burn opens a gap of order a kilometre. Against a 6,378 km globe that is
  // well under a pixel, so at 1x the two paths overlap exactly and the
  // maneuver looks like it did nothing. Scaling the offset from the baseline
  // is the only way to see it; the panel states the factor so the geometry is
  // never mistaken for true scale.
  const amplified = useMemo(() => {
    if (exaggeration <= 1) return plan.maneuvered_track;
    const n = Math.min(plan.baseline_track.length, plan.maneuvered_track.length);
    return plan.maneuvered_track.slice(0, n).map((point, i) => {
      const base = plan.baseline_track[i].position_km;
      return {
        ...point,
        position_km: point.position_km.map(
          (v, axis) => base[axis] + (v - base[axis]) * exaggeration,
        ),
      };
    });
  }, [plan, exaggeration]);

  const tcaIndex = Math.floor((amplified.length - 1) / 2);

  return (
    <group>
      <OrbitPath points={plan.baseline_track} color={TRACK.baseline} dashed opacity={burned ? 0.55 : 0.9} />
      {burned && <OrbitPath points={amplified} color={TRACK.maneuvered} lineWidth={2.8} />}
      {burned && amplified.length > 0 && <BurnMarker point={amplified[0]} />}
      {burned && amplified.length > 2 && (
        <MissMarker
          baseline={plan.baseline_track[tcaIndex]}
          maneuvered={amplified[tcaIndex]}
          missKm={plan.miss_distance_after_km}
        />
      )}
    </group>
  );
}

/** Closest approach: joins where the satellite would have been to where it is. */
function MissMarker({
  baseline,
  maneuvered,
  missKm,
}: {
  baseline: GeoMarker;
  maneuvered: GeoMarker;
  missKm: number;
}) {
  const points = useMemo(
    () =>
      [
        ecefKmToVec3(baseline.position_km).toArray(),
        ecefKmToVec3(maneuvered.position_km).toArray(),
      ] as [number, number, number][],
    [baseline, maneuvered],
  );
  const tip = useMemo(() => ecefKmToVec3(maneuvered.position_km), [maneuvered]);

  return (
    <group>
      <Line points={points} color={TRACK.burn} lineWidth={1.4} transparent opacity={0.8} />
      <group position={tip}>
        <mesh>
          <sphereGeometry args={[0.016, 10, 10]} />
          <meshStandardMaterial color={TRACK.maneuvered} emissive={TRACK.maneuvered} emissiveIntensity={0.7} />
        </mesh>
        <Html distanceFactor={9} style={{ pointerEvents: "none" }} zIndexRange={[100, 0]}>
          <div className="sat-tag miss">
            {missKm < 1 ? `${(missKm * 1000).toFixed(0)} m` : `${missKm.toFixed(2)} km`}
          </div>
        </Html>
      </group>
    </group>
  );
}

/** Where the impulse is applied — the point both tracks share. */
function BurnMarker({ point }: { point: GeoMarker }) {
  const pos = useMemo(() => ecefKmToVec3(point.position_km), [point]);
  return (
    <group position={pos}>
      <mesh>
        <sphereGeometry args={[0.022, 12, 12]} />
        <meshStandardMaterial color={TRACK.burn} emissive={TRACK.burn} emissiveIntensity={0.8} />
      </mesh>
      <Html distanceFactor={9} style={{ pointerEvents: "none" }} zIndexRange={[100, 0]}>
        <div className="sat-tag burn">burn</div>
      </Html>
    </group>
  );
}

/** Prefer frozen-ECEF cartesian rings so GEO/HEO orbits render fully. */
function OrbitPath({
  points,
  color,
  dashed = false,
  lineWidth = 1.6,
  opacity = 0.92,
}: {
  points: GeoMarker[];
  color: string;
  dashed?: boolean;
  lineWidth?: number;
  opacity?: number;
}) {
  const pathPoints = useMemo(() => {
    if (points.length < 2) return null;
    const useEcef = points.every((p) => Array.isArray(p.position_km) && p.position_km.length === 3);
    if (useEcef) {
      return points.map((p) => ecefKmToVec3(p.position_km).toArray() as [number, number, number]);
    }
    // Fallback: lat/lon path with antimeridian splits handled by a single segment.
    return points.map(
      (p) => latLonToVec3(p.lat_deg, p.lon_deg, p.alt_km).toArray() as [number, number, number],
    );
  }, [points]);

  if (!pathPoints) return null;
  return (
    <Line
      points={pathPoints}
      color={color}
      lineWidth={lineWidth}
      transparent
      opacity={opacity}
      dashed={dashed}
      dashSize={0.05}
      gapSize={0.03}
    />
  );
}

function TrafficMarker({
  sat,
  selected,
  role,
  onSelect,
}: {
  sat: SkyTrafficSat;
  selected: boolean;
  role?: "primary" | "secondary" | null;
  onSelect: (id: string) => void;
}) {
  const ref = useRef<THREE.Mesh>(null);
  const color =
    role === "primary" ? TRACK.primary : role === "secondary" ? TRACK.secondary : typeColor(sat.object_type);
  const highlighted = selected || Boolean(role);
  const pos = latLonToVec3(sat.lat_deg, sat.lon_deg, sat.alt_km);
  const size = markerSize(sat.alt_km, highlighted);

  useFrame(({ clock }) => {
    if (!ref.current || !highlighted) return;
    ref.current.scale.setScalar(1 + Math.sin(clock.elapsedTime * 3.5) * 0.2);
  });

  const handleClick = (event: ThreeEvent<MouseEvent>) => {
    event.stopPropagation();
    onSelect(sat.id);
  };

  const label =
    role === "primary"
      ? `P · ${sat.name}`
      : role === "secondary"
        ? `S · ${sat.name}`
        : sat.name;

  return (
    <group position={pos}>
      <mesh
        onClick={handleClick}
        onPointerOver={() => {
          document.body.style.cursor = "pointer";
        }}
        onPointerOut={() => {
          document.body.style.cursor = "default";
        }}
      >
        <sphereGeometry args={[Math.max(size * 3.2, 0.05), 8, 8]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      <mesh ref={ref}>
        <sphereGeometry args={[size, 12, 12]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={highlighted ? 0.55 : 0.32}
          roughness={0.35}
        />
      </mesh>
      {highlighted && (
        <Html distanceFactor={8} style={{ pointerEvents: "none" }} zIndexRange={[100, 0]}>
          <div className={`sat-tag ${role ?? ""}`}>{label}</div>
        </Html>
      )}
    </group>
  );
}

function LinkLine({ a, b, risk }: { a: GeoMarker; b: GeoMarker; risk: string }) {
  const color =
    risk === "CRITICAL" ? "#e85d4c" : risk === "HIGH" ? "#e09b3d" : risk === "MEDIUM" ? "#d4a574" : "#7eb8a8";
  const points = useMemo(
    () =>
      [
        latLonToVec3(a.lat_deg, a.lon_deg, a.alt_km).toArray(),
        latLonToVec3(b.lat_deg, b.lon_deg, b.alt_km).toArray(),
      ] as [number, number, number][],
    [a, b],
  );
  return <Line points={points} color={color} lineWidth={2} />;
}

function FrameSelected({
  sats,
  controlsRef,
}: {
  sats: SkyTrafficSat[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  controlsRef: RefObject<any>;
}) {
  const { camera } = useThree();

  useEffect(() => {
    if (!sats.length || !controlsRef.current) return;
    const center = new THREE.Vector3();
    let maxR = 0;
    for (const sat of sats) {
      const pos = latLonToVec3(sat.lat_deg, sat.lon_deg, sat.alt_km);
      center.add(pos);
      maxR = Math.max(maxR, pos.length());
    }
    center.multiplyScalar(1 / sats.length);
    const need = Math.max(5.0, maxR * 2.35, center.length() + maxR);
    const dir = camera.position.clone().normalize();
    camera.position.copy(dir.multiplyScalar(need));
    controlsRef.current.target.copy(center.multiplyScalar(0.15));
    controlsRef.current.minDistance = 1.6;
    controlsRef.current.maxDistance = Math.max(22, need * 1.5);
    controlsRef.current.update();
  }, [sats, camera, controlsRef]);

  return null;
}

type GlobeSceneProps = {
  traffic: SkyTrafficSat[];
  selectedId: string | null;
  selectedTrack: GeoMarker[];
  primaryId: string | null;
  secondaryId: string | null;
  primaryTrack: GeoMarker[];
  secondaryTrack: GeoMarker[];
  result: AssessResponse | null;
  maneuver: ManeuverPlan | null;
  exaggeration: number;
  onSelect: (id: string) => void;
};

export default function GlobeScene({
  traffic,
  selectedId,
  selectedTrack,
  primaryId,
  secondaryId,
  primaryTrack,
  secondaryTrack,
  result,
  maneuver,
  exaggeration,
  onSelect,
}: GlobeSceneProps) {
  const controlsRef = useRef(null);
  const pairMode = Boolean(primaryId && secondaryId);

  const visible = useMemo(() => {
    if (!pairMode) return traffic;
    return traffic.filter((s) => s.id === primaryId || s.id === secondaryId);
  }, [traffic, pairMode, primaryId, secondaryId]);

  const focusSats = useMemo(() => {
    if (pairMode) return visible;
    const selected = traffic.find((s) => s.id === selectedId);
    return selected ? [selected] : [];
  }, [pairMode, visible, traffic, selectedId]);

  return (
    <Canvas
      className="globe-canvas"
      camera={{ position: [0, 1.1, 5.2], fov: 40, near: 0.1, far: 500 }}
      dpr={[1, 1.6]}
      onPointerMissed={() => onSelect("")}
    >
      <color attach="background" args={["#070b10"]} />
      <ambientLight intensity={0.5} />
      <directionalLight position={[5, 2.5, 3]} intensity={1.35} color="#fff4e6" />
      <directionalLight position={[-3, -1, -2]} intensity={0.35} color="#6a9eab" />
      <Stars radius={120} depth={50} count={3600} factor={3.2} saturation={0} fade speed={0.35} />
      <Earth />
      {visible.map((sat) => (
        <TrafficMarker
          key={sat.id}
          sat={sat}
          selected={sat.id === selectedId}
          role={
            sat.id === primaryId ? "primary" : sat.id === secondaryId ? "secondary" : null
          }
          onSelect={onSelect}
        />
      ))}
      {!pairMode && selectedTrack.length > 0 && (
        <OrbitPath points={selectedTrack} color={TRACK.primary} />
      )}
      {pairMode && primaryTrack.length > 0 && (
        <OrbitPath points={primaryTrack} color={TRACK.primary} />
      )}
      {pairMode && secondaryTrack.length > 0 && (
        <OrbitPath points={secondaryTrack} color={TRACK.secondary} />
      )}
      {maneuver && <ManeuverPaths plan={maneuver} exaggeration={exaggeration} />}
      {result?.primary && result?.secondary && (
        <LinkLine a={result.primary} b={result.secondary} risk={result.risk_level} />
      )}
      <FrameSelected sats={focusSats} controlsRef={controlsRef} />
      <OrbitControls
        ref={controlsRef}
        enablePan={false}
        minDistance={1.6}
        maxDistance={22}
        autoRotate={false}
      />
    </Canvas>
  );
}
