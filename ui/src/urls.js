export const safeCodexUrl = (url) =>
  typeof url === "string" && url.startsWith("codex://new?prompt=") ? url : null;
