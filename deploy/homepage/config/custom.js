// Resolve service links against the hostname used to open Homepage. Standard
// relative URLs retain the current port, so a small resolver is needed when
// each service listens on a different port.
(() => {
  const marker = "/__homecore_service__/";

  function resolveServiceLinks() {
    // Use the publicly trusted, private Serve listener when Homepage is opened
    // over Tailscale; LAN clients keep the local chessmate.home.arpa name.
    const tailnetAccess = window.location.hostname.endsWith(".ts.net") ||
      /^100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(window.location.hostname);
    if (tailnetAccess) {
      for (const link of document.querySelectorAll('a[href^="https://chessmate.home.arpa/"]')) {
        const url = new URL(link.href);
        link.href = `https://home-core.tail479ad.ts.net:18080${url.pathname}${url.search}${url.hash}`;
      }
    }
    for (const link of document.querySelectorAll(`a[href*="${marker}"]`)) {
      const markerUrl = new URL(link.href, window.location.href);
      const parts = markerUrl.pathname.slice(marker.length).split("/");
      const [protocol, port, ...pathParts] = parts;

      if (!/^(http|https)$/.test(protocol) || !/^(\d{1,5}|edge)$/.test(port)) {
        continue;
      }

      const path = `/${pathParts.join("/")}`;
      // Funnel owns Tailscale 443 for TTLock. Caddy therefore uses 8443 on
      // the tailnet but retains the standard HTTPS port on the home LAN.
      const resolvedPort =
        port === "edge"
          ? window.location.hostname.endsWith(".ts.net")
            ? "8443"
            : "443"
          : port;
      const defaultPort =
        (protocol === "http" && resolvedPort === "80") ||
        (protocol === "https" && resolvedPort === "443");
      const authority = defaultPort
        ? window.location.hostname
        : `${window.location.hostname}:${resolvedPort}`;

      link.href = `${protocol}://${authority}${path}${markerUrl.search}${markerUrl.hash}`;
    }
  }

  resolveServiceLinks();
  new MutationObserver(resolveServiceLinks).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
})();
