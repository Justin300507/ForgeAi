import React from "react";
import { Link, useNavigate } from "react-router-dom";
import { Menu, X, ArrowRight } from "lucide-react";
import { useAuth } from "../AuthContext";
import { VIDEOS, OVERLAY_PNG, IDEA_DRAFT_KEY } from "../lib/cinematic";

const SANS = { fontFamily: "system-ui, sans-serif" };
const DARK_HERO = "#182C41";

const NAV_LINKS = ["How It Works", "Features", "Pricing", "Community"];

const STATS = [
  "12,000+ Apps Forged",
  "3-Min Average Build",
  "Full-Stack + Live Deploy",
  "Verification-First Design",
];

export default function Landing() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [activeVideo, setActiveVideo] = React.useState(0);
  const [isTransitioning, setIsTransitioning] = React.useState(false);
  const [menuOpen, setMenuOpen] = React.useState(false);
  const [idea, setIdea] = React.useState("");
  const sceneRef = React.useRef(null);

  const switchVideo = (index) => {
    if (index === activeVideo || isTransitioning) return;
    setActiveVideo(index);
    setIsTransitioning(true);
    setTimeout(() => setIsTransitioning(false), 1000);
  };

  // Reduced motion: park the ambient videos on their first frame.
  React.useEffect(() => {
    if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    sceneRef.current?.querySelectorAll("video").forEach((v) => v.pause());
  }, []);

  // Mobile menu: close on Escape, lock body scroll while open.
  React.useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e) => e.key === "Escape" && setMenuOpen(false);
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [menuOpen]);

  const submitIdea = (e) => {
    e.preventDefault();
    if (idea.trim()) sessionStorage.setItem(IDEA_DRAFT_KEY, idea.trim());
    nav(user ? "/new" : "/register");
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
      {/* Background video layer */}
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

      {/* Content layer */}
      <div className="relative z-[2] flex flex-col h-full">
        {/* Navigation */}
        <nav className="flex items-center justify-between px-5 sm:px-10 py-5 sm:py-6">
          <Link to="/" className="hero-serif italic text-white text-xl sm:text-2xl">
            ForgeAI
          </Link>

          {/* Desktop nav pill */}
          <div className="hidden md:flex items-center gap-1 liquid-glass rounded-full pl-6 pr-1.5 py-1.5" style={SANS}>
            {NAV_LINKS.map((label) => (
              <a
                key={label}
                href={`#${label.toLowerCase().replace(/\s+/g, "-")}`}
                className="text-white/90 hover:text-white text-sm px-3 py-1.5 transition-colors"
              >
                {label}
              </a>
            ))}
            <Link
              to={primaryCta.to}
              className="ml-2 bg-white text-slate-900 text-sm font-medium px-4 py-2 rounded-full hover:bg-white/90 transition-colors"
            >
              {primaryCta.label}
            </Link>
          </div>

          {/* Mobile hamburger */}
          <button
            onClick={() => setMenuOpen((o) => !o)}
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            aria-expanded={menuOpen}
            className="md:hidden liquid-glass rounded-full w-11 h-11 flex items-center justify-center text-white"
          >
            <span className="relative w-5 h-5 block">
              <Menu
                size={20}
                className={
                  menuOpen
                    ? "absolute inset-0 transition-all duration-300 rotate-90 scale-75 opacity-0"
                    : "absolute inset-0 transition-all duration-300 rotate-0 scale-100 opacity-100"
                }
              />
              <X
                size={20}
                className={
                  menuOpen
                    ? "absolute inset-0 transition-all duration-300 rotate-0 scale-100 opacity-100"
                    : "absolute inset-0 transition-all duration-300 -rotate-90 scale-75 opacity-0"
                }
              />
            </span>
          </button>
        </nav>

        {/* Mobile menu overlay */}
        <div
          className={
            menuOpen
              ? "fixed inset-0 z-50 md:hidden bg-black/60 backdrop-blur-sm opacity-100 pointer-events-auto transition-opacity duration-500"
              : "fixed inset-0 z-50 md:hidden bg-black/60 backdrop-blur-sm opacity-0 pointer-events-none transition-opacity duration-500"
          }
          style={{ transitionTimingFunction: "cubic-bezier(0.4,0,0.2,1)" }}
        >
          {/* !absolute: .liquid-glass sets position:relative and would otherwise win the cascade */}
          <button
            onClick={() => setMenuOpen(false)}
            aria-label="Close menu"
            className="!absolute top-5 right-5 liquid-glass rounded-full w-11 h-11 flex items-center justify-center text-white"
          >
            <X size={20} />
          </button>
          <div className="h-full flex flex-col items-center justify-center gap-8">
            {NAV_LINKS.map((label, i) => (
              <a
                key={label}
                href={`#${label.toLowerCase().replace(/\s+/g, "-")}`}
                onClick={() => setMenuOpen(false)}
                className={
                  menuOpen
                    ? "text-white text-3xl transition-all duration-500 translate-y-0 opacity-100"
                    : "text-white text-3xl transition-all duration-500 translate-y-4 opacity-0"
                }
                style={{
                  transitionTimingFunction: "cubic-bezier(0.4,0,0.2,1)",
                  transitionDelay: menuOpen ? `${100 + i * 50}ms` : "0ms",
                }}
              >
                {label}
              </a>
            ))}
            <Link
              to={primaryCta.to}
              onClick={() => setMenuOpen(false)}
              className={
                menuOpen
                  ? "mt-4 bg-white text-slate-900 font-medium px-8 py-3 rounded-full transition-all duration-500 scale-100 opacity-100"
                  : "mt-4 bg-white text-slate-900 font-medium px-8 py-3 rounded-full transition-all duration-500 scale-90 opacity-0"
              }
              style={{
                ...SANS,
                transitionTimingFunction: "cubic-bezier(0.4,0,0.2,1)",
                transitionDelay: menuOpen ? "300ms" : "0ms",
              }}
            >
              {primaryCta.label}
            </Link>
          </div>
        </div>

        {/* Hero content */}
        <div className="flex-1 flex flex-col items-center justify-center text-center px-4">
          <div
            className="anim-fade-up liquid-glass rounded-full px-4 py-1.5 text-xs sm:text-sm mb-6 flex items-center gap-2 transition-colors duration-700"
            style={{ ...SANS, ...heroColor, "--d": "60ms" }}
          >
            <span className="live-dot bg-emerald-400 shrink-0" aria-hidden="true" />
            Over 12,000 apps already forged from plain English
          </div>

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

          {/* Video switcher */}
          <div
            className="anim-fade-up flex items-center gap-3 sm:gap-5 mt-9"
            style={{ ...SANS, "--d": "380ms" }}
            role="group"
            aria-label="Background scene"
          >
            {VIDEOS.map((video, index) => (
              <button
                key={video.label}
                onClick={() => switchVideo(index)}
                aria-pressed={index === activeVideo}
                aria-label={`Switch background to ${video.label}`}
                className={
                  index === activeVideo
                    ? "text-xs sm:text-sm px-1 py-2 border-b-2 border-current opacity-100 transition-all duration-700"
                    : "text-xs sm:text-sm px-1 py-2 border-b-2 border-transparent opacity-50 hover:opacity-80 transition-all duration-700"
                }
                style={heroColor}
              >
                {video.label}
              </button>
            ))}
          </div>
        </div>

        {/* Bottom stats */}
        <div
          className="flex flex-wrap items-center justify-center gap-x-4 gap-y-2 px-4 pb-6 text-white/70 text-xs sm:text-sm"
          style={SANS}
        >
          {STATS.map((stat, i) => (
            <React.Fragment key={stat}>
              {i > 0 && <span className="hidden sm:inline text-white/30" aria-hidden="true">|</span>}
              <span>{stat}</span>
            </React.Fragment>
          ))}
        </div>
      </div>
    </section>
  );
}
