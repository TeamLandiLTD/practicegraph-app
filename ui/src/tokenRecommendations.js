
export const hasBasePrices = offer => offer.pricing.input != null && offer.pricing.output != null;
export function pricedEdition(edition) {
  const offers = edition.offers.filter(hasBasePrices);
  const modelIds = new Set(offers.map(o => o.model_id));
  return {...edition, offers, models: edition.models.filter(m => modelIds.has(m.id))};
}
export const pricedProviders = edition => [...new Set(edition.offers.filter(hasBasePrices).map(o => o.provider))]
  .sort((a, b) => providerName(a).localeCompare(providerName(b)));

export const PROVIDERS = {
  openai: ["OpenAI", "Developer"], anthropic: ["Anthropic", "Developer"], google: ["Google AI", "Developer"],
  xai: ["xAI", "Developer"], mistral: ["Mistral", "Developer"], deepseek: ["DeepSeek", "Developer"],
  alibaba: ["Alibaba / Qwen", "Developer"], zai: ["Z.ai", "Developer"], moonshot: ["Moonshot / Kimi", "Developer"], minimax: ["MiniMax", "Developer"],
  together: ["Together AI", "Host"], fireworks: ["Fireworks", "Host"], deepinfra: ["DeepInfra", "Host"], groq: ["Groq", "Host"],
  cerebras: ["Cerebras", "Host"], nebius: ["Nebius", "Host"], novita: ["Novita", "Host"], hyperbolic: ["Hyperbolic", "Host"],
  sambanova: ["SambaNova", "Host"], baseten: ["Baseten", "Host"], openrouter: ["OpenRouter", "Router"], huggingface: ["Hugging Face", "Router"],
  "aws-bedrock": ["AWS Bedrock", "Cloud"], "vertex-ai": ["Google Vertex AI", "Cloud"], azure: ["Microsoft Azure", "Cloud"],
};
export const providerName = id => PROVIDERS[id]?.[0] || id;
export const providerList = edition => [...new Set([...Object.keys(PROVIDERS), ...edition.coverage.map(c => c.provider), ...edition.offers.map(o => o.provider)])]
  .sort((a, b) => providerName(a).localeCompare(providerName(b)));

// A developer sells its own models through these provider IDs; aliases cover naming differences.
const FIRST_PARTY = {
  openai: ["openai"], anthropic: ["anthropic"], google: ["google", "gemini"], xai: ["xai"], mistral: ["mistral"],
  deepseek: ["deepseek"], alibaba: ["alibaba", "qwen"], zai: ["zai", "zhipu"], moonshot: ["moonshot", "kimi"], minimax: ["minimax"],
};
const slug = value => String(value).toLowerCase().replace(/[^a-z0-9]/g, "");
export const isFirstParty = (model, offer) => (FIRST_PARTY[offer.provider] || [])
  .some(alias => slug(model.developer).startsWith(alias));
const TIER_RANK = {standard: 0, other: 1, priority: 2, batch: 3};
const cmp = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const num = value => value == null ? Infinity : Number(value);

export function summarizeModel(model, offers) {
  const priced = offers.filter(hasBasePrices);
  const own = priced.filter(o => isFirstParty(model, o));
  const direct = priced.filter(o => o.channel !== "router");
  const pool = own.length ? own : direct.length ? direct : priced;
  // First party: the base alias in its default configuration at its dearer tariff; hosts: the lowest standard price.
  const ordered = [...pool].sort((a, b) => cmp(a.status === "available" ? 0 : 1, b.status === "available" ? 0 : 1)
    || cmp(TIER_RANK[a.service_tier] ?? 9, TIER_RANK[b.service_tier] ?? 9)
    || cmp(a.context_band.min ?? 0, b.context_band.min ?? 0)
    || cmp(num(a.pricing.cache_ttl_seconds), num(b.pricing.cache_ttl_seconds))
    || (own.length ? cmp(a.api_model_id.length, b.api_model_id.length) || a.api_model_id.localeCompare(b.api_model_id)
      || cmp(Number(b.pricing.input), Number(a.pricing.input)) || cmp(Number(b.pricing.output), Number(a.pricing.output))
      : cmp(Number(a.pricing.input), Number(b.pricing.input)) || cmp(Number(a.pricing.output), Number(b.pricing.output)))
    || a.id.localeCompare(b.id));
  const headline = ordered[0] || null;
  const same = o => headline && o.status === headline.status && o.service_tier === headline.service_tier
    && (o.context_band.min ?? 0) === (headline.context_band.min ?? 0) && o.pricing.cache_ttl_seconds === headline.pricing.cache_ttl_seconds
    && o.api_model_id === headline.api_model_id;
  const inputs = priced.map(o => o.pricing.input).sort((a, b) => Number(a) - Number(b));
  return {
    headline,
    from: own.length === 0,
    aliases: new Set(own.map(o => o.api_model_id)).size > 1,
    tariffs: own.length ? own.filter(same).length : 1,
    hosts: [...new Set(priced.map(o => o.provider))].sort((a, b) => providerName(a).localeCompare(providerName(b))),
    inputRange: inputs.length ? [inputs[0], inputs.at(-1)] : null,
  };
}

// A two-letter monogram and one of eight tones per developer, stable across
// editions: the row is scannable before the name is read.
export function developerBadge(developer) {
  const words = String(developer).replace(/[^A-Za-z0-9 ]/g, " ").trim().split(/\s+/).filter(Boolean);
  const initials = words.length >= 2 ? words[0][0] + words[1][0] : (words[0] || "??").slice(0, 2);
  let hash = 0;
  for (const ch of String(developer).toLowerCase()) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return {initials, tone: hash % 8};
}

// Direct, available, standard-tier hosts that undercut the developer's own
// standard price on BOTH input and output. Routers and other tiers never
// qualify: a cheaper quantized route is a different product, and batch is a
// different service. The saving is a whole percentage of the developer's rate.
export function cheaperHosts(model, offers) {
  const priced = offers.filter(hasBasePrices);
  // The bar is the developer's LOWEST own rate (any standard or tariff-window
  // offer that is available), not the headline: a host that undercuts a peak
  // tariff but not the off-peak one is not cheaper than the developer.
  const own = priced.filter(o => isFirstParty(model, o) && o.status === "available" && (o.service_tier === "standard" || o.service_tier === "other"))
    .sort((a, b) => Number(a.pricing.input) - Number(b.pricing.input) || Number(a.pricing.output) - Number(b.pricing.output));
  if (!own.length) return [];
  const base = own[0];
  const saving = (theirs, ours) => Math.round(100 * (Number(ours) - Number(theirs)) / Number(ours));
  return priced.filter(o => !isFirstParty(model, o) && o.channel !== "router" && o.status === "available"
      && o.service_tier === "standard" && Number(o.pricing.input) < Number(base.pricing.input)
      && Number(o.pricing.output) < Number(base.pricing.output))
    .map(o => ({offer: o, input_saving: saving(o.pricing.input, base.pricing.input), output_saving: saving(o.pricing.output, base.pricing.output)}))
    .sort((a, b) => b.output_saving - a.output_saving || a.offer.id.localeCompare(b.offer.id));
}

// The lowest direct, available, standard-tier price for a model, measured
// against the developer's own lowest rate when the developer sells it.
const eligible = o => hasBasePrices(o) && o.channel !== "router" && o.status === "available" && o.service_tier === "standard";
const byPrice = (a, b) => Number(a.pricing.output) - Number(b.pricing.output) || Number(a.pricing.input) - Number(b.pricing.input) || a.id.localeCompare(b.id);
const developerFloor = (model, offers) => offers.filter(o => hasBasePrices(o) && isFirstParty(model, o) && o.status === "available"
  && (o.service_tier === "standard" || o.service_tier === "other")).sort(byPrice)[0] || null;
const pct = (theirs, ours) => Math.round(100 * (Number(ours) - Number(theirs)) / Number(ours));
export function lowestOffer(model, offers) {
  const low = offers.filter(eligible).sort(byPrice)[0] || null;
  const developer = developerFloor(model, offers);
  if (!low) return {offer: null, developer, same_as_developer: false, input_saving: 0, output_saving: 0};
  const same = !!developer && (isFirstParty(model, low)
    || (Number(low.pricing.input) >= Number(developer.pricing.input) && Number(low.pricing.output) >= Number(developer.pricing.output)));
  return {offer: low, developer, same_as_developer: same,
    input_saving: developer && !same ? Math.max(0, pct(low.pricing.input, developer.pricing.input)) : 0,
    output_saving: developer && !same ? Math.max(0, pct(low.pricing.output, developer.pricing.output)) : 0};
}

// Deals the edition proves on its own: hosts undercutting developers, half-price
// windows (batch tiers and off-peak tariffs), and announced prices with an end date.
export function findDeals(edition, states, now = Date.now()) {
  const undercuts = [], halfPrice = [], ending = [];
  const day = 86400000;
  for (const model of edition.models) {
    const offers = edition.offers.filter(o => o.model_id === model.id && hasBasePrices(o) && states[o.id] !== "expired" && states[o.id] !== "announced");
    for (const c of cheaperHosts(model, offers)) undercuts.push({model, ...c, kind: "undercut"});
    // Half price: judged per provider against that provider's dearest own standard or tariff rate.
    for (const provider of new Set(offers.map(o => o.provider))) {
      const mine = offers.filter(o => o.provider === provider && o.status === "available" && o.channel !== "router");
      const regular = mine.filter(o => o.service_tier === "standard" || o.service_tier === "other").sort((a, b) => byPrice(b, a));
      if (!regular.length) continue;
      const sameBand = (a, b) => (a.context_band.min ?? 0) === (b.context_band.min ?? 0) && (a.context_band.max ?? null) === (b.context_band.max ?? null);
      for (const o of mine) {
        // The bar is the provider's dearest regular offer in the SAME context band, so a
        // batch rate is never measured against a long-context surcharge it does not carry.
        const reference = regular.find(r => r !== o && sameBand(r, o)) || regular[0];
        const window = o.service_tier === "batch" ? "batch" : o.service_tier === "other" && o !== reference ? "off-peak" : null;
        if (!window || reference === o) continue;
        if (Number(o.pricing.output) <= Number(reference.pricing.output) / 2 && Number(o.pricing.input) <= Number(reference.pricing.input) / 2) {
          halfPrice.push({model, offer: o, reference, kind: window, output_saving: pct(o.pricing.output, reference.pricing.output)});
        }
      }
    }
    for (const o of offers) {
      if (!o.effective_to || o.status !== "available") continue;
      const left = Date.parse(o.effective_to) - now;
      if (left > 0 && left <= 180 * day) ending.push({model, offer: o, kind: "ending", days_left: Math.ceil(left / day)});
    }
  }
  undercuts.sort((a, b) => b.output_saving - a.output_saving || a.offer.id.localeCompare(b.offer.id));
  halfPrice.sort((a, b) => b.output_saving - a.output_saving || a.model.name.localeCompare(b.model.name) || a.offer.id.localeCompare(b.offer.id));
  ending.sort((a, b) => a.days_left - b.days_left || a.offer.id.localeCompare(b.offer.id));
  const CAP = 8;
  return {undercuts: undercuts.slice(0, CAP), undercutsTotal: undercuts.length,
    halfPrice: halfPrice.slice(0, CAP), halfPriceTotal: halfPrice.length,
    ending: ending.slice(0, CAP), endingTotal: ending.length};
}
