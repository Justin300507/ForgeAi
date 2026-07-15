import React from "react";
import * as THREE from "three";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { STAGES } from "../lib/pipelineStages";
import { STAGE_TEXTURES, STAGE_COLORS, STAGE_COPY } from "../lib/forgeAssets";
import { deviceTier, dprCap, particleBudget, onVisible } from "../lib/perf";

gsap.registerPlugin(ScrollTrigger);

const SANS = { fontFamily: "Inter, system-ui, sans-serif" };

// One scroll-screen per stage — the pin holds while the object forges.
const SCROLL_PER_STAGE = 620;

/* Procedural stand-in for a stage texture while its Higgsfield render is
   not wired yet: the consistent silhouette (a rounded app-slab wireframe)
   lit in the stage's accent color, so the morph reads correctly even
   before final art lands. */
function makeFallbackTexture(stageIndex) {
  const { accent } = STAGE_COLORS[stageIndex];
  const c = document.createElement("canvas");
  c.width = 640;
  c.height = 800;
  const ctx = c.getContext("2d");

  const glow = ctx.createRadialGradient(320, 380, 40, 320, 400, 460);
  glow.addColorStop(0, `${accent}55`);
  glow.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, 640, 800);

  // The one silhouette all eight states share: an app slab, portrait.
  const x = 150, y = 170, w = 340, h = 460, r = 26;
  const slab = () => {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  };

  // Later stages fill in more of the slab — spark → lattice → skinned.
  const solidity = stageIndex / (STAGE_COLORS.length - 1);
  slab();
  ctx.fillStyle = `rgba(18, 14, 30, ${0.25 + solidity * 0.6})`;
  ctx.fill();
  slab();
  ctx.strokeStyle = accent;
  ctx.lineWidth = 2.5;
  ctx.shadowColor = accent;
  ctx.shadowBlur = 18;
  ctx.stroke();
  ctx.shadowBlur = 0;

  // Interior detail scales with stage: blueprint lines early, UI bars late.
  ctx.strokeStyle = `${accent}88`;
  ctx.lineWidth = 1.25;
  const rows = 2 + stageIndex;
  for (let i = 1; i <= rows; i++) {
    const yy = y + (h / (rows + 1)) * i;
    ctx.beginPath();
    ctx.moveTo(x + 28, yy);
    ctx.lineTo(x + w - 28, yy);
    ctx.stroke();
  }
  if (stageIndex === 0) {
    // Planning: mostly a spark, barely a slab.
    ctx.clearRect(0, 0, 640, 800);
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, 640, 800);
    const spark = ctx.createRadialGradient(320, 400, 4, 320, 400, 120);
    spark.addColorStop(0, "#ffffff");
    spark.addColorStop(0.25, accent);
    spark.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = spark;
    ctx.fillRect(0, 0, 640, 800);
  }

  const tex = new THREE.CanvasTexture(c);
  tex.encoding = THREE.sRGBEncoding;
  return tex;
}

function loadStageTextures() {
  const loader = new THREE.TextureLoader();
  loader.setCrossOrigin("anonymous");
  return STAGE_TEXTURES.map((entry, i) => {
    if (!entry.src) return makeFallbackTexture(i);
    const tex = loader.load(entry.src);
    tex.encoding = THREE.sRGBEncoding;
    return tex;
  });
}

/* Static fallback — served to reduced-motion users AND low-tier devices
   (budget phones/laptops, where even a paused-offscreen pinned WebGL
   scrub can't hold frame rate): the eight states as static frames —
   no pin, no parallax, no cursor work. */
function StaticStages() {
  return (
    <section className="forge-section" aria-label="The forge pipeline">
      <div className="max-w-5xl mx-auto">
        <p className="forge-eyebrow forge-mono mb-3">The forge</p>
        <h2 className="hero-serif text-4xl sm:text-5xl text-white mb-12">
          Watch an idea take shape.
        </h2>
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-6">
          {STAGES.map((s, i) => (
            <figure key={s.id} className="glass-panel rounded-2xl p-4">
              <div
                className="rounded-xl h-44 mb-4"
                style={{
                  background: `radial-gradient(60% 60% at 50% 45%, ${STAGE_COLORS[i].glow}, transparent 75%), rgba(12,9,20,0.8)`,
                }}
              >
                {STAGE_TEXTURES[i].src && (
                  <img
                    src={STAGE_TEXTURES[i].src}
                    alt=""
                    className="w-full h-full object-cover rounded-xl"
                  />
                )}
              </div>
              <figcaption style={SANS}>
                <span className="forge-mono text-[10px]" style={{ color: STAGE_COLORS[i].accent }}>
                  {String(i + 1).padStart(2, "0")}
                </span>
                <p className="text-white text-sm font-medium mt-1">{s.label}</p>
                <p className="text-white/50 text-xs mt-1 leading-relaxed">{STAGE_COPY[s.id]}</p>
              </figcaption>
            </figure>
          ))}
        </div>
      </div>
    </section>
  );
}

export default function ForgeMorph() {
  const rootRef = React.useRef(null);
  const canvasHostRef = React.useRef(null);
  const bgRef = React.useRef(null);
  const gridRef = React.useRef(null);
  const [stage, setStage] = React.useState(0);
  const staticMode = React.useMemo(
    () =>
      window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
      deviceTier() === "low",
    []
  );

  React.useEffect(() => {
    if (staticMode) return;
    const host = canvasHostRef.current;
    const root = rootRef.current;
    if (!host || !root) return;

    // ── Scene ──
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
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 60);
    camera.position.z = 5.6;

    const textures = loadStageTextures();

    // One object, eight material states: a slab whose front face
    // crossfades between consecutive stage renders as scroll advances.
    const material = new THREE.ShaderMaterial({
      transparent: true,
      uniforms: {
        texA: { value: textures[0] },
        texB: { value: textures[1] },
        mixAmt: { value: 0 },
      },
      vertexShader: `
        varying vec2 vUv;
        void main() {
          vUv = uv;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      // Luma-keyed alpha: the stage renders sit on pure black, so darkness
      // IS transparency — the object floats free of any rectangular edge.
      fragmentShader: `
        uniform sampler2D texA;
        uniform sampler2D texB;
        uniform float mixAmt;
        varying vec2 vUv;
        void main() {
          vec4 col = mix(texture2D(texA, vUv), texture2D(texB, vUv), mixAmt);
          float luma = dot(col.rgb, vec3(0.299, 0.587, 0.114));
          float alpha = smoothstep(0.04, 0.16, luma);
          gl_FragColor = vec4(col.rgb, alpha * col.a);
        }
      `,
    });
    const slab = new THREE.Mesh(new THREE.PlaneGeometry(2.7, 3.375), material);
    scene.add(slab);

    // Ember/spark particles in a shallow volume behind + around the slab.
    // They drift upward slowly and shy away from the cursor.
    const COUNT = particleBudget(220);
    const pos = new Float32Array(COUNT * 3);
    const base = new Float32Array(COUNT * 3);
    for (let i = 0; i < COUNT; i++) {
      base[i * 3] = pos[i * 3] = (Math.random() - 0.5) * 9;
      base[i * 3 + 1] = pos[i * 3 + 1] = (Math.random() - 0.5) * 6;
      base[i * 3 + 2] = pos[i * 3 + 2] = -1.5 - Math.random() * 3;
    }
    const pGeo = new THREE.BufferGeometry();
    pGeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const pMat = new THREE.PointsMaterial({
      size: 0.035,
      color: new THREE.Color("#ffd9a8"),
      transparent: true,
      opacity: 0.55,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const particles = new THREE.Points(pGeo, pMat);
    scene.add(particles);

    const accentColors = STAGE_COLORS.map((s) => new THREE.Color(s.accent));

    // ── Sizing ──
    const resize = () => {
      const w = host.clientWidth;
      const h = host.clientHeight;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      // Fit the slab inside the frustum on narrow viewports: shrink it so
      // its width (plus rotation swing) never crops off-screen on mobile.
      const visibleH = 2 * Math.tan((camera.fov * Math.PI) / 360) * camera.position.z;
      const visibleW = visibleH * camera.aspect;
      const fit = Math.min(1, (visibleW * 0.68) / 2.7, (visibleH * 0.72) / 3.375);
      slab.scale.setScalar(fit);
    };
    resize();
    window.addEventListener("resize", resize);

    // ── Cursor: subtle follow on rotation + light, particle repulsion ──
    const mouse = { x: 0, y: 0, lx: 0, ly: 0 };
    const onMove = (e) => {
      mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
      mouse.y = -((e.clientY / window.innerHeight) * 2 - 1);
    };
    window.addEventListener("pointermove", onMove, { passive: true });

    // ── Scroll scrub: progress 0→1 across the pinned span drives the
    // stage index, texture crossfade, rotation, and background accent. ──
    const state = { progress: 0, stage: 0 };
    const st = ScrollTrigger.create({
      trigger: root,
      start: "top top",
      end: `+=${SCROLL_PER_STAGE * (STAGES.length - 1)}`,
      pin: true,
      scrub: 0.8,
      onUpdate: (self) => {
        state.progress = self.progress;
        const s = self.progress * (STAGES.length - 1);
        const idx = Math.min(Math.floor(s), STAGES.length - 2);
        material.uniforms.texA.value = textures[idx];
        material.uniforms.texB.value = textures[idx + 1];
        material.uniforms.mixAmt.value = s - idx;

        const landed = Math.round(s);
        if (landed !== state.stage) {
          state.stage = landed;
          setStage(landed);
          const { accent, glow } = STAGE_COLORS[landed];
          if (bgRef.current) {
            bgRef.current.style.setProperty("--forge-glow", glow);
            root.style.setProperty("--forge-accent", accent);
            root.style.setProperty("--forge-glow", glow);
          }
        }
        // Blueprint grid drifts against scroll for depth.
        if (gridRef.current) {
          gridRef.current.style.transform = `translateY(${self.progress * -60}px)`;
        }
      },
    });

    // ── Frame loop — runs only while the section is on screen. This
    // renderer used to burn a full rAF + render pass for the entire life
    // of the page, even under the hero or past the footer. ──
    let raf;
    let running = false;
    const clock = new THREE.Clock();
    const tick = () => {
      if (!running) return;
      const t = clock.getElapsedTime();
      mouse.lx += (mouse.x - mouse.lx) * 0.06;
      mouse.ly += (mouse.y - mouse.ly) * 0.06;

      // Slow vertical-axis rotation across the whole forging, plus a
      // gentle cursor-follow tilt — the object turns as it transforms.
      slab.rotation.y = state.progress * 1.15 - 0.28 + mouse.lx * 0.22;
      slab.rotation.x = mouse.ly * -0.1;
      slab.position.y = Math.sin(t * 0.8) * 0.05;

      // Ember color eases toward the live stage accent.
      pMat.color.lerp(accentColors[state.stage], 0.04);

      // Particles: slow rise + cursor repulsion (in NDC-ish space).
      const arr = pGeo.attributes.position.array;
      for (let i = 0; i < COUNT; i++) {
        const ix = i * 3;
        let ny = arr[ix + 1] + 0.0035 + (i % 5) * 0.0006;
        if (ny > 3.2) ny = -3.2;
        const dx = arr[ix] - mouse.lx * 4.2;
        const dy = ny - mouse.ly * 2.8;
        const d2 = dx * dx + dy * dy;
        if (d2 < 1.1) {
          const push = (1.1 - d2) * 0.045;
          arr[ix] += dx * push;
          ny += dy * push;
        } else {
          arr[ix] += (base[ix] - arr[ix]) * 0.01;
        }
        arr[ix + 1] = ny;
      }
      pGeo.attributes.position.needsUpdate = true;

      // Parallax: particle field counter-moves slightly vs. the slab.
      particles.rotation.y = mouse.lx * -0.06;

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
      window.removeEventListener("pointermove", onMove);
      textures.forEach((t) => t.dispose());
      material.dispose();
      slab.geometry.dispose();
      pGeo.dispose();
      pMat.dispose();
      renderer.dispose();
      host.removeChild(renderer.domElement);
    };
  }, [staticMode]);

  if (staticMode) return <StaticStages />;

  const current = STAGES[stage];

  return (
    <section
      ref={rootRef}
      className="relative h-screen overflow-hidden"
      style={{ "--forge-accent": STAGE_COLORS[0].accent, "--forge-glow": STAGE_COLORS[0].glow }}
      aria-label="The forge: an idea transforming into a deployed app as you scroll"
    >
      <div ref={bgRef} className="forge-stage-bg" />
      <div ref={gridRef} className="forge-grid-layer" />

      {/* The forge object */}
      <div ref={canvasHostRef} className="absolute inset-0" aria-hidden="true" />

      {/* Stage readout — bottom-left, syncs to the landed stage */}
      <div
        className="absolute bottom-10 left-6 sm:left-12 max-w-sm pointer-events-none"
        style={SANS}
      >
        <p className="forge-eyebrow forge-mono mb-2">
          The forge · {String(stage + 1).padStart(2, "0")} / {String(STAGES.length).padStart(2, "0")}
        </p>
        <h3
          key={current.id}
          className="hero-serif text-4xl sm:text-5xl anim-fade-up"
          style={{ color: "white", "--d": "0ms" }}
        >
          {current.label}
        </h3>
        <p key={`${current.id}-copy`} className="text-white/60 text-sm mt-3 leading-relaxed anim-fade-up" style={{ "--d": "80ms" }}>
          {STAGE_COPY[current.id]}
        </p>
      </div>

      {/* Section intro — top-center, quiet */}
      <div className="absolute top-14 inset-x-0 text-center pointer-events-none px-4" style={SANS}>
        <p className="forge-eyebrow forge-mono mb-2">Scroll to forge</p>
        <h2 className="hero-serif text-2xl sm:text-3xl text-white/85">
          Watch an idea take shape.
        </h2>
      </div>

      {/* Progress rail — right edge */}
      <div className="absolute right-6 sm:right-10 top-1/2 -translate-y-1/2 flex flex-col gap-3">
        {STAGES.map((s, i) => (
          <span key={s.id} className={`forge-rail-node ${i <= stage ? "lit" : ""}`} />
        ))}
      </div>
    </section>
  );
}
