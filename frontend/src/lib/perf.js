// Device performance tier for the cinematic surfaces. Everything visual
// stays the same *story* on every tier — lower tiers just spend fewer
// pixels and particles telling it, so the landing holds 60fps on a
// budget laptop or phone instead of only on a desktop GPU.

let tier;

export function deviceTier() {
  if (tier) return tier;
  const mem = navigator.deviceMemory || 8;
  const cores = navigator.hardwareConcurrency || 8;
  const coarse = window.matchMedia("(pointer: coarse)").matches;
  if (coarse) {
    // Phones/tablets: even flagships throttle sustained WebGL + video.
    tier = mem >= 8 ? "mid" : "low";
  } else if (mem <= 4 || cores <= 4) {
    tier = "low";
  } else {
    tier = "high";
  }
  return tier;
}

// WebGL canvases render at min(devicePixelRatio, cap) — DPR 2+ quadruples
// the fragment work for detail nobody notices under motion.
export function dprCap() {
  return { low: 1, mid: 1.5, high: 2 }[deviceTier()];
}

// Scale a particle count down to the tier's budget.
export function particleBudget(full) {
  const f = { low: 0.35, mid: 0.6, high: 1 }[deviceTier()];
  return Math.max(24, Math.round(full * f));
}

// Run `cb(isVisible)` as `el` enters/leaves the viewport — used to pause
// video decode and WebGL frame loops the moment their section scrolls
// away. Returns a disconnect function.
export function onVisible(el, cb, rootMargin = "120px") {
  if (!el || typeof IntersectionObserver === "undefined") return () => {};
  const io = new IntersectionObserver(
    ([entry]) => cb(entry.isIntersecting),
    { rootMargin }
  );
  io.observe(el);
  return () => io.disconnect();
}
