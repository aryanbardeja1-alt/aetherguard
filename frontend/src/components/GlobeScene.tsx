import { useMemo, useRef } from "react";
import { Canvas, ThreeEvent, useFrame } from "@react-three/fiber";
import { Html, Line, OrbitControls, Stars } from "@react-three/drei";
import * as THREE from "three";
import type { AssessResponse, GeoMarker, SkyTrafficSat } from "../api";

const EARTH_R = 1;

export function latLonToVec3(lat: number, lon: number, altKm: number, scale = EARTH_R): THREE.Vector3 {
  const radius = scale * (1 + Math.max(altKm, 150) / 6378.137);
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

function OrbitPath({ points, color }: { points: GeoMarker[]; color: string }) {
  const pathPoints = useMemo(() => {
    if (points.length < 2) return null;
    return points.map((p) => latLonToVec3(p.lat_deg, p.lon_deg, p.alt_km).toArray()) as [
      number,
      number,
      number,
    ][];
  }, [points]);

  if (!pathPoints) return null;
  return <Line points={pathPoints} color={color} lineWidth={1.4} transparent opacity={0.9} />;
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
      <mesh ref={ref} onClick={handleClick} onPointerOver={() => { document.body.style.cursor = "pointer"; }} onPointerOut={() => { document.body.style.cursor = "default"; }}>
        <sphereGeometry args={[selected ? 0.032 : 0.016, 12, 12]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={selected ? 0.55 : 0.25}
          roughness={0.35}
        />
      </mesh>
      {selected && (
        <Html distanceFactor={6} style={{ pointerEvents: "none" }}>
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
      camera={{ position: [0, 0.55, 2.7], fov: 42, near: 0.1, far: 100 }}
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
      <OrbitControls enablePan={false} minDistance={1.55} maxDistance={5.5} autoRotate={false} />
    </Canvas>
  );
}
