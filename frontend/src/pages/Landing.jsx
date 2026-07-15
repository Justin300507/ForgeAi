import React from "react";
import { Link } from "react-router-dom";
import { ChevronDown } from "lucide-react";
import { useAuth, hasLoggedInBefore } from "../AuthContext";
import { useVeil } from "../components/Veil";
import { VIDEOS, OVERLAY_PNG } from "../lib/cinematic";
import { deviceTier, onVisible } from "../lib/perf";
import useLenis from "../landing/useLenis";
import CursorFX from "../landing/CursorFX";
import ForgeMorph from "../landing/ForgeMorph";
import ForgeStrike from "../landing/ForgeStrike";
import {
  ProblemSection,
  PipelineSection,
  ExamplesSection,
  StatsSection,
  CtaFooter,
} from "../landing/Sections";

const SANS = { fontFamily: "system-ui, sans-serif" };
const DARK_HERO = "#182C41";

// How long each ambient scene holds before crossfading to the next.
const SCENE_HOLD_MS = 6000;
// The crossfade itself (matches the videos' transition-opacity duration-1000)
// plus a beat — after this, the outgoing video can safely stop decoding.
const FADE_SETTLE_MS = 1100;

/* The train-window hero — unchanged concept: a journey unfolding behind
   glass while you describe the app you imagine. First screen of the page;
   the forge story now continues beneath it.

   Performance contract: only the scene currently on screen decodes video.
   Low-tier devices park on a single scene (no cycling, one decoder);
   everyone else plays exactly one video at a time, pausing the outgoing
   scene once its crossfade settles, and the whole window goes dormant the
   moment it scrolls out of view. */
function Hero({ leaving, onLeave }) {
  const { user } = useAuth();
  // Cycle scenes only where the device can afford multiple video decoders.
  const scenes = React.useMemo(
    () => (deviceTier() === "low" ? [VIDEOS[0]] : VIDEOS),
    []
  );
  const [activeVideo, setActiveVideo] = React.useState(0);
  const [heroVisible, setHeroVisible] = React.useState(true);
  const sceneRef = React.useRef(null);

  // Dormancy: the hero is a whole screen tall — once it scrolls away, its
  // videos must stop decoding or they drag every section below it.
  React.useEffect(
    () => onVisible(sceneRef.current, setHeroVisible, "0px"),
    []
  );

  // Ambient scenes cycle on their own — no controls, just weather.
  React.useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      // Reduced motion: park the videos on their first frame, no cycling.
      sceneRef.current?.querySelectorAll("video").forEach((v) => v.pause());
      return;
    }
    if (!heroVisible || scenes.length < 2) return;
    const t = setInterval(
      () => setActiveVideo((v) => (v + 1) % scenes.length),
      SCENE_HOLD_MS
    );
    return () => clearInterval(t);
  }, [heroVisible, scenes.length]);

  // Playback management: play the active scene, stop every other one
  // after the crossfade settles; stop everything when off-screen.
  React.useEffect(() => {
    const vids = sceneRef.current?.querySelectorAll("video");
    if (!vids) return;
    if (
      !heroVisible ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      vids.forEach((v) => v.pause());
      return;
    }
    vids.forEach((v, i) => {
      if (i === activeVideo) v.play().catch(() => {});
    });
    const t = setTimeout(() => {
      vids.forEach((v, i) => {
        if (i !== activeVideo) v.pause();
      });
    }, FADE_SETTLE_MS);
    return () => clearTimeout(t);
  }, [activeVideo, heroVisible]);

  const heroDark = activeVideo === 2;
  const heroColor = { color: heroDark ? DARK_HERO : "#ffffff" };
  const heroMuted = {
    color: heroDark ? "rgba(24,44,65,0.9)" : "rgba(255,255,255,0.8)",
    // Soft halo keeps the muted text readable over the busy treeline scene.
    textShadow: heroDark ? "0 1px 14px rgba(255,255,255,0.45)" : "none",
  };
  // "Open Dashboard" is earned: it appears only for an active session or a
  // device that has signed in before. First-time visitors get onboarding.
  const returning = Boolean(user) || hasLoggedInBefore();
  const primaryCta = returning
    ? { to: "/dashboard", label: "Open Dashboard" }
    : { to: "/register", label: "Get Started" };

  return (
    <section
      ref={sceneRef}
      className="relative w-full h-screen overflow-hidden bg-black"
      style={{ height: "100dvh" }}
    >
      {/* Scene layer: videos + window frame, zoomed through on exit */}
      <div className={`scene-zoom ${leaving ? "leaving" : ""}`}>
        {scenes.map((video, index) => (
          <video
            key={video.src}
            src={video.src}
            autoPlay={index === 0}
            muted
            loop
            playsInline
            preload={index === 0 ? "auto" : "metadata"}
            disablePictureInPicture
            aria-hidden="true"
            tabIndex={-1}
            className={
              index === activeVideo
                ? "absolute inset-0 w-full h-full object-cover transition-opacity duration-1000 ease-in-out opacity-100"
                : "absolute inset-0 w-full h-full object-cover transition-opacity duration-1000 ease-in-out opacity-0"
            }
          />
        ))}

        {/* Transparent PNG overlay */}
        <img
          src={OVERLAY_PNG}
          alt=""
          aria-hidden="true"
          onError={(e) => { e.currentTarget.style.display = "none"; }}
          className="train-bob absolute inset-0 w-full h-full object-cover pointer-events-none z-[1]"
        />
      </div>

      {/* Content layer */}
      <div className={`scene-fade ${leaving ? "leaving" : ""} relative z-[2] flex flex-col h-full`}>
        {/* Navigation — wordmark and a single door in */}
        <nav className="flex items-center justify-between px-5 sm:px-10 py-5 sm:py-6">
          <Link to="/" className="hero-serif italic text-white text-xl sm:text-2xl">
            ForgeAI
          </Link>
          <button
            onClick={() => onLeave(primaryCta.to)}
            className="bg-white text-slate-900 text-sm font-medium px-5 py-2 rounded-full hover:bg-white/90 transition-colors"
            style={SANS}
          >
            {primaryCta.label}
          </button>
        </nav>

        {/* Hero content */}
        <div className="flex-1 flex flex-col items-center justify-center text-center px-4 pb-16">
          <h1
            className="anim-fade-up hero-serif text-4xl sm:text-5xl md:text-7xl lg:text-[5.5rem] leading-[1.1] max-w-4xl transition-colors duration-700"
            style={{ ...heroColor, "--d": "140ms" }}
          >
            Software Born from
            <br />
            a Single Sentence
          </h1>

          <p
            className="anim-fade-up max-w-xl leading-relaxed mt-6 text-sm sm:text-base transition-colors duration-700"
            style={{ ...SANS, ...heroMuted, "--d": "220ms" }}
          >
            Rise above the boilerplate. Describe the product you imagine —
            ForgeAI architects, builds, verifies, and deploys a living
            full-stack application while you watch.
          </p>

          {/* No form up front — the journey below earns the ask; the single
              Forge It door lives at the end of the scroll (CtaFooter). */}
          <button
            onClick={() =>
              document.getElementById("forge-story")?.scrollIntoView({ behavior: "smooth" })
            }
            className="anim-fade-up liquid-glass rounded-full px-6 py-2.5 mt-8 text-sm transition-colors duration-700"
            style={{ ...SANS, ...heroColor, "--d": "300ms" }}
          >
            See how it's forged
          </button>
        </div>

        {/* Scroll cue — the journey continues below the window */}
        <div
          className="absolute bottom-6 inset-x-0 flex flex-col items-center gap-1 pointer-events-none transition-colors duration-700"
          style={{ ...SANS, ...heroMuted }}
        >
          <span className="forge-mono text-[10px] uppercase">The forge awaits</span>
          <ChevronDown size={16} aria-hidden="true" className="animate-bounce" />
        </div>
      </div>
    </section>
  );
}

export default function Landing() {
  const { user } = useAuth();
  const { veilNav } = useVeil();
  const [leaving, setLeaving] = React.useState(false);
  useLenis();

  // Camera-forward exit: zoom through the window glass while the veil
  // sweeps in, so leaving the landing feels like entering the landscape.
  const leave = (to) => {
    setLeaving(true);
    veilNav(to);
  };

  // The one door at the end of the story: signed-in smiths go straight to
  // the anvil, everyone else to the sign-in threshold.
  const forgeIt = () => leave(user ? "/new" : "/login");

  return (
    <main className="relative bg-[#0b0813]">
      <CursorFX />
      <Hero leaving={leaving} onLeave={leave} />
      <div id="forge-story" className={`scene-fade ${leaving ? "leaving" : ""}`}>
        <ProblemSection />
        <ForgeMorph />
        <ForgeStrike />
        <PipelineSection />
        <ExamplesSection />
        <StatsSection />
        <CtaFooter onForge={forgeIt} />
      </div>
    </main>
  );
}
