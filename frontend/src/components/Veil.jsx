import React from "react";
import { useNavigate } from "react-router-dom";

// Cinematic route transition: an ink-and-aurora veil sweeps over the screen,
// the serif wordmark breathes in the dark, and the next page is revealed
// beneath it as it lifts. Lives above <Routes> so it survives navigation.

const VeilContext = React.createContext(null);
export const useVeil = () => React.useContext(VeilContext);

const COVER_MS = 550;   // matches .forge-veil transition-duration
const REVEAL_HOLD_MS = 220; // let the next page mount behind the veil
const LIFT_MS = 950;    // matches .forge-veil.lift transition-duration

const reducedMotion = () =>
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function VeilProvider({ children }) {
  const nav = useNavigate();
  const [phase, setPhase] = React.useState("idle"); // idle | cover | lift
  const timers = React.useRef([]);

  React.useEffect(() => () => timers.current.forEach(clearTimeout), []);
  const later = (fn, ms) => timers.current.push(setTimeout(fn, ms));

  // Resolves once the screen is fully covered.
  const cover = React.useCallback(() => {
    if (reducedMotion()) return Promise.resolve();
    setPhase("cover");
    return new Promise((resolve) => later(resolve, COVER_MS));
  }, []);

  // Lift the veil to reveal whatever is now underneath.
  const lift = React.useCallback(() => {
    if (reducedMotion()) { setPhase("idle"); return; }
    setPhase("lift");
    later(() => setPhase("idle"), LIFT_MS);
  }, []);

  // Lift after a short hold so the destination page has painted.
  const liftSoon = React.useCallback(() => {
    later(lift, REVEAL_HOLD_MS);
  }, [lift]);

  // The one-call version: cover, navigate, reveal.
  const veilNav = React.useCallback(
    async (to) => {
      if (phase !== "idle") return;
      await cover();
      nav(to);
      window.scrollTo(0, 0);
      liftSoon();
    },
    [phase, cover, nav, liftSoon]
  );

  const api = React.useMemo(
    () => ({ veilNav, cover, lift, liftSoon }),
    [veilNav, cover, lift, liftSoon]
  );

  return (
    <VeilContext.Provider value={api}>
      {children}
      <div className={`forge-veil ${phase !== "idle" ? phase : ""}`} aria-hidden="true">
        <span className="veil-mark hero-serif italic">ForgeAI</span>
      </div>
    </VeilContext.Provider>
  );
}
