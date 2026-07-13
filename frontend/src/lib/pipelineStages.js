import { Lightbulb, LayoutTemplate, Server, MonitorSmartphone, ShieldCheck, Play, Rocket, Flag } from "lucide-react";

// Shared pipeline stage list -- single source of truth for both the live
// generation stepper (ProjectDetail.jsx) and the pre-submit preview card
// (NewProject.jsx), so the two never drift out of sync. Each stage also
// carries an Icon (lucide-react component) shown while the stage is
// waiting/active; completed/failed stages override it with a checkmark/✕
// at the call site.
export const STAGES = [
  { id:"plan",     label:"Planning",     keywords:["PRODUCT MANAGER"],                         Icon: Lightbulb },
  { id:"arch",     label:"Architecture", keywords:["ARCHITECT","TECH LEAD"],                   Icon: LayoutTemplate },
  { id:"backend",  label:"Backend",      keywords:["BACKEND TEAM","Wave 1","Wave 4"],           Icon: Server },
  { id:"frontend", label:"Frontend",     keywords:["FRONTEND TEAM","START FRONTEND"],           Icon: MonitorSmartphone },
  { id:"validate", label:"Validation",   keywords:["VALIDATION LOOP","Fix attempt","PATCHER"],  Icon: ShieldCheck },
  { id:"runtime",  label:"Runtime",      keywords:["RUNTIME","uvicorn","smoke test"],           Icon: Play },
  { id:"deploy",   label:"Deploy",       keywords:["Cloudflare","Render","GitHub","DEPLOY"],    Icon: Rocket },
  { id:"done",     label:"Complete",     keywords:["FINAL STATUS","V6 SCORE","Forge Score"],    Icon: Flag },
];

export function detectStage(logs) {
  for (let i = logs.length - 1; i >= 0; i--) {
    for (let s = STAGES.length - 1; s >= 0; s--) {
      if (STAGES[s].keywords.some(k => logs[i].includes(k))) return STAGES[s].id;
    }
  }
  return null;
}
