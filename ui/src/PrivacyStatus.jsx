import React from "react";

export function PrivacyStatus({ privacy }) {
  const sharing = privacy?.sharing_enabled;
  const provider = privacy?.wording_provider;
  const providerName = provider === "claude" ? "Claude" : provider === "codex" ? "Codex" : null;
  const label = !privacy ? "Privacy status unavailable"
    : sharing ? "Aggregate sharing enabled"
    : providerName ? `Optional ${providerName} wording enabled`
    : "Personal analysis stays local";

  return (
    <details className="privacy-status">
      <summary className="trust">{label}</summary>
      <div className="privacy-details">
        <strong>Privacy on this device</strong>
        <p>Personal activity is analyzed on this device.</p>
        <p>{sharing === true
          ? `Anonymous aggregate sharing is enabled. Destination ${privacy.endpoint_configured ? "configured" : "not configured"}.`
          : sharing === false ? "Anonymous aggregate sharing is off."
          : "Sharing state could not be read."}</p>
        {privacy?.sent_count > 0 ? <p>{privacy.sent_count} aggregate records were sent previously.</p> : null}
        {privacy?.queued_count > 0 ? <p>{privacy.queued_count} aggregate records are queued.</p> : null}
        <p>{providerName
          ? `Optional wording uses your ${providerName} CLI. Composed personal sentences and figures may be sent to that provider and use your plan or credits.`
          : "Wording uses local templates."}</p>
        <p>Public editions are downloaded without attaching your activity history.</p>
        <p className="muted">You control aggregate sharing with the consent settings. Downloading an edition is separate from sharing activity.</p>
      </div>
    </details>
  );
}
