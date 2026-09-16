import React from "react";

// Field report 2026-09-16: the installed window went blank after a few
// minutes — the paper background and nothing else. React 18 unmounts the
// whole root when a render or effect throws with no boundary above it, and
// that is exactly what an empty page looks like. No trigger reproduced under
// instrumentation, so the product must (1) never lose the page to one thrown
// component and (2) keep the error where the next report can read it.
//
// Storage is treated as unreliable: every access is guarded, because a
// storage failure is itself one of the candidates for the blank page.

export const LAST_ERROR_KEY = "pg-last-error";

function safeString(value, limit = 4000) {
  try { return String(value ?? "").slice(0, limit); } catch { return ""; }
}

/** Keep the most recent client-side error for the next bug report. */
export function recordClientError(entry) {
  const record = {
    at: new Date().toISOString(),
    source: safeString(entry.source, 40) || "unknown",
    message: safeString(entry.message, 1000),
    stack: safeString(entry.stack),
    componentStack: safeString(entry.componentStack),
    href: (() => { try { return window.location.hash || "#"; } catch { return ""; } })(),
    version: safeString(entry.version, 40),
  };
  try { localStorage.setItem(LAST_ERROR_KEY, JSON.stringify(record)); } catch { /* storage is a convenience, not a dependency */ }
  return record;
}

/** Wire window-level capture once; errors outside React land in the same record. */
export function installGlobalErrorCapture(target = window) {
  const onError = (event) => recordClientError({
    source: "window", message: event.message || (event.error && event.error.message),
    stack: event.error && event.error.stack,
  });
  const onRejection = (event) => {
    const reason = event.reason;
    recordClientError({
      source: "promise", message: reason && reason.message ? reason.message : reason,
      stack: reason && reason.stack,
    });
  };
  target.addEventListener("error", onError);
  target.addEventListener("unhandledrejection", onRejection);
  return () => { target.removeEventListener("error", onError); target.removeEventListener("unhandledrejection", onRejection); };
}

export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null, record: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    const record = recordClientError({
      source: "render", message: error && error.message ? error.message : error,
      stack: error && error.stack, componentStack: info && info.componentStack,
    });
    this.setState({ record });
  }

  reload = () => {
    if (this.props.reload) { this.props.reload(); return; }
    try { window.location.reload(); } catch { /* nothing else to do */ }
  };

  render() {
    const { error, record } = this.state;
    if (!error) return this.props.children;
    const message = (error && error.message) || String(error);
    const stack = (record && record.stack) || (error && error.stack) || "";
    const componentStack = (record && record.componentStack) || "";
    return (
      <div className="loading crashed" role="alert">
        <h1>PracticeGraph hit an error</h1>
        <p>The page stopped rendering. Your data is untouched; reloading brings the dashboard back.</p>
        <p className="crash-message">{message}</p>
        <button type="button" className="primary" onClick={this.reload}>Reload the page</button>
        <details>
          <summary>Details for an issue report</summary>
          <pre>{[stack, componentStack].filter(Boolean).join("\n\n")}</pre>
        </details>
      </div>
    );
  }
}
