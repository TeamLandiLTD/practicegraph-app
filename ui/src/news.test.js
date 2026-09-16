import { expect, test } from "vitest";

import { activeNews, toggleNewsId } from "./news.js";

test("rich news is active only when the validated shelf has items", () => {
  expect(activeNews({ news: [{ id: "n" }] })).toEqual([{ id: "n" }]);
  expect(activeNews({ news: [], briefings: [{ id: "b" }] })).toEqual([]);
  expect(activeNews({})).toEqual([]);
});

test("one tapped news item is expanded at a time", () => {
  expect(toggleNewsId(null, "a")).toBe("a");
  expect(toggleNewsId("a", "a")).toBeNull();
  expect(toggleNewsId("a", "b")).toBe("b");
});
