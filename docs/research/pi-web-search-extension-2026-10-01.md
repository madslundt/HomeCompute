# Pi web search extension setup

Verified: 2026-10-01

## Installed

`@tian.zuo/pi-web-search@0.9.0` is installed for the `agent` account on home-core and pinned in `/home/agent/.pi/agent/settings.json`. It provides `web_search` and `web_fetch`, with Firecrawl's keyless tier as a no-key fallback (the extension documents 1,000 free credits/month). It can also use Pi's OpenAI/Codex sign-in for OpenAI search when that account is connected. Search queries can therefore be sent to Firecrawl or the selected search provider. This provides text search and page fetching, not a general interactive browser.

The Pi TUI launched under `automation-moe` and listed this extension in its loaded resources. No model prompt or search request was sent during verification.

## Compatibility note

An initial attempt to use `pi-mono-web-search@0.1.0` failed during Pi startup because its `jsdom` dependency imports `punycode/`, which the Nix-packaged Pi 0.85.1 runtime did not resolve. That package was removed. `@tian.zuo/pi-web-search@0.9.0` loaded successfully with the installed runtime.

## Sources

- [Pi package install and extension docs](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/docs/packages.md)
- [Installed extension README](https://github.com/TianZuo555/pi-extensions/blob/main/packages/pi-web-search/README.md)
- [Installed extension package manifest](https://raw.githubusercontent.com/TianZuo555/pi-extensions/main/packages/pi-web-search/package.json)
