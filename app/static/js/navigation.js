import { canonicalUrl as resolveUrl } from "./routes.mjs";

// Browser navigation owns the URL and history. Keep only the canonicalisation
// needed for the few legacy URLs that existed before the direct routes.
(() => {
  const canonicalUrl = href => resolveUrl(href, location.href);
  const initialUrl = canonicalUrl(location.href);
  if (initialUrl.href !== location.href) {
    history.replaceState(null, "", initialUrl.href);
  }

  window.ocheNavigate = href => {
    const url = canonicalUrl(href);
    if (url.href !== location.href) location.assign(url.href);
  };
})();
