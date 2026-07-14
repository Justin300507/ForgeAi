import React from "react";
import { OVERLAY_PNG } from "../lib/cinematic";
import sceneryStill from "../assets/scenery-golden-hour.jpg";
import SceneryParticles from "./SceneryParticles";
import { useSceneryBoost } from "./SceneryBoost";

// The persistent blurred backdrop behind every authenticated page --
// mounted once in App.jsx (sibling to Routes) so route changes never
// recreate it. A static image, not video: zero decode cost behind the
// generation workspace's live WebSocket log stream. Briefly brightens
// (the "boosted" class) when a page fires useSceneryBoost().boost(),
// e.g. NewProject's Forge-press wow moment.
export default function Scenery() {
  const boost = useSceneryBoost();
  return (
    <div className={`scenery-layer${boost?.boosted ? " boosted" : ""}${boost?.sustained ? " sustained" : ""}`} aria-hidden="true">
      <div className="scenery-image" style={{ backgroundImage: `url(${sceneryStill})` }} />
      <div className="scenery-scrim" />
      {/* Blueprint drafting grid — the landing forge's signature texture,
          carried into the workspace at whisper volume so marketing and
          product read as one world. */}
      <div className="scenery-blueprint" />
      <div className="scenery-frame" style={{ backgroundImage: `url(${OVERLAY_PNG})` }} />
      <div className="scenery-mist--a" />
      <div className="scenery-mist--b" />
      <div className="scenery-light" />
      <SceneryParticles />
    </div>
  );
}
