import React from "react";
import Lenis from "lenis";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { deviceTier } from "../lib/perf";

gsap.registerPlugin(ScrollTrigger);

/* Smooth scroll for the landing route only — mounts a Lenis instance and
   keeps GSAP's ScrollTrigger in sync with it, tearing everything down on
   unmount so app pages keep native scrolling. Reduced motion: no Lenis,
   the browser scrolls natively. */
export default function useLenis() {
  React.useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    // Low tier renders the static forge sections (no pinned scrub), so
    // smooth-scroll would be a permanent rAF loop with nothing to sync.
    if (deviceTier() === "low") return;

    const lenis = new Lenis({ lerp: 0.11, wheelMultiplier: 1 });
    lenis.on("scroll", ScrollTrigger.update);

    const raf = (time) => lenis.raf(time * 1000);
    gsap.ticker.add(raf);
    gsap.ticker.lagSmoothing(0);

    return () => {
      gsap.ticker.remove(raf);
      lenis.destroy();
    };
  }, []);
}
