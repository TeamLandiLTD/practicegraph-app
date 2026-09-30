import React from "react";

// A hover/focus tooltip: shows `text` on hover or keyboard focus, nothing until
// asked. The ⓘ is where method, source and caveat live (COPY_RULES rule 4), so
// the card face stays calm and the explanation is one gesture away.
export function Info({ text, className = "" }) {
  if (!text) return null;
  return (
    <span className={`info ${className}`.trim()} tabIndex={0} role="button"
          aria-label={text}>
      <span className="infoi" aria-hidden="true">i</span>
      <span className="infotip" role="tooltip">{text}</span>
    </span>
  );
}
