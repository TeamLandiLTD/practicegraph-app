export function activeNews(view) {
  return Array.isArray(view?.news) && view.news.length > 0 ? view.news : [];
}

export function toggleNewsId(current, requested) {
  return current === requested ? null : requested;
}
