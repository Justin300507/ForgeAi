import React from "react";

/* Custom forge cursor: a warm ember dot with a lagging ring. Mounted only
   on the landing route; pointer-coarse and reduced-motion users never see
   it (CSS hides it and the `cursor: none` rule is gated the same way). */
export default function CursorFX() {
  const dotRef = React.useRef(null);
  const ringRef = React.useRef(null);

  React.useEffect(() => {
    const fine = window.matchMedia("(pointer: fine)").matches;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!fine || reduced) return;

    document.body.classList.add("forge-cursor-active");
    const pos = { x: -100, y: -100, rx: -100, ry: -100 };
    let raf;

    const onMove = (e) => {
      pos.x = e.clientX;
      pos.y = e.clientY;
    };
    const onDown = () => ringRef.current?.classList.add("pressing");
    const onUp = () => ringRef.current?.classList.remove("pressing");

    const tick = () => {
      pos.rx += (pos.x - pos.rx) * 0.16;
      pos.ry += (pos.y - pos.ry) * 0.16;
      if (dotRef.current) {
        dotRef.current.style.transform = `translate(${pos.x - 3}px, ${pos.y - 3}px)`;
      }
      if (ringRef.current) {
        const scale = ringRef.current.classList.contains("pressing") ? " scale(0.7)" : "";
        ringRef.current.style.transform = `translate(${pos.rx - 15}px, ${pos.ry - 15}px)${scale}`;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("pointerup", onUp);

    return () => {
      cancelAnimationFrame(raf);
      document.body.classList.remove("forge-cursor-active");
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointerup", onUp);
    };
  }, []);

  return (
    <>
      <div ref={dotRef} className="forge-cursor-dot" aria-hidden="true" />
      <div ref={ringRef} className="forge-cursor-ring" aria-hidden="true" />
    </>
  );
}
