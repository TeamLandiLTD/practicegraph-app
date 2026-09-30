// The session token rides the opening URL (?token=...) — put there by
// `practicegraph ui serve`. Read it once, then strip it from the address bar so
// it does not linger in browser history or ride a Referer; every request
// carries it back in the X-PracticeGraph-Token header instead.
const params = new URLSearchParams(window.location.search);
// Per-tab, per-origin storage survives reloads without retaining the secret in
// the address bar. A new launch token always replaces the previous session.
const TOKEN_KEY = "pg-api-session-token";
const launchToken = params.get("token") || "";
let token = launchToken;
try {
  if (launchToken) sessionStorage.setItem(TOKEN_KEY, launchToken);
  else token = sessionStorage.getItem(TOKEN_KEY) || "";
} catch {
  // Restricted storage still permits the initial token-authenticated visit.
}
if (token && window.history && window.history.replaceState) {
  params.delete("token");
  const query = params.toString();
  window.history.replaceState(
    null,
    "",
    window.location.pathname + (query ? `?${query}` : "") + window.location.hash,
  );
}

const headers = { "X-PracticeGraph-Token": token };

export async function fetchNews() {
  const response = await fetch("/api/news", {headers});
  if (!response.ok) throw new Error("News could not be loaded.");
  return response.json();
}
export const refreshTokenPrices = () => postJson("/api/token-prices/refresh", {});
export const changeNews = body => postJson("/api/news", body);

// Run `task` at most once at a time. A call while it runs asks for exactly
// one more run afterwards, so the latest state is still fetched but a slow
// answer never has requests stacking up behind it (field report 2026-09-29:
// polls every five seconds against a view that took longer to build).
export function coalesce(task) {
  let running = null;
  let again = false;
  const run = () => {
    if (running) {
      again = true;
      return running;
    }
    let started;
    try {
      started = Promise.resolve(task());
    } catch (error) {
      started = Promise.reject(error);
    }
    running = started.finally(() => {
      running = null;
      if (again) {
        again = false;
        run();
      }
    });
    return running;
  };
  return run;
}

export async function fetchView() {
  const response = await fetch("/api/view", { headers });
  if (!response.ok) throw new Error(`view ${response.status}`);
  const view = await response.json();
  // Before any component renders the new view, so every amount on the page
  // (including pages that fetch their own data) uses the same currency.
  setDisplayCurrency(view.currency);
  return view;
}

export async function fetchUsage(period, page = 0, session = "", signal) {
  const query = new URLSearchParams({period, page: String(page)});
  if (session) query.set("session", session);
  const response = await fetch(`/api/usage?${query}`, {headers, signal});
  if (!response.ok) throw new Error(`usage ${response.status}`);
  return response.json();
}

export async function fetchTraining(exporting = false) {
  const response = await fetch(`/api/training${exporting ? "/export" : ""}`, {headers});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "training_unavailable");
  return result;
}
export const changeTraining = body => postJson("/api/training", body);

export async function fetchPresence() {
  const response = await fetch("/api/presence", { headers });
  if (!response.ok) return null;
  return response.json();
}

export async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return response.ok;
}

export async function postJson(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `post ${response.status}`);
  return result;
}

export const saveSchedule = (update) => postJson("/api/schedule", update);

// One archived day of an editorial shelf (news or build ideas).
export async function fetchFeedDay(channel, day) {
  const params = new URLSearchParams({ channel, day });
  const response = await fetch(`/api/feed-day?${params}`, { headers });
  if (!response.ok) throw new Error(`feed ${response.status}`);
  return response.json();
}

// The one-click default: the server answers with a closed outcome either
// way (409 carries the refusal), so this never throws on a refusal.
export async function pinModel(body) {
  try {
    const response = await fetch("/api/pin-model", {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await response.json();
  } catch {
    return { ok: false, outcome: "io_error", path: "", backup: null };
  }
}

export const saveCapabilityPaths = (paths) =>
  postJson("/api/capability-paths", { paths });
export const recordCapabilityOutcome = (outcome) =>
  postJson("/api/capability-outcome", { outcome });
export const acceptCapabilityPractice = (practiceId) =>
  postJson("/api/capability-practice", { practice_id: practiceId });
export const reviewCapabilityPractice = (feedback) =>
  postJson("/api/capability-review", { feedback });
export const clearCapabilityHistory = () =>
  postJson("/api/capability-clear", { confirm: true });

export async function fetchPracticeTime(exporting = false) {
  const response = await fetch(`/api/practice-time${exporting ? "/export" : ""}`, { headers });
  if (!response.ok) throw new Error("practice_time_unavailable");
  return response.json();
}
export const changePracticeTime = (body) =>
  postJson(body.action === "restore" ? "/api/practice-time/restore" : "/api/practice-time", body);

export async function fetchSetupImprovements(exporting = false) {
  const response = await fetch(`/api/setup-improvements${exporting ? "/export" : ""}`, {headers});
  if (!response.ok) throw new Error("setup_unavailable");
  return response.json();
}
export const changeSetupImprovement = (body) => postJson(
  body.action === "restore" ? "/api/setup-improvements/restore" : "/api/setup-improvements", body);

export const checkin = (rating) => post("/api/checkin", { rating });
// The audience switch: a closed enum into config.json server-side.
export const dismissSuggestion = (id) => post("/api/dismiss", { suggestion_id: id });
export const dismissTip = (id) => post("/api/dismiss", { tip_id: id });
// Copy ledger: a copied skill retires from the shelf for a few weeks. Local
// meta only; fire-and-forget from the shelf (a failure never blocks the copy).
export const recordSkillCopy = (id) => post("/api/skill-copied", { skill_id: id });

// Focus timer runs in-page (no OS protocol); it just records lifecycle events
// to the local API so the counters still land in the daily record.
export const recordBlock = (event) => post("/api/block", { event });

// Close the day (A2): mark today closed. The day is passed explicitly (the
// server never trusts the client's clock for the stored flag; it validates).
export const closeDay = (day, pieces) =>
  post("/api/day-close", pieces == null ? { day } : { day, pieces });

// The Calibration Mirror: post ONLY the estimate. The server resolves which
// session it belongs to, so the browser never holds a session identity — and
// never sees the actual length before the estimate is given.
export const recordEstimate = (felt) => post("/api/calibration", { felt });
// The felt-drain probe: one bracket (or a skip) per local day, recorded
// before the verification components are sent — the no-peek rule.
export const recordDrain = (felt) => post("/api/drain", { felt });

// The economy first-open retrospective (W1.1/AM-3): dismiss once, forever.
export const dismissEconomyIntro = () => post("/api/economy-intro", {});
// The vocabulary card: same contract — once put away, it stays away. No id
// travels, because the card is all-or-nothing rather than per-term.
export const dismissVocabulary = () => post("/api/vocabulary", {});
// The confirm-once billing lens (W1.1/AM-1): closed enum, validated
// server-side; unknown values are refused, never coerced.
export const setBillingMode = (mode, tool) => post("/api/billing-mode", { mode, ...(tool ? { tool } : {}) });
// The display currency: a closed set of codes, validated server-side.
export const setCurrency = (code) => post("/api/currency", { code });

// The currency amounts are shown in (analysis/fx.py). Money stays micro-USD
// in every view and API answer; the page multiplies by the view's factor only
// to show it, with the prefix and decimals the engine uses in its own
// sentences. USD formats exactly as it always has.
const USD_DISPLAY = { code: "USD", factor: 1, prefix: "$", digits: 2, date: null };
let display = USD_DISPLAY;

export const setDisplayCurrency = (block) => {
  const factor = Number(block?.factor);
  display = block && block.code !== "USD" && Number.isFinite(factor) && factor > 0
    ? { code: block.code, factor, prefix: block.prefix, digits: block.digits, date: block.date }
    : USD_DISPLAY;
};

export const currencyCode = () => display.code;

// "Converted from US dollars at the European Central Bank rate of 2026-09-29."
// Empty in USD, where nothing is converted.
export const convertedNote = () => display.code === "USD" ? ""
  : `Converted from US dollars at the European Central Bank rate of ${display.date}.`;

const shown = (amount, digits) => display.prefix + amount.toLocaleString("en-US", {
  minimumFractionDigits: digits,
  maximumFractionDigits: digits,
});

// A micro-USD amount in the display currency, to its usual decimals. The name
// predates the currency setting and is kept for its many callers.
export const usd = (micro) => shown(micro / 1_000_000 * display.factor, display.digits);

// A price per million tokens, as the price edition gives it (dollars, maybe
// a decimal string). USD keeps the edition's own digits without trailing
// zeros; a converted price shows the currency's decimals, more under 1.
export const perMillion = (value) => {
  if (value == null) return "Unknown";
  if (display.code === "USD") {
    return `$${String(value).replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1")}`;
  }
  const amount = Number(value) * display.factor;
  return display.prefix + amount.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: amount < 1 ? Math.max(display.digits, 3) : display.digits,
  });
};

export const compact = (value) => {
  if (value >= 1e9) return (value / 1e9).toFixed(1) + "B";
  if (value >= 1e6) return (value / 1e6).toFixed(1) + "M";
  if (value >= 1e3) return (value / 1e3).toFixed(1) + "K";
  return String(value);
};

export const count = (value) => value.toLocaleString("en-US");

// A count rounded for a sentence (COPY_RULES rule 5): exact under a
// hundred, nearest ten under a thousand, nearest hundred under ten
// thousand, nearest thousand beyond - "about 1,300", never "1,280".
export const about = (value) => {
  const n = Math.round(value);
  if (n < 100) return count(n);
  const step = n < 1000 ? 10 : n < 10000 ? 100 : 1000;
  return count(Math.round(n / step) * step);
};

// Money rounded for a sentence: exact under 10, whole units to 1,000, tens
// beyond ("$4.12", "$312", "$1,130").
export const usdAbout = (micro) => {
  const amount = micro / 1_000_000 * display.factor;
  if (amount < 10) return usd(micro);
  const step = amount < 1000 ? 1 : 10;
  return display.prefix + (Math.round(amount / step) * step).toLocaleString("en-US");
};

// A small per-unit price for a sentence: "25 cents", "$1.20", never "$0.2527".
// Other currencies have no "cents" to say, so they show the amount itself,
// or "less than" the smallest amount they write.
export const centsAbout = (micro) => {
  if (display.code !== "USD") {
    const smallest = 10 ** -display.digits;
    if (micro > 0 && micro / 1_000_000 * display.factor < smallest) {
      return `less than ${shown(smallest, display.digits)}`;
    }
    return usd(micro);
  }
  if (micro > 0 && micro < 10_000) return "less than 1 cent";
  if (micro < 1_000_000) {
    const cents = Math.round(micro / 10_000);
    return cents === 1 ? "1 cent" : `${cents} cents`;
  }
  return usd(micro);
};

// The movement against the reader's own past, in words (COPY_RULES rule
// 2): "up 61 since last month", never a glyph and a window size.
export const deltaPhrase = (delta, windowDays) => {
  const past = windowDays === 28 ? "last month"
    : windowDays === 7 ? "last week" : `the ${windowDays} days before`;
  if (delta == null) return "";
  if (delta === 0) return `same as ${past}`;
  const size = Number.isInteger(delta) ? count(Math.abs(delta)) : Math.abs(delta);
  return `${delta > 0 ? "up" : "down"} ${size} since ${past}`;
};

// Matches the Python duration_hm: "14h 32m" (minutes zero-padded once hours
// appear, so mono columns stay aligned), plain "45m" under an hour.
export const durationHm = (minutes) => {
  if (minutes >= 60) {
    return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
  }
  return `${minutes}m`;
};

export async function fetchTokenPrices() {
  const response = await fetch("/api/token-prices", {headers});
  if (!response.ok) throw new Error("Token prices unavailable");
  return response.json();
}
