import {expect, it} from "vitest";
import {providerList, pricedEdition, pricedProviders} from "./tokenRecommendations.js";
import research from "../../tests/fixtures/token-prices-research.json";

const at = "2026-09-12T00:00:00Z";
export function recommendationFixture() {
  const edition = structuredClone(research);
  edition.sources.forEach(s => s.checked_at = at);
  const o = edition.offers[0];
  Object.assign(o, {observed_at: at, service_tier: "standard", status: "available", context_band: {min: null, max: null}});
  o.limits.context_tokens = 131072; o.limits.output_tokens = 32000;
  Object.assign(o.pricing, {input: "0.10", output: "0.50", cache_read: null, cache_write: null});
  edition.offers = [o];
  edition.benchmarks = [{id: "score", model_id: o.model_id, task: "general", version: "4.3", score: "40", reasoning_effort: "high", harness: null, observed_at: at, source_ids: [edition.sources[0].id], notes: "Synthetic test result"}];
  edition.offer_evidence = [{offer_id: o.id, reasoning: true, modes: ["high"], benchmark_ids: ["score"], match: "documented", observed_at: at, source_ids: [edition.sources[0].id], notes: "Synthetic configuration"}];
  return edition;
}
it("keeps additional providers discoverable without claiming verified prices", () => {
  const d = recommendationFixture();
  expect(providerList(d)).toContain("azure"); expect(providerList(d)).toContain("huggingface");
  d.coverage.push({provider: "new-provider"}); expect(providerList(d)).toContain("new-provider");
});
// Model summaries: one headline price per model, first-party before hosts, direct before routers.
import {summarizeModel} from "./tokenRecommendations.js";
const offer = (id, provider, input, output, extra = {}) => ({...structuredClone(research.offers[0]), id, provider, upstream_provider: null,
  channel: "direct", service_tier: "standard", precision: null, context_band: {min: null, max: null},
  ...extra, pricing: {...structuredClone(research.offers[0].pricing), input, output, cache_read: null, cache_write: null, cache_ttl_seconds: null, ...(extra.pricing || {})}});
it("headline price is the developer's own standard offer even when a host is cheaper", () => {
  const model = {...research.models[0], developer: "Anthropic"};
  const s = summarizeModel(model, [offer("host", "together", "0.50", "1.00"), offer("own", "anthropic", "2", "10")]);
  expect(s.headline.id).toBe("own");
  expect(s.from).toBe(false);
  expect(s.hosts).toEqual(["anthropic", "together"]);
});
it("open model without a first-party seller takes the lowest direct standard offer, not a cheaper router route", () => {
  const model = {...research.models[0], developer: "OpenAI"};
  const s = summarizeModel(model, [
    offer("route", "openrouter", "0.03", "0.17", {channel: "router", upstream_provider: "akashml/bf16"}),
    offer("groq", "groq", "0.15", "0.60"), offer("novita", "novita", "0.05", "0.25"),
    offer("priority", "fireworks", "0.01", "0.02", {service_tier: "priority"}),
  ]);
  expect(s.headline.id).toBe("novita");
  expect(s.from).toBe(true);
  expect(s.inputRange).toEqual(["0.01", "0.15"]);
  expect(s.hosts).toEqual(["fireworks", "groq", "novita", "openrouter"]);
});
it("falls back to a router route only when no direct host prices the model", () => {
  const s = summarizeModel({...research.models[0], developer: "Meta"}, [offer("route", "openrouter", "0.03", "0.17", {channel: "router"})]);
  expect(s.headline.id).toBe("route");
  expect(s.from).toBe(true);
});
it("among first-party variants prefers the default cache duration, lowest input band, and the dearer tariff", () => {
  const model = {...research.models[0], developer: "Anthropic"};
  const s = summarizeModel(model, [
    offer("long", "anthropic", "2", "10", {pricing: {cache_ttl_seconds: 3600, cache_write: "4"}}),
    offer("short", "anthropic", "2", "10", {pricing: {cache_ttl_seconds: 300, cache_write: "2.5"}}),
  ]);
  expect(s.headline.id).toBe("short");
  const bands = summarizeModel({...research.models[0], developer: "OpenAI"}, [
    offer("high", "openai", "8", "30", {context_band: {min: 272001, max: 922000}}),
    offer("low", "openai", "4", "20", {context_band: {min: 0, max: 272000}}),
  ]);
  expect(bands.headline.id).toBe("low");
  const tariff = summarizeModel({...research.models[0], developer: "DeepSeek"}, [
    offer("offpeak", "deepseek", "0.15", "0.60", {service_tier: "other"}),
    offer("peak", "deepseek", "0.30", "1.20", {service_tier: "other"}),
  ]);
  expect(tariff.headline.id).toBe("peak");
});
it("matches developers to their own provider through aliases", () => {
  const qwen = summarizeModel({...research.models[0], developer: "Qwen"}, [offer("host", "deepinfra", "0.10", "0.30"), offer("own", "alibaba", "0.60", "3.60")]);
  expect(qwen.headline.id).toBe("own");
  const kimi = summarizeModel({...research.models[0], developer: "Moonshot AI"}, [offer("own", "moonshot", "3", "15")]);
  expect(kimi.from).toBe(false);
});
it("an available offer outranks a cheaper preview offer for the headline", () => {
  const s = summarizeModel({...research.models[0], developer: "Qwen"}, [
    offer("preview", "groq", "0.80", "4.00", {status: "preview"}), offer("live", "cerebras", "0.99", "1.49")]);
  expect(s.headline.id).toBe("live");
});
it("among first-party aliases the base API model ID wins before price", () => {
  const s = summarizeModel({...research.models[0], developer: "Moonshot AI"}, [
    offer("fast", "moonshot", "1.90", "8", {api_model_id: "kimi-k2.7-code-highspeed"}),
    offer("base", "moonshot", "0.95", "4", {api_model_id: "kimi-k2.7-code"})]);
  expect(s.headline.id).toBe("base");
  expect(s.aliases).toBe(true);
});
it("counts indistinguishable first-party tariffs so the row can say so", () => {
  const tariff = summarizeModel({...research.models[0], developer: "DeepSeek"}, [
    offer("offpeak", "deepseek", "0.15", "0.60", {service_tier: "other"}), offer("peak", "deepseek", "0.30", "1.20", {service_tier: "other"})]);
  expect(tariff.tariffs).toBe(2);
  const cache = summarizeModel({...research.models[0], developer: "Anthropic"}, [
    offer("long", "anthropic", "2", "10", {pricing: {cache_ttl_seconds: 3600}}), offer("short", "anthropic", "2", "10", {pricing: {cache_ttl_seconds: 300}})]);
  expect(cache.tariffs).toBe(1);
});

import {developerBadge} from "./tokenRecommendations.js";
it("gives each developer a stable two-letter monogram and tone", () => {
  expect(developerBadge("Anthropic")).toEqual({initials: "An", tone: developerBadge("Anthropic").tone});
  expect(developerBadge("Moonshot AI").initials).toBe("MA");
  expect(developerBadge("Z.ai").initials).toBe("Za");
  expect(developerBadge("xAI").initials).toBe("xA");
  const tones = ["OpenAI", "Anthropic", "Google", "DeepSeek", "Meta", "Mistral", "Qwen", "xAI"].map(d => developerBadge(d).tone);
  expect(tones.every(t => Number.isInteger(t) && t >= 0 && t < 8)).toBe(true);
  expect(new Set(tones).size).toBeGreaterThanOrEqual(6);
});

import {cheaperHosts} from "./tokenRecommendations.js";
it("names the direct hosts that undercut the developer's own price on both rates", () => {
  const model = {...research.models[0], developer: "Anthropic"};
  const offers = [
    offer("own", "anthropic", "3", "15"),
    offer("groq", "groq", "1.5", "7.5"),
    offer("together", "together", "3", "12"),
    offer("bedrock", "aws-bedrock", "3", "15"),
    offer("route", "openrouter", "1", "5", {channel: "router", upstream_provider: "x/fp8"}),
    offer("batch", "fireworks", "0.5", "2", {service_tier: "batch"}),
    offer("preview", "cerebras", "0.5", "2", {status: "preview"}),
  ];
  const cheaper = cheaperHosts(model, offers);
  expect(cheaper.map(c => c.offer.id)).toEqual(["groq"]);
  expect(cheaper[0].output_saving).toBe(50);
  expect(cheaper[0].input_saving).toBe(50);
});
it("has nothing to flag without a first-party price to compare against", () => {
  expect(cheaperHosts({...research.models[0], developer: "Meta"}, [offer("a", "groq", "1", "2"), offer("b", "together", "0.5", "1")])).toEqual([]);
});
it("compares hosts against the developer's lowest own rate, not the dearer tariff", () => {
  const model = {...research.models[0], developer: "DeepSeek"};
  const offers = [
    offer("peak", "deepseek", "0.3", "1.2", {service_tier: "other"}),
    offer("offpeak", "deepseek", "0.15", "0.6", {service_tier: "other"}),
    offer("fireworks", "fireworks", "0.22", "0.66"),
    offer("novita", "novita", "0.1", "0.5"),
  ];
  expect(cheaperHosts(model, offers).map(c => c.offer.id)).toEqual(["novita"]);
  expect(cheaperHosts(model, offers)[0].output_saving).toBe(17);
});

import {lowestOffer, findDeals} from "./tokenRecommendations.js";
it("picks the lowest direct standard price and measures it against the developer", () => {
  const model = {...research.models[0], developer: "Anthropic"};
  const offers = [
    offer("own", "anthropic", "3", "15"), offer("deepinfra", "deepinfra", "2.5", "12"),
    offer("vertex", "vertex-ai", "3", "15"), offer("route", "openrouter", "1", "5", {channel: "router"}),
    offer("batch", "anthropic", "1.5", "7.5", {service_tier: "batch"}), offer("preview", "groq", "0.5", "1", {status: "preview"}),
  ];
  const low = lowestOffer(model, offers);
  expect(low.offer.id).toBe("deepinfra");
  expect(low.output_saving).toBe(20);
  expect(low.same_as_developer).toBe(false);
  expect(lowestOffer(model, [offer("own", "anthropic", "3", "15"), offer("vertex", "vertex-ai", "3", "15")]).same_as_developer).toBe(true);
  expect(lowestOffer({...research.models[0], developer: "Meta"}, [offer("a", "groq", "1", "2")]).developer).toBe(null);
});
it("finds the three kinds of deal from the edition alone", () => {
  const now = Date.parse("2026-09-16T00:00:00Z");
  const anthropic = {...research.models[0], id: "m-claude", name: "Claude Sonnet 5", developer: "Anthropic"};
  const gemini = {...research.models[0], id: "m-gem", name: "Gemini 3.8 Flash", developer: "Google"};
  const deepseek = {...research.models[0], id: "m-ds", name: "DeepSeek V4.1 Flash", developer: "DeepSeek"};
  const kimi = {...research.models[0], id: "m-kimi", name: "Kimi K2.6", developer: "Moonshot AI"};
  const o = (id, mid, provider, inp, out, extra = {}) => ({...offer(id, provider, inp, out, extra), model_id: mid});
  const edition = {models: [anthropic, gemini, deepseek, kimi], offers: [
    o("c-std", "m-claude", "anthropic", "2", "10"), o("c-batch", "m-claude", "anthropic", "1", "5", {service_tier: "batch"}),
    o("g-std", "m-gem", "google", "0.75", "3.75", {effective_to: "2027-01-01T00:00:00Z"}), o("g-batch", "m-gem", "google", "0.375", "1.875", {service_tier: "batch"}),
    o("d-peak", "m-ds", "deepseek", "0.3", "1.2", {service_tier: "other"}), o("d-off", "m-ds", "deepseek", "0.15", "0.6", {service_tier: "other"}),
    o("k-own", "m-kimi", "moonshot", "0.95", "4"), o("k-novita", "m-kimi", "novita", "0.8", "3.4"), o("k-stale", "m-kimi", "together", "0.5", "2", {status: "preview"}),
  ]};
  const states = Object.fromEntries(edition.offers.map(x => [x.id, "current"]));
  const deals = findDeals(edition, states, now);
  expect(deals.undercuts.map(d => d.offer.id)).toEqual(["k-novita"]);
  expect(deals.halfPrice.map(d => d.offer.id).sort()).toEqual(["c-batch", "d-off", "g-batch"]);
  expect(deals.halfPrice.find(d => d.offer.id === "d-off").kind).toBe("off-peak");
  expect(deals.halfPrice.find(d => d.offer.id === "c-batch").kind).toBe("batch");
  expect(deals.ending.map(d => d.offer.id)).toEqual(["g-std"]);
  expect(deals.ending[0].days_left).toBe(107);
  expect(findDeals(edition, states, Date.parse("2027-02-01T00:00:00Z")).ending).toEqual([]);
});
it("measures a batch offer against the standard offer of the same context band and caps each deal group", () => {
  const now = Date.parse("2026-09-16T00:00:00Z");
  const sol = {...research.models[0], id: "m-sol", name: "GPT-5.6 Sol", developer: "OpenAI"};
  const o = (id, mid, provider, inp, out, extra = {}) => ({...offer(id, provider, inp, out, extra), model_id: mid});
  const edition = {models: [sol], offers: [
    o("s-short", "m-sol", "openai", "4", "20", {context_band: {min: 0, max: 272000}}),
    o("s-long", "m-sol", "openai", "8", "30", {context_band: {min: 272001, max: null}}),
    o("s-batch", "m-sol", "openai", "2", "10", {service_tier: "batch", context_band: {min: 0, max: 272000}}),
  ]};
  const states = Object.fromEntries(edition.offers.map(x => [x.id, "current"]));
  const deals = findDeals(edition, states, now);
  expect(deals.halfPrice).toHaveLength(1);
  expect(deals.halfPrice[0].reference.id).toBe("s-short");
  expect(deals.halfPrice[0].output_saving).toBe(50);
  const many = {models: [], offers: []};
  for (let i = 0; i < 12; i++) {
    const m = {...research.models[0], id: `m${i}`, name: `Model ${i}`, developer: "Anthropic"};
    many.models.push(m);
    many.offers.push(o(`std${i}`, `m${i}`, "anthropic", "2", "10"), o(`b${i}`, `m${i}`, "anthropic", "1", "5", {service_tier: "batch"}));
  }
  const capped = findDeals(many, Object.fromEntries(many.offers.map(x => [x.id, "current"])), now);
  expect(capped.halfPrice).toHaveLength(8);
  expect(capped.halfPriceTotal).toBe(12);
});
