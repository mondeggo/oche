export const homeRoutes = {
  overview: "/", integrations: "/integrations", autodarts: "/integrations/autodarts",
  wled: "/integrations/wled", wled2: "/integrations/visual-wled",
  caller: "/integrations/caller",
  profiles: "/profiles", events: "/events",
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
    (["system", "updates"].includes(url.searchParams.get("service")) ? url.searchParams.get("service") : "autodarts")
  : url.pathname + url.search;
