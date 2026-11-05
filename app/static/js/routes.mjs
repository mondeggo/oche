// Home's own pages, plus the views integrations register (sent by the server).
const registered = (() => {
  try { return JSON.parse(globalThis.document?.getElementById("oche-home-routes")?.textContent || "{}"); }
  catch { return {}; }
})();
export const homeRoutes = {
  integrations: "/integrations",
  wled: "/integrations/wled", wled2: "/integrations/visual-wled",
  caller: "/integrations/caller",
  profiles: "/profiles", events: "/events",
  ...registered,
};
export const isHome = path => Object.values(homeRoutes).includes(path);
export function pageForUrl(url) {
  if (url.pathname === "/integrations/wled") {
    let view = url.searchParams.get("view");
    if (!view) { try { view = globalThis.localStorage?.getItem("oche.wled.view"); } catch { /* Default list. */ } }
    return view === "visual" ? "wled2" : "wled";
  }
  return Object.keys(homeRoutes).find(key => homeRoutes[key] === url.pathname) || "integrations";
}
export function canonicalUrl(href, base) {
  const url = new URL(href, base);
  if ((isHome(url.pathname) || url.pathname === "/") && Object.hasOwn(homeRoutes, url.hash.slice(1))) {
    url.pathname = homeRoutes[url.hash.slice(1)];
    url.hash = "";
  }
  if (url.pathname === "/") {
    url.pathname = "/local";
    if (url.hash === "#overview") url.hash = "";
  }
  if (url.pathname === "/supervisor" && url.searchParams.get("service") === "cameras") {
    url.pathname = "/integrations/cameras";
    url.search = "";
  }
  return url;
}
