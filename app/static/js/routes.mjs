// Home's own pages, plus the views integrations register (sent by the server).
const registered = (() => {
  try { return JSON.parse(globalThis.document?.getElementById("oche-home-routes")?.textContent || "{}"); }
  catch { return {}; }
})();
export const homeRoutes = {
  overview: "/", integrations: "/integrations",
  wled: "/integrations/wled", wled2: "/integrations/visual-wled",
  caller: "/integrations/caller",
  profiles: "/profiles", events: "/events",
  ...registered,
};
export const isHome = path => Object.values(homeRoutes).includes(path);
export const pageForUrl = url => Object.keys(homeRoutes).find(key => homeRoutes[key] === url.pathname) || "overview";
export function canonicalUrl(href, base) {
  const url = new URL(href, base);
  if (isHome(url.pathname) && Object.hasOwn(homeRoutes, url.hash.slice(1))) {
    url.pathname = homeRoutes[url.hash.slice(1)];
    url.hash = "";
  }
  if (url.pathname === "/supervisor" && url.searchParams.get("service") === "cameras") {
    url.pathname = "/integrations/cameras";
    url.search = "";
  }
  return url;
}
// Integration pages keep their setup terminals and camera previews while hidden.
export const persistent = path => isHome(path) || path === "/supervisor" || path.startsWith("/integrations/");
export const viewKey = url => isHome(url.pathname) ? "home"
  : url.pathname === "/supervisor" ? "/supervisor?service=" +
    (url.searchParams.get("service") === "updates" ? "updates" : "system")
  : url.pathname + url.search;
