import React, { useMemo } from "react";

const PARTICLE_COUNT = 12;
const FIREFLY_COUNT = 3;

function randomParticle(index) {
  const isFirefly = index < FIREFLY_COUNT;
  return {
    id: index,
    isFirefly,
    left: `${Math.random() * 100}%`,
    top: `${Math.random() * 100}%`,
    size: isFirefly ? `${3 + Math.random() * 1.5}px` : `${1.5 + Math.random()}px`,
    duration: `${14 + Math.random() * 10}s`,
    delay: `${Math.random() * -20}s`,
    baseOpacity: isFirefly ? 0.35 + Math.random() * 0.15 : 0.15 + Math.random() * 0.1,
  };
}

// Twelve ambient dust/firefly motes for the Living Scenery backdrop --
// position/timing randomized once at mount (useMemo, not state) so the
// randomization never recomputes on re-render. Purely decorative, driven
// by CSS @keyframes only after mount (no per-frame JS).
export default function SceneryParticles() {
  const particles = useMemo(
    () => Array.from({ length: PARTICLE_COUNT }, (_, i) => randomParticle(i)),
    []
  );

  return (
    <>
      {particles.map((p) => (
        <span
          key={p.id}
          className={`scenery-particle ${p.isFirefly ? "scenery-particle--firefly" : "scenery-particle--dust"}`}
          style={{
            "--particle-left": p.left,
            "--particle-top": p.top,
            "--particle-size": p.size,
            "--particle-duration": p.duration,
            "--particle-delay": p.delay,
            "--particle-base-opacity": p.baseOpacity,
          }}
        />
      ))}
    </>
  );
}
