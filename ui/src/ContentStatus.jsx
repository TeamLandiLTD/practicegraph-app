import React from "react";

// One short edition line per curated card: the label and the date on the
// face, the curator behind the title, the source as the link. An older
// edition keeps its warning on the same line — the honesty rule stands, in
// fewer words.
export function ContentStatus({ status, label = "Curated reading" }) {
  if (!status?.version) {
    return <p className="content-status">{label} · no edition downloaded yet</p>;
  }
  const text = status.edition_date
    ? `${label} edition ${status.edition_date}`
      + (status.state === "stale" ? " · older — check its sources" : "")
      + (status.state === "date_unknown" ? " · date unverified" : "")
    : `${label} · edition date unavailable`;
  const url = typeof status.source_url === "string" && status.source_url.startsWith("https://")
    ? status.source_url : null;
  const title = status.source_label || undefined;
  return <p className="content-status">
    {url ? <a href={url} target="_blank" rel="noreferrer" title={title}>{text}</a>
      : <span title={title}>{text}</span>}
  </p>;
}
