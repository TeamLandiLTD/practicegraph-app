// Separate tool defaults, the API market, news and community (owner review).
export const SURFACES = [
  {id: "spend", label: "Usage"},
  {id: "models", label: "Models"},
  {id: "api-prices", label: "API Prices"},
  {id: "tools", label: "Tools"},
  {id: "skills", label: "Agent skills"},
  {id: "connectors", label: "Integrations"},
  // Off the menu for now (owner 2026-09-16): still reachable by address until it is rethought.
  {id: "practice", label: "Practice", menu: false},
  {id: "baseline", label: "Baseline", menu: false},
  {id: "mindfulness", label: "Work rhythm"},
  {id: "news", label: "News"},
  {id: "community", label: "Community"},
  {id: "build", label: "Build ideas"},
  {id: "docs", label: "Guides"},
];

// Retired and aliased addresses keep working: each lands on the page that
// absorbed it, never on a dead end.
const RETIRED = {
  usage: "spend", toolkit: "models", projects: "spend",
  history: "practice", learning: "practice", advice: "practice", setup: "practice",
  reading: "news", updates: "news", prices: "api-prices",
};

export function resolveSurface(hash) {
  const id = hash.replace(/^#/, "");
  if (RETIRED[id]) return RETIRED[id];
  return SURFACES.some(page => page.id === id) ? id : "spend";
}

// One line icon per destination: a plain signifier, drawn on a 20-unit grid.
export const ICONS = {
  spend: "M3 15.5 8 9l4 3 5-7M3 17h14",
  models: "M10 3l6 3.5v7L10 17l-6-3.5v-7L10 3zm0 0v14M4 6.5l6 3.5 6-3.5",
  "api-prices": "M10 3v14M6.5 6.5h5a2 2 0 0 1 0 4h-3a2 2 0 0 0 0 4h5",
  tools: "M12.5 3.5a3.5 3.5 0 0 0-4.6 4.6L3.5 12.5l4 4 4.4-4.4a3.5 3.5 0 0 0 4.6-4.6l-2.3 2.3-2.2-2.2 2.5-2.1z",
  skills: "M10 3l2 4.2 4.6.6-3.3 3.2.8 4.6L10 13.4l-4.1 2.2.8-4.6L3.4 7.8 8 7.2z",
  connectors: "M7 4v4a3 3 0 0 0 6 0V4M10 11v6M6 4h2M12 4h2",
  practice: "M4 16V9l6-5 6 5v7M8 16v-4h4v4",
  baseline: "M3 10h14",
  mindfulness: "M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14zm0 3.5V10l2.5 2",
  news: "M4 4h12v12H4zM7 7h6M7 10h6M7 13h3",
  community: "M7 9a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zm6 1a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM2.5 16c0-2.5 2-4 4.5-4s4.5 1.5 4.5 4M10.5 15c.3-2 2-3 4-3 1.7 0 3 1 3 3",
  build: "M10 3a4.5 4.5 0 0 0-2.5 8.2V14h5v-2.8A4.5 4.5 0 0 0 10 3zM8 16.5h4",
  docs: "M5 3h8l3 3v11H5zM8 9h5M8 12h5",
};
