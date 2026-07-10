// Shared pipeline stage list -- single source of truth for both the live
// generation stepper (ProjectDetail.jsx) and the pre-submit preview card
// (NewProject.jsx), so the two never drift out of sync.
export const STAGES = [
  { id:"plan",     label:"Planning",     keywords:["PRODUCT MANAGER"] },
  { id:"arch",     label:"Architecture", keywords:["ARCHITECT","TECH LEAD"] },
  { id:"backend",  label:"Backend",      keywords:["BACKEND TEAM","Wave 1","Wave 4"] },
  { id:"frontend", label:"Frontend",     keywords:["FRONTEND TEAM","START FRONTEND"] },
  { id:"validate", label:"Validation",   keywords:["VALIDATION LOOP","Fix attempt","PATCHER"] },
  { id:"runtime",  label:"Runtime",      keywords:["RUNTIME","uvicorn","smoke test"] },
  { id:"deploy",   label:"Deploy",       keywords:["Cloudflare","Render","GitHub","DEPLOY"] },
  { id:"done",     label:"Complete",     keywords:["FINAL STATUS","V6 SCORE","Forge Score"] },
];

export function detectStage(logs) {
  for (let i = logs.length - 1; i >= 0; i--) {
    for (let s = STAGES.length - 1; s >= 0; s--) {
      if (STAGES[s].keywords.some(k => logs[i].includes(k))) return STAGES[s].id;
    }
  }
  return null;
}
