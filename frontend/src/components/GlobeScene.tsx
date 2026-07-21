import { useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Line, OrbitControls, Stars } from "@react-three/drei";
import * as THREE from "three";
import type { AssessResponse, GeoMarker } from "../api";

const EARTH_R = 1;

function latLonToVec3(lat: number, lon: number, altKm: number, scale = EARTH_R): THREE.Vector3 {
  const radius = scale * (1 + altKm / 6378.137);
  const phi = THREE.MathUtils.degToRad(90 - lat);
  const theta = THREE.MathUtils.degToRad(lon + 180);
  return new THREE.Vector3(
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta),
  );
}

function Earth() {
  const earthRef = useRef<THREE.Mesh>(null);
  const cloudRef = useRef<THREE.Mesh>(null);

  useFrame((_, delta) => {
    if (earthRef.current) earthRef.current.rotation.y += delta * 0.028;
    if (cloudRef.current) cloudRef.current.rotation.y += delta * 0.034;
  });

  return (
    <group>
      <mesh ref={earthRef}>
        <sphereGeometry args={[EARTH_R, 64, 64]} />
        <meshStandardMaterial
          color="#1c4a5c"
          roughness={0.82}
          metalness={0.12}
          emissive="#0a1c24"
          emissiveIntensity={0.15}
        />
      </mesh>
      <mesh ref={cloudRef} scale={1.012}>
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
        <meshBasicMaterial color="#d4a574" wireframe transparent opacity={0.045} />
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
    return points.map((p) =>
      latLonToVec3(p.lat_deg, p.lon_deg, Math.max(p.alt_km, 200)).toArray(),
    ) as [number, number, number][];
  }, [points]);

  if (!pathPoints) return null;
  return <Line points={pathPoints} color={color} lineWidth={1.25} transparent opacity={0.85} />;
}

function SatMarker({
  marker,
  color,
  pulse,
}: {
  marker: GeoMarker;
  color: string;
  pulse?: boolean;
}) {
  const ref = useRef<THREE.Mesh>(null);
  const pos = latLonToVec3(marker.lat_deg, marker.lon_deg, Math.max(marker.alt_km, 200));

  useFrame(({ clock }) => {
    if (!ref.current || !pulse) return;
    const s = 1 + Math.sin(clock.elapsedTime * 3.2) * 0.22;
    ref.current.scale.setScalar(s);
  });

  return (
    <group position={pos}>
      <mesh ref={ref}>
        <sphereGeometry args={[0.028, 16, 16]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.35} roughness={0.4} />
      </mesh>
      <mesh>
        <sphereGeometry args={[0.05, 12, 12]} />
        <meshBasicMaterial color={color} transparent opacity={0.18} depthWrite={false} />
      </mesh>
    </group>
  );
}

function LinkLine({ a, b, risk }: { a: GeoMarker; b: GeoMarker; risk: string }) {
  const color =
    risk === "CRITICAL" ? "#e85d4c" : risk === "HIGH" ? "#e09b3d" : risk === "MEDIUM" ? "#d4a574" : "#7eb8a8";
  const points = useMemo(
    () =>
      [
        latLonToVec3(a.lat_deg, a.lon_deg, Math.max(a.alt_km, 200)).toArray(),
        latLonToVec3(b.lat_deg, b.lon_deg, Math.max(b.alt_km, 200)).toArray(),
      ] as [number, number, number][],
    [a, b],
  );
  return <Line points={points} color={color} lineWidth={2} />;
}

type GlobeSceneProps = {
  result: AssessResponse | null;
  primaryTrack: GeoMarker[];
  secondaryTrack: GeoMarker[];
};

export default function GlobeScene({ result, primaryTrack, secondaryTrack }: GlobeSceneProps) {
  return (
    <Canvas
      className="globe-canvas"
      camera={{ position: [0, 0.6, 2.65], fov: 42, near: 0.1, far: 100 }}
      dpr={[1, 1.75]}
    >
      <color attach="background" args={["#070b10"]} />
      <ambientLight intensity={0.45} />
      <directionalLight position={[4, 2, 3]} intensity={1.35} color="#fff4e6" />
      <directionalLight position={[-3, -1, -2]} intensity={0.35} color="#6a9eab" />
      <Stars radius={80} depth={40} count={3500} factor={3.2} saturation={0} fade speed={0.4} />
      <Earth />
      {primaryTrack.length > 0 && <OrbitPath points={primaryTrack} color="#d4a574" />}
      {secondaryTrack.length > 0 && <OrbitPath points={secondaryTrack} color="#7eb8a8" />}
      {result?.primary && (
        <SatMarker marker={result.primary} color="#d4a574" pulse={result.action_required} />
      )}
      {result?.secondary && <SatMarker marker={result.secondary} color="#7eb8a8" />}
      {result?.primary && result?.secondary && (
        <LinkLine a={result.primary} b={result.secondary} risk={result.risk_level} />
      )}
      <OrbitControls
        enablePan={false}
        minDistance={1.6}
        maxDistance={5}
        autoRotate={!result}
        autoRotateSpeed={0.35}
      />
    </Canvas>
  );
}
