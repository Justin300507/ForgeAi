import React from "react";

// Lets a routed page (e.g. NewProject's Forge-press wow moment) briefly
// intensify the globally-mounted Scenery backdrop, without Scenery needing
// to know who asked. Mirrors the Veil.jsx context pattern: a provider held
// above both the trigger and the consumer, one hook to read the flag.

const SceneryBoostContext = React.createContext(null);
export const useSceneryBoost = () => React.useContext(SceneryBoostContext);

const BOOST_MS = 900; // covers the 550ms veil-cover window plus an ease-out tail

export function SceneryBoostProvider({ children }) {
  const [boosted, setBoosted] = React.useState(false);
  const timer = React.useRef(null);

  React.useEffect(() => () => clearTimeout(timer.current), []);

  const boost = React.useCallback(() => {
    setBoosted(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setBoosted(false), BOOST_MS);
  }, []);

  const api = React.useMemo(() => ({ boosted, boost }), [boosted, boost]);

  return (
    <SceneryBoostContext.Provider value={api}>
      {children}
    </SceneryBoostContext.Provider>
  );
}
