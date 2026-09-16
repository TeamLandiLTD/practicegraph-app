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
