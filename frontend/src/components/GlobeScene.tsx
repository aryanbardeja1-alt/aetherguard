import { useMemo, useRef } from "react";
import { Canvas, ThreeEvent, useFrame } from "@react-three/fiber";
import { Html, Line, OrbitControls, Stars } from "@react-three/drei";
import * as THREE from "three";
import type { AssessResponse, GeoMarker, SkyTrafficSat } from "../api";

const EARTH_R = 1;
const EARTH_KM = 6378.137;

/**
 * Compress true altitude so LEO / MEO / GEO / HEO all stay in camera range.
 * Without this, GEO (~36e3 km) and HEO (>1e5 km) render far off-screen and
 * look like they "failed to load".
 */
export function displayAltitudeKm(altKm: number): number {
  const alt = Math.max(altKm, 120);
  if (alt <= 2000) return alt;
  if (alt <= 40000) {
    // MEO → GEO: map 2e3–40e3 into 2e3–9000 visual km (~1.3–2.4 R)
    const t = (alt - 2000) / (40000 - 2000);
    return 2000 + t * 7000;
  }
  // Deep HEO / science: soft log so Cluster/CXO stay near ~2.8 R
  return 9000 + Math.log10(1 + (alt - 40000) / 20000) * 4000;
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
  const base = altKm > 10000 ? 0.024 : 0.016;
  return selected ? base * 1.85 : base;
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
    </group>
  );
}

/** Split orbit polylines when they wrap the antimeridian so lines don't streak across Earth. */
function OrbitPath({ points, color }: { points: GeoMarker[]; color: string }) {
  const segments = useMemo(() => {
    if (points.length < 2) return [];
    const segs: [number, number, number][][] = [];
    let current: [number, number, number][] = [];
    let prevLon: number | null = null;

    for (const p of points) {
      if (!Number.isFinite(p.lat_deg) || !Number.isFinite(p.lon_deg)) continue;
      if (prevLon !== null && Math.abs(p.lon_deg - prevLon) > 180) {
        if (current.length >= 2) segs.push(current);
        current = [];
      }
      current.push(latLonToVec3(p.lat_deg, p.lon_deg, p.alt_km).toArray() as [number, number, number]);
      prevLon = p.lon_deg;
    }
    if (current.length >= 2) segs.push(current);
    return segs;
  }, [points]);

  return (
    <>
      {segments.map((pts, i) => (
        <Line key={i} points={pts} color={color} lineWidth={1.4} transparent opacity={0.9} />
      ))}
    </>
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
      {/* Larger invisible hit target so crowded LEO sats stay clickable */}
      <mesh
        onClick={handleClick}
        onPointerOver={() => {
          document.body.style.cursor = "pointer";
        }}
        onPointerOut={() => {
          document.body.style.cursor = "default";
        }}
      >
        <sphereGeometry args={[Math.max(size * 2.8, 0.04), 8, 8]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      <mesh ref={ref}>
        <sphereGeometry args={[size, 12, 12]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={selected ? 0.55 : 0.28}
          roughness={0.35}
        />
      </mesh>
      {selected && (
        <Html distanceFactor={7} style={{ pointerEvents: "none" }}>
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

type GlobeSceneProps = {
  traffic: SkyTrafficSat[];
  selectedId: string | null;
  selectedTrack: GeoMarker[];
  result: AssessResponse | null;
  onSelect: (id: string) => void;
};

export default function GlobeScene({
  traffic,
  selectedId,
  selectedTrack,
  result,
  onSelect,
}: GlobeSceneProps) {
  return (
    <Canvas
      className="globe-canvas"
      camera={{ position: [0, 0.7, 3.2], fov: 42, near: 0.1, far: 200 }}
      dpr={[1, 1.6]}
      onPointerMissed={() => onSelect("")}
    >
      <color attach="background" args={["#070b10"]} />
      <ambientLight intensity={0.45} />
      <directionalLight position={[4, 2, 3]} intensity={1.35} color="#fff4e6" />
      <directionalLight position={[-3, -1, -2]} intensity={0.35} color="#6a9eab" />
      <Stars radius={80} depth={40} count={3200} factor={3.1} saturation={0} fade speed={0.35} />
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
      {result?.primary && result?.secondary && (
        <LinkLine a={result.primary} b={result.secondary} risk={result.risk_level} />
      )}
      <OrbitControls enablePan={false} minDistance={1.55} maxDistance={12} autoRotate={false} />
    </Canvas>
  );
}
