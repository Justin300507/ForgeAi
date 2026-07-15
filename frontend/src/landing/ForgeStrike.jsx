import React from "react";
import * as THREE from "three";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { STAGE_TEXTURES } from "../lib/forgeAssets";
import { deviceTier, dprCap, particleBudget, onVisible } from "../lib/perf";

gsap.registerPlugin(ScrollTrigger);

const SANS = { fontFamily: "Inter, system-ui, sans-serif" };

/* The mid-scroll centerpiece, fully procedural: a hammer of light strikes
   a wireframe blueprint and it resolves into the finished, glowing app.
   Every frame is a pure function of scroll progress, so scrubbing
   backwards un-forges the strike — something a pre-rendered video can
   only fake. Phases (p = pinned scroll progress 0→1):
     0.00–0.38  the light-hammer descends, tension builds
     0.38–0.46  impact: flash + radial spark burst
     0.46–1.00  sparks decay, blueprint crossfades to the finished app,
                calm glow settles, headline lands                         */

const IMPACT = 0.4;

function loadTexture(src, fallbackHue) {
  if (src) {
    const t = new THREE.TextureLoader().load(src);
    t.encoding = THREE.sRGBEncoding;
    return t;
  }
  // Procedural stand-in (same spirit as ForgeMorph's fallbacks).
  const c = document.createElement("canvas");
  c.width = 512; c.height = 640;
  const ctx = c.getContext("2d");
  ctx.strokeStyle = fallbackHue;
  ctx.lineWidth = 3;
  ctx.shadowColor = fallbackHue;
  ctx.shadowBlur = 16;
  ctx.strokeRect(90, 90, 332, 460);
  const t = new THREE.CanvasTexture(c);
  t.encoding = THREE.sRGBEncoding;
  return t;
}

const easeOutCubic = (x) => 1 - Math.pow(1 - x, 3);
const clamp01 = (x) => Math.min(1, Math.max(0, x));

export default function ForgeStrike() {
  const rootRef = React.useRef(null);
  const hostRef = React.useRef(null);
  const flashRef = React.useRef(null);
  const headlineRef = React.useRef(null);
  // Static on reduced-motion AND low-tier devices — same reasoning as
  // ForgeMorph: a pinned WebGL scrub is the wrong spend on a budget GPU.
  const staticMode = React.useMemo(
    () =>
      window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
      deviceTier() === "low",
    []
  );

  React.useEffect(() => {
    if (staticMode) return;
    const root = rootRef.current;
    const host = hostRef.current;
    if (!root || !host) return;

    // Tier-scaled: AA and full DPR only where the GPU can afford them.
    const renderer = new THREE.WebGLRenderer({
      antialias: deviceTier() === "high",
      alpha: true,
      powerPreference: "high-performance",
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, dprCap()));
    renderer.outputEncoding = THREE.sRGBEncoding;
    host.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 60);
    camera.position.z = 6.2;

    // ── The slab: blueprint wireframe → finished interface ──
    const texWire = loadTexture(STAGE_TEXTURES[1].src, "#8fd0ff"); // arch
    const texDone = loadTexture(STAGE_TEXTURES[7].src, "#7ee0b0"); // complete
    const slabMat = new THREE.ShaderMaterial({
      transparent: true,
      uniforms: {
        texA: { value: texWire },
        texB: { value: texDone },
        mixAmt: { value: 0 },
        brightness: { value: 1 },
      },
      vertexShader: `
        varying vec2 vUv;
        void main() {
          vUv = uv;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      // Luma-keyed alpha: these renders sit on pure black, so darkness IS
      // transparency — the slab floats without a rectangular photo edge.
      fragmentShader: `
        uniform sampler2D texA;
        uniform sampler2D texB;
        uniform float mixAmt;
        uniform float brightness;
        varying vec2 vUv;
        void main() {
          vec4 col = mix(texture2D(texA, vUv), texture2D(texB, vUv), mixAmt);
          float luma = dot(col.rgb, vec3(0.299, 0.587, 0.114));
          float alpha = smoothstep(0.04, 0.16, luma);
          gl_FragColor = vec4(col.rgb * brightness, alpha * col.a);
        }
      `,
    });
    const slab = new THREE.Mesh(new THREE.PlaneGeometry(2.6, 3.25), slabMat);
    slab.rotation.x = -0.16;
    scene.add(slab);

    // ── The hammer of light: a blazing vertical beam above the slab ──
    const beamMat = new THREE.MeshBasicMaterial({
      color: new THREE.Color("#ffd9a8"),
      transparent: true,
      opacity: 0.9,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const beam = new THREE.Mesh(new THREE.ConeGeometry(0.22, 2.6, 24), beamMat);
    beam.rotation.x = Math.PI; // point down
    beam.position.set(0, 4.6, 0.3);
    scene.add(beam);
    const beamGlow = new THREE.PointLight("#ffb066", 0, 14);
    beamGlow.position.set(0, 2.4, 1.4);
    scene.add(beamGlow);

    // ── Spark burst: directions fixed, radius is a function of scroll ──
    const COUNT = particleBudget(900);
    const dirs = new Float32Array(COUNT * 3);
    const speeds = new Float32Array(COUNT);
    for (let i = 0; i < COUNT; i++) {
      // Hemispherical burst biased outward/upward from the impact point.
      const theta = Math.random() * Math.PI * 2;
      const up = Math.random() * 0.9 + 0.1;
      const r = Math.sqrt(1 - up * up);
      dirs[i * 3] = Math.cos(theta) * r;
      dirs[i * 3 + 1] = up * (Math.random() > 0.25 ? 1 : -0.35);
      dirs[i * 3 + 2] = Math.sin(theta) * r * 0.6;
      speeds[i] = 0.35 + Math.random() * 0.65;
    }
    const sparkGeo = new THREE.BufferGeometry();
    sparkGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(COUNT * 3), 3));
    const sparkMat = new THREE.PointsMaterial({
      size: 0.045,
      color: new THREE.Color("#ffc37a"),
      transparent: true,
      opacity: 0,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const sparks = new THREE.Points(sparkGeo, sparkMat);
    sparks.position.set(0, 1.4, 0.2); // impact point: top edge of the slab
    scene.add(sparks);

    const resize = () => {
      const w = host.clientWidth;
      const h = host.clientHeight;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      const visibleH = 2 * Math.tan((camera.fov * Math.PI) / 360) * camera.position.z;
      const visibleW = visibleH * camera.aspect;
      const fit = Math.min(1, (visibleW * 0.62) / 2.6);
      slab.scale.setScalar(fit);
    };
    resize();
    window.addEventListener("resize", resize);

    // ── Everything below is a pure function of pinned progress p ──
    const state = { p: 0 };
    const applyProgress = (p) => {
      state.p = p;

      // Hammer: descends 0→IMPACT, then vanishes into the flash.
      const drop = clamp01(p / IMPACT);
      beam.position.y = 4.6 - easeOutCubic(drop) * 3.0; // lands at ~1.6
      beamMat.opacity = p < IMPACT ? 0.55 + drop * 0.45 : clamp01(1 - (p - IMPACT) * 14);
      beamGlow.intensity = drop * 2.2 * (p < IMPACT + 0.08 ? 1 : clamp01(1 - (p - IMPACT) * 6));

      // Flash: a sharp spike right at impact.
      const flashT = clamp01(1 - Math.abs(p - IMPACT) / 0.05);
      if (flashRef.current) flashRef.current.style.opacity = (flashT * 0.85).toFixed(3);

      // Sparks: radius/opacity keyed to post-impact progress.
      const burst = clamp01((p - IMPACT) / (1 - IMPACT));
      const radius = easeOutCubic(burst) * 5.2;
      const droop = burst * burst * 2.4;
      const arr = sparkGeo.attributes.position.array;
      for (let i = 0; i < COUNT; i++) {
        const s = speeds[i];
        arr[i * 3] = dirs[i * 3] * radius * s;
        arr[i * 3 + 1] = dirs[i * 3 + 1] * radius * s - droop * s;
        arr[i * 3 + 2] = dirs[i * 3 + 2] * radius * s;
      }
      sparkGeo.attributes.position.needsUpdate = true;
      sparkMat.opacity = burst === 0 ? 0 : clamp01(1.2 - burst * 1.2);

      // Slab: blueprint until impact, then resolves into the finished app.
      slabMat.uniforms.mixAmt.value = clamp01((p - 0.46) / 0.42);
      slabMat.uniforms.brightness.value = 1 + flashT * 1.6;
      slab.rotation.y = (p - 0.5) * 0.5;
      slab.rotation.x = -0.16 + clamp01((p - 0.46) / 0.5) * 0.16; // straightens

      // Headline: lands with the finished app.
      if (headlineRef.current) {
        const t = clamp01((p - 0.72) / 0.2);
        headlineRef.current.style.opacity = t.toFixed(3);
        headlineRef.current.style.transform = `translateY(${(1 - t) * 26}px)`;
      }
    };
    applyProgress(0);

    const st = ScrollTrigger.create({
      trigger: root,
      start: "top top",
      end: "+=1800",
      pin: true,
      scrub: 0.6,
      onUpdate: (self) => applyProgress(self.progress),
    });

    // Frame loop — runs only while the strike is on screen (it used to
    // render every frame for the whole life of the page).
    let raf;
    let running = false;
    const clock = new THREE.Clock();
    const tick = () => {
      if (!running) return;
      const t = clock.getElapsedTime();
      // Ambient life on top of the scrubbed state (subtle, additive only).
      slab.position.y = Math.sin(t * 0.7) * 0.04;
      beam.rotation.y = t * 0.6;
      renderer.render(scene, camera);
      raf = requestAnimationFrame(tick);
    };
    const stopVisibility = onVisible(root, (visible) => {
      if (visible && !running) {
        running = true;
        clock.start();
        raf = requestAnimationFrame(tick);
      } else if (!visible) {
        running = false;
        cancelAnimationFrame(raf);
      }
    });

    return () => {
      running = false;
      stopVisibility();
      cancelAnimationFrame(raf);
      st.kill();
      window.removeEventListener("resize", resize);
      [texWire, texDone].forEach((t) => t.dispose());
      slabMat.dispose();
      slab.geometry.dispose();
      beamMat.dispose();
      beam.geometry.dispose();
      sparkGeo.dispose();
      sparkMat.dispose();
      renderer.dispose();
      host.removeChild(renderer.domElement);
    };
  }, [staticMode]);

  // Static mode: the before/after of the strike as two calm frames.
  if (staticMode) {
    return (
      <section className="forge-section" aria-label="The forge strike" style={SANS}>
        <div className="max-w-4xl mx-auto text-center">
          <div className="grid grid-cols-2 gap-6 mb-8">
            {[STAGE_TEXTURES[1], STAGE_TEXTURES[7]].map((t, i) =>
              t.src ? (
                <img key={i} src={t.src} alt="" className="rounded-2xl w-full" />
              ) : (
                <div key={i} className="rounded-2xl aspect-[4/5] bg-[#0e0a1a]" />
              )
            )}
          </div>
          <h2 className="hero-serif text-3xl sm:text-4xl text-white">
            This is what forging software looks like.
          </h2>
        </div>
      </section>
    );
  }

  return (
    <section
      ref={rootRef}
      className="relative h-screen overflow-hidden bg-[#0b0813]"
      aria-label="The forge strike: a hammer of light resolving a blueprint into a finished app"
    >
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(55% 60% at 50% 60%, rgba(255,138,61,0.14), transparent 72%)",
        }}
      />
      <div ref={hostRef} className="absolute inset-0" aria-hidden="true" />
      {/* Impact flash */}
      <div
        ref={flashRef}
        className="absolute inset-0 pointer-events-none"
        style={{
          opacity: 0,
          background:
            "radial-gradient(60% 55% at 50% 42%, rgba(255,235,205,0.95), rgba(255,138,61,0.35) 55%, transparent 78%)",
        }}
      />
      <div
        ref={headlineRef}
        className="absolute inset-x-0 bottom-16 text-center px-4 pointer-events-none"
        style={{ ...SANS, opacity: 0 }}
      >
        <h2 className="hero-serif text-3xl sm:text-5xl text-white">
          This is what forging software looks like.
        </h2>
      </div>
    </section>
  );
}
