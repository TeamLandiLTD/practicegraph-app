import { beforeEach, expect, it, vi } from "vitest";

it("does not round positive sub-cent usage to zero", async () => {
  const { centsAbout } = await import("./api.js");
  expect(centsAbout(1)).toBe("less than 1 cent");
  expect(centsAbout(9999)).toBe("less than 1 cent");
  expect(centsAbout(10000)).toBe("1 cent");
  expect(centsAbout(0)).toBe("0 cents");
});

beforeEach(() => {
  vi.resetModules();
  sessionStorage.clear();
  window.history.replaceState(null, "", "/");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }));
});

it("keeps authentication across a reload without retaining the URL secret", async () => {
  window.history.replaceState(null, "", "/?token=first-session#practice");
  const first = await import("./api.js");
  await first.fetchView();
  expect(window.location.search).toBe("");
  expect(window.location.hash).toBe("#practice");
  vi.resetModules();
  const reloaded = await import("./api.js");
  await reloaded.fetchView();
  expect(fetch).toHaveBeenLastCalledWith("/api/view", {
    headers: { "X-PracticeGraph-Token": "first-session" },
  });
});

it("replaces an expired stored token with a new launch token", async () => {
  sessionStorage.setItem("pg-api-session-token", "old-session");
  window.history.replaceState(null, "", "/?token=new-session");
  const api = await import("./api.js");
  await api.saveSchedule({});
  expect(fetch.mock.lastCall[1].headers["X-PracticeGraph-Token"]).toBe("new-session");
  expect(sessionStorage.getItem("pg-api-session-token")).toBe("new-session");
});

it("does not invent credentials on a first visit without a token", async () => {
  const api = await import("./api.js");
  await api.fetchView();
  expect(fetch.mock.lastCall[1].headers["X-PracticeGraph-Token"]).toBe("");
});

const EUR = { code: "EUR", factor: "0.8", prefix: "€", digits: 2, date: "2026-09-29" };
const JPY = { code: "JPY", factor: "157.12", prefix: "¥", digits: 0, date: "2026-09-29" };

it("formats US dollars exactly as before a currency is chosen", async () => {
  const api = await import("./api.js");
  api.setDisplayCurrency({ code: "USD", factor: "1", prefix: "$", digits: 2 });
  expect(api.usd(1_234_567)).toBe("$1.23");
  expect(api.usd(1_234_560_000)).toBe("$1,234.56");
  expect(api.usdAbout(1_234_560_000)).toBe("$1,230");
  expect(api.centsAbout(250_000)).toBe("25 cents");
  expect(api.perMillion("2.50")).toBe("$2.5");
  expect(api.perMillion(15)).toBe("$15");
  expect(api.perMillion(null)).toBe("Unknown");
  expect(api.currencyCode()).toBe("USD");
  expect(api.convertedNote()).toBe("");
});

it("converts every amount with the view's factor and the currency's decimals", async () => {
  const api = await import("./api.js");
  api.setDisplayCurrency(EUR);
  expect(api.usd(10_000_000)).toBe("€8.00");
  expect(api.usd(1_234_560_000)).toBe("€987.65");
  expect(api.usdAbout(1_234_000_000)).toBe("€987");
  expect(api.usdAbout(20_000_000_000)).toBe("€16,000");
  expect(api.centsAbout(250_000)).toBe("€0.20");
  expect(api.centsAbout(5_000)).toBe("less than €0.01");
  expect(api.perMillion("2.50")).toBe("€2");
  expect(api.perMillion(0.2)).toBe("€0.16");
  expect(api.currencyCode()).toBe("EUR");
  expect(api.convertedNote()).toBe(
    "Converted from US dollars at the European Central Bank rate of 2026-09-29.");
  api.setDisplayCurrency(JPY);
  expect(api.usd(1_000_000)).toBe("¥157");
  expect(api.centsAbout(1_000)).toBe("less than ¥1");
  api.setDisplayCurrency({ code: "CHF", factor: "0.8", prefix: "CHF ", digits: 2 });
  expect(api.usd(1_000_000)).toBe("CHF 0.80");
});

it("falls back to US dollars on a missing or malformed currency block", async () => {
  const api = await import("./api.js");
  for (const block of [null, undefined, { ...EUR, factor: "0" }, { ...EUR, factor: "abc" }, { ...EUR, factor: undefined }]) {
    api.setDisplayCurrency(EUR);
    api.setDisplayCurrency(block);
    expect(api.usd(1_000_000)).toBe("$1.00");
  }
});

it("takes the display currency from every view it fetches", async () => {
  fetch.mockResolvedValue({ ok: true, json: async () => ({ currency: EUR }) });
  const api = await import("./api.js");
  await api.fetchView();
  expect(api.usd(1_000_000)).toBe("€0.80");
  fetch.mockResolvedValue({ ok: true, json: async () => ({}) });
  await api.fetchView();
  expect(api.usd(1_000_000)).toBe("$1.00");
});

it("saves the currency through its own closed endpoint", async () => {
  const api = await import("./api.js");
  await api.setCurrency("GBP");
  const [path, init] = fetch.mock.calls.at(-1);
  expect(path).toBe("/api/currency");
  expect(JSON.parse(init.body)).toEqual({ code: "GBP" });
});

it("runs a refresh at most once at a time and repeats it once if asked meanwhile", async () => {
  const { coalesce } = await import("./api.js");
  const pending = [];
  const task = vi.fn(() => new Promise((resolve) => pending.push(resolve)));
  const run = coalesce(task);
  run(); run(); run();  // a poll, then two more while the first is waiting
  expect(task).toHaveBeenCalledTimes(1);
  pending.shift()();
  await vi.waitFor(() => expect(task).toHaveBeenCalledTimes(2));
  pending.shift()();
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(task).toHaveBeenCalledTimes(2);
  run();
  expect(task).toHaveBeenCalledTimes(3);
});
