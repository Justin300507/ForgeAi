import React from "react";
import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { useAuth } from "../AuthContext";
import { useVeil } from "../components/Veil";
import { VIDEOS, OVERLAY_PNG, IDEA_DRAFT_KEY } from "../lib/cinematic";

const SANS = { fontFamily: "system-ui, sans-serif" };
const DARK_HERO = "#182C41";

// How long each ambient scene holds before crossfading to the next.
const SCENE_HOLD_MS = 3000;

export default function Landing() {
  const { user } = useAuth();
  const { veilNav } = useVeil();
  const [activeVideo, setActiveVideo] = React.useState(0);
  const [leaving, setLeaving] = React.useState(false);
  const [idea, setIdea] = React.useState("");
  const sceneRef = React.useRef(null);

  // Camera-forward exit: zoom through the window glass while the veil
  // sweeps in, so leaving the landing feels like entering the landscape.
  const leave = (to) => {
    setLeaving(true);
    veilNav(to);
  };

  // Ambient scenes cycle on their own — no controls, just weather.
  React.useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      // Reduced motion: park the videos on their first frame, no cycling.
      sceneRef.current?.querySelectorAll("video").forEach((v) => v.pause());
      return;
    }
    const t = setInterval(
      () => setActiveVideo((v) => (v + 1) % VIDEOS.length),
      SCENE_HOLD_MS
    );
    return () => clearInterval(t);
  }, []);

  const submitIdea = (e) => {
    e.preventDefault();
    if (idea.trim()) sessionStorage.setItem(IDEA_DRAFT_KEY, idea.trim());
    leave(user ? "/new" : "/register");
  };

  const heroDark = activeVideo === 2;
  const heroColor = { color: heroDark ? DARK_HERO : "#ffffff" };
  const heroMuted = {
    color: heroDark ? "rgba(24,44,65,0.9)" : "rgba(255,255,255,0.8)",
    // Soft halo keeps the muted text readable over the busy treeline scene.
    textShadow: heroDark ? "0 1px 14px rgba(255,255,255,0.45)" : "none",
  };
  const primaryCta = user
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
        {VIDEOS.map((video, index) => (
          <video
            key={video.src}
            src={video.src}
            autoPlay
            muted
            loop
            playsInline
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
            onClick={() => leave(primaryCta.to)}
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

          <form
            onSubmit={submitIdea}
            className="anim-fade-up liquid-glass rounded-full flex items-center gap-2 p-1.5 pl-5 mt-8 w-full max-w-[340px] sm:max-w-md"
            style={{ ...SANS, "--d": "300ms" }}
          >
            <input
              type="text"
              value={idea}
              onChange={(e) => setIdea(e.target.value)}
              placeholder="Describe the app you imagine…"
              aria-label="Describe the app you want to build"
              className="flex-1 min-w-0 bg-transparent outline-none text-sm placeholder-current transition-colors duration-700"
              style={heroColor}
            />
            <button
              type="submit"
              className="bg-white text-slate-900 text-sm font-medium pl-4 pr-3 py-2 rounded-full whitespace-nowrap hover:bg-white/90 transition-colors flex items-center gap-1"
            >
              Forge It
              <ArrowRight size={14} aria-hidden="true" />
            </button>
          </form>
        </div>
      </div>
    </section>
  );
}
