import React from "react";
import { motion } from "framer-motion";
import { STAGES } from "../lib/pipelineStages";
import { STAGE_COLORS, STAGE_COPY, EXAMPLE_MOCKUPS, BG_TEXTURES } from "../lib/forgeAssets";

const SANS = { fontFamily: "Inter, system-ui, sans-serif" };

const rise = {
  initial: { opacity: 0, y: 28 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: "-80px" },
  transition: { duration: 0.7, ease: [0.16, 1, 0.3, 1] },
};

/* ── The problem with boilerplate — quiet, text-first ─────────────────── */
export function ProblemSection() {
  return (
    <section className="forge-section" style={SANS}>
      <div className="max-w-3xl mx-auto text-center">
        <motion.p {...rise} className="forge-eyebrow forge-mono mb-4">
          The problem
        </motion.p>
        <motion.h2
          {...rise}
          transition={{ ...rise.transition, delay: 0.08 }}
          className="hero-serif text-4xl sm:text-6xl text-white leading-[1.12]"
        >
          Every app begins with the same thousand lines.
        </motion.h2>
        <motion.p
          {...rise}
          transition={{ ...rise.transition, delay: 0.16 }}
          className="text-white/55 text-base sm:text-lg leading-relaxed mt-7 max-w-xl mx-auto"
        >
          Auth. Models. Routes. Migrations. CRUD. Weeks of scaffolding before
          the first interesting decision. ForgeAI starts on the other side of
          all of it — you describe the product, the forge does the boilerplate.
        </motion.p>
      </div>
    </section>
  );
}

/* ── How the pipeline works — the real stages, plainly ────────────────── */
export function PipelineSection() {
  return (
    <section
      className="forge-section"
      style={{
        ...SANS,
        ...(BG_TEXTURES.blueprintGrid
          ? {
              backgroundImage: `linear-gradient(rgba(11,8,19,0.92), rgba(11,8,19,0.92)), url(${BG_TEXTURES.blueprintGrid})`,
              backgroundSize: "cover",
              backgroundPosition: "center",
            }
          : {}),
      }}
    >
      <div className="max-w-5xl mx-auto">
        <motion.p {...rise} className="forge-eyebrow forge-mono mb-4">
          How it works
        </motion.p>
        <motion.h2
          {...rise}
          transition={{ ...rise.transition, delay: 0.08 }}
          className="hero-serif text-4xl sm:text-5xl text-white mb-14"
        >
          One pipeline, eight honest stages.
        </motion.h2>
        <ol className="grid sm:grid-cols-2 gap-x-10 gap-y-8">
          {STAGES.map((s, i) => (
            <motion.li
              key={s.id}
              {...rise}
              transition={{ ...rise.transition, delay: 0.05 * i }}
              className="flex gap-4 items-start"
            >
              <span
                className="forge-mono text-xs mt-1.5 shrink-0"
                style={{ color: STAGE_COLORS[i].accent }}
              >
                {String(i + 1).padStart(2, "0")}
              </span>
              <div>
                <div className="flex items-center gap-2.5">
                  <s.Icon size={15} style={{ color: STAGE_COLORS[i].accent }} aria-hidden="true" />
                  <h3 className="text-white text-base font-medium">{s.label}</h3>
                </div>
                <p className="text-white/50 text-sm leading-relaxed mt-1.5">
                  {STAGE_COPY[s.id]}
                </p>
              </div>
            </motion.li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/* ── Shipped examples — the three real deployed apps, 3D tilt cards ───── */
function TiltCard({ mockup, delay }) {
  const ref = React.useRef(null);
  const [tilt, setTilt] = React.useState({ x: 0, y: 0 });
  const reduced = React.useMemo(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    []
  );

  const onMove = (e) => {
    if (reduced) return;
    const r = ref.current.getBoundingClientRect();
    setTilt({
      x: ((e.clientY - r.top) / r.height - 0.5) * -10,
      y: ((e.clientX - r.left) / r.width - 0.5) * 12,
    });
  };

  return (
    <motion.div
      {...rise}
      transition={{ ...rise.transition, delay }}
      className="forge-tilt-space"
    >
      <div
        ref={ref}
        onPointerMove={onMove}
        onPointerLeave={() => setTilt({ x: 0, y: 0 })}
        className="forge-tilt-card glass-panel rounded-2xl p-5 h-full"
        style={{
          transform: `rotateX(${tilt.x}deg) rotateY(${tilt.y}deg)`,
          transition: "transform 300ms cubic-bezier(0.16, 1, 0.3, 1)",
        }}
      >
        <div className="rounded-xl aspect-[4/3] overflow-hidden bg-[#0e0a1a]">
          {mockup.src ? (
            <img
              src={mockup.src}
              alt={`${mockup.name} interface, generated and deployed by ForgeAI`}
              loading="lazy"
              className="w-full h-full object-cover"
            />
          ) : (
            <div
              aria-hidden="true"
              className="w-full h-full"
              style={{
                background:
                  "radial-gradient(70% 60% at 50% 40%, rgba(124,58,237,0.25), transparent 75%), #0e0a1a",
              }}
            />
          )}
        </div>
        <h3 className="text-white text-base font-medium mt-4">{mockup.name}</h3>
        <p className="text-white/45 text-xs mt-1">
          Forged from one sentence. Live in production.
        </p>
      </div>
    </motion.div>
  );
}

export function ExamplesSection() {
  return (
    <section className="forge-section" style={SANS}>
      <div className="max-w-5xl mx-auto">
        <motion.p {...rise} className="forge-eyebrow forge-mono mb-4">
          Shipped
        </motion.p>
        <motion.h2
          {...rise}
          transition={{ ...rise.transition, delay: 0.08 }}
          className="hero-serif text-4xl sm:text-5xl text-white mb-12"
        >
          Real apps, already forged.
        </motion.h2>
        <div className="grid sm:grid-cols-3 gap-6">
          {EXAMPLE_MOCKUPS.map((m, i) => (
            <TiltCard key={m.id} mockup={m} delay={0.08 * i} />
          ))}
        </div>
      </div>
    </section>
  );
}

/* ── Speed & cost — only what's actually true of the product ──────────── */
const STATS = [
  { value: "3–5 min", label: "from sentence to deployed app" },
  { value: "~15k", label: "tokens per full pipeline run" },
  { value: "≈ $0", label: "cost per generation" },
];

export function StatsSection() {
  return (
    <section
      className="forge-section"
      style={{
        ...SANS,
        ...(BG_TEXTURES.moltenLight
          ? {
              backgroundImage: `linear-gradient(rgba(11,8,19,0.88), rgba(11,8,19,0.88)), url(${BG_TEXTURES.moltenLight})`,
              backgroundSize: "cover",
              backgroundPosition: "center",
            }
          : {}),
      }}
    >
      <div className="max-w-4xl mx-auto grid sm:grid-cols-3 gap-10 text-center">
        {STATS.map((s, i) => (
          <motion.div key={s.label} {...rise} transition={{ ...rise.transition, delay: 0.08 * i }}>
            <p className="hero-serif stat-value text-5xl sm:text-6xl">{s.value}</p>
            <p className="text-white/50 text-sm mt-3">{s.label}</p>
          </motion.div>
        ))}
      </div>
    </section>
  );
}

/* ── Closing CTA + footer ─────────────────────────────────────────────── */
export function CtaFooter({ onForge }) {
  const [idea, setIdea] = React.useState("");
  return (
    <footer className="forge-section pb-12" style={SANS}>
      <div className="max-w-2xl mx-auto text-center">
        <motion.h2 {...rise} className="hero-serif text-4xl sm:text-6xl text-white leading-[1.12]">
          Your turn at the anvil.
        </motion.h2>
        <motion.form
          {...rise}
          transition={{ ...rise.transition, delay: 0.1 }}
          onSubmit={(e) => {
            e.preventDefault();
            onForge(idea.trim());
          }}
          className="liquid-glass glow-focus rounded-full flex items-center gap-2 p-1.5 pl-5 mt-9 w-full max-w-md mx-auto"
        >
          <input
            type="text"
            value={idea}
            onChange={(e) => setIdea(e.target.value)}
            placeholder="Describe the app you imagine…"
            aria-label="Describe the app you want to build"
            className="flex-1 min-w-0 bg-transparent outline-none text-sm text-white placeholder-white/40"
          />
          <button
            type="submit"
            className="bg-white text-slate-900 text-sm font-medium px-5 py-2 rounded-full whitespace-nowrap hover:bg-white/90 transition-colors"
          >
            Forge It
          </button>
        </motion.form>
      </div>
      <div className="max-w-5xl mx-auto mt-24 pt-6 border-t border-white/10 flex items-center justify-between text-white/35 text-xs">
        <span className="hero-serif italic text-white/60 text-base">ForgeAI</span>
        <span>Software born from a single sentence.</span>
      </div>
    </footer>
  );
}
