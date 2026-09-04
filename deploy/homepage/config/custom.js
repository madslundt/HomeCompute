// Resolve service links against the hostname used to open Homepage. Standard
// relative URLs retain the current port, so a small resolver is needed when
// each service listens on a different port.
(() => {
  const marker = "/__homecore_service__/";

  function resolveServiceLinks() {
    for (const link of document.querySelectorAll(`a[href*="${marker}"]`)) {
      const markerUrl = new URL(link.href, window.location.href);
      const parts = markerUrl.pathname.slice(marker.length).split("/");
      const [protocol, port, ...pathParts] = parts;

      if (!/^(http|https)$/.test(protocol) || !/^\d{1,5}$/.test(port)) {
        continue;
      }

      const path = `/${pathParts.join("/")}`;
      const defaultPort =
        (protocol === "http" && port === "80") ||
        (protocol === "https" && port === "443");
      const authority = defaultPort
        ? window.location.hostname
        : `${window.location.hostname}:${port}`;

      link.href = `${protocol}://${authority}${path}${markerUrl.search}${markerUrl.hash}`;
    }
  }

  resolveServiceLinks();
  new MutationObserver(resolveServiceLinks).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
})();
