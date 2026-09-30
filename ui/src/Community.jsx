import React from "react";
import { ContentStatus } from "./ContentStatus.jsx";

export function Community({ items = [], status }) {
  return (
    <section className="card community-reading">
      <h2 className="section-title">Community findings</h2>
      <ContentStatus status={status} label="Community findings" />
      {items.length === 0 && status?.version ? <p>This edition contains no selected community findings.</p> : null}
      {items.map((item) => (
        <article key={item.id} id={`community-${item.id}`} className="community-item">
          <div className="muted">{item.source} · observed {item.observed}</div>
          <h3>{item.title}</h3>
          <p>{item.hook}</p>
          <details>
            <summary>What the discussion found</summary>
            <p>{item.finding}</p>
            {typeof item.url === "string" && item.url.startsWith("https://")
              ? <a href={item.url} target="_blank" rel="noreferrer">Read the source discussion</a> : null}
          </details>
        </article>
      ))}
    </section>
  );
}
