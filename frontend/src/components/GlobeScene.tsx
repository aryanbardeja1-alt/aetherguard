import { useEffect, useMemo, useRef } from "react";
import type { RefObject } from "react";
import { Canvas, ThreeEvent, useFrame, useThree } from "@react-three/fiber";
import { Html, Line, OrbitControls, Stars } from "@react-three/drei";
import * as THREE from "three";
import type { AssessResponse, GeoMarker, ManeuverPlan, SkyTrafficSat } from "../api";

const EARTH_R = 1;
const EARTH_KM = 6378.137;

/**
 * Compress true altitude so LEO / MEO / GEO / HEO all stay in a shared view
 * shell while still reading as "farther out" than LEO.
 */
export function displayAltitudeKm(altKm: number): number {
  const alt = Math.max(altKm, 120);
  if (alt <= 2000) return alt;
  if (alt <= 36000) {
    // MEO → GEO belt: map into ~0.3–1.6 R above the surface
    const t = (alt - 2000) / (36000 - 2000);
    return 2000 + t * 10000;
  }
  // HEO / science missions beyond GEO
  const t = Math.min(1, (alt - 36000) / 100000);
  return 12000 + t * 6000;
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
  switch (objectType) {
    case "station":
      return "#d4a574";
    case "visual":
      return "#7eb8a8";
    case "debris":
      return "#e09b3d";
    default:
      return "#9aa8b5";
  }
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
function ManeuverPaths({ plan }: { plan: ManeuverPlan }) {
  const burned = plan.delta_v_magnitude_m_s > 0;
  return (
    <group>
      <OrbitPath points={plan.baseline_track} color="#e85d4c" dashed opacity={burned ? 0.55 : 0.9} />
      {burned && <OrbitPath points={plan.maneuvered_track} color="#7eb8a8" lineWidth={2.4} />}
      {burned && plan.maneuvered_track.length > 0 && (
        <BurnMarker point={plan.maneuvered_track[0]} />
      )}
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
        <meshStandardMaterial color="#f0d98c" emissive="#f0d98c" emissiveIntensity={0.8} />
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
  onSelect,
}: {
  sat: SkyTrafficSat;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const ref = useRef<THREE.Mesh>(null);
  const color = typeColor(sat.object_type);
  const pos = latLonToVec3(sat.lat_deg, sat.lon_deg, sat.alt_km);
  const size = markerSize(sat.alt_km, selected);

  useFrame(({ clock }) => {
    if (!ref.current || !selected) return;
    ref.current.scale.setScalar(1 + Math.sin(clock.elapsedTime * 3.5) * 0.2);
  });

  const handleClick = (event: ThreeEvent<MouseEvent>) => {
    event.stopPropagation();
    onSelect(sat.id);
  };

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
          emissiveIntensity={selected ? 0.55 : 0.32}
          roughness={0.35}
        />
      </mesh>
      {selected && (
        <Html distanceFactor={8} style={{ pointerEvents: "none" }} zIndexRange={[100, 0]}>
          <div className="sat-tag">{sat.name}</div>
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
  sat,
  controlsRef,
}: {
  sat: SkyTrafficSat | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  controlsRef: RefObject<any>;
}) {
  const { camera } = useThree();

  useEffect(() => {
    if (!sat || !controlsRef.current) return;
    const pos = latLonToVec3(sat.lat_deg, sat.lon_deg, sat.alt_km);
    const need = Math.max(5.0, pos.length() * 2.35);
    const dir = camera.position.clone().normalize();
    camera.position.copy(dir.multiplyScalar(need));
    controlsRef.current.minDistance = 1.6;
    controlsRef.current.maxDistance = Math.max(22, need * 1.5);
    controlsRef.current.update();
  }, [sat, camera, controlsRef]);

  return null;
}

type GlobeSceneProps = {
  traffic: SkyTrafficSat[];
  selectedId: string | null;
  selectedTrack: GeoMarker[];
  result: AssessResponse | null;
  maneuver: ManeuverPlan | null;
  onSelect: (id: string) => void;
};

export default function GlobeScene({
  traffic,
  selectedId,
  selectedTrack,
  result,
  maneuver,
  onSelect,
}: GlobeSceneProps) {
  const controlsRef = useRef(null);
  const selected = traffic.find((s) => s.id === selectedId) ?? null;

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
      {traffic.map((sat) => (
        <TrafficMarker
          key={sat.id}
          sat={sat}
          selected={sat.id === selectedId}
          onSelect={onSelect}
        />
      ))}
      {selectedTrack.length > 0 && <OrbitPath points={selectedTrack} color="#d4a574" />}
      {maneuver && <ManeuverPaths plan={maneuver} />}
      {result?.primary && result?.secondary && (
        <LinkLine a={result.primary} b={result.secondary} risk={result.risk_level} />
      )}
      <FrameSelected sat={selected} controlsRef={controlsRef} />
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
