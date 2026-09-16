import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import NewsPanel from "./NewsPanel.jsx";
import { ErrorBoundary, installGlobalErrorCapture } from "./ErrorBoundary.jsx";
import "./styles.css";
import "./redesign.css";

installGlobalErrorCapture();
const compactNews = new URLSearchParams(window.location.search).get("panel") === "news";
createRoot(document.getElementById("root")).render(
  <ErrorBoundary>{compactNews ? <NewsPanel /> : <App />}</ErrorBoundary>,
);
