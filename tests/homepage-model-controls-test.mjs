#!/usr/bin/env node
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../deploy/homepage/config/custom.js', import.meta.url), 'utf8');
const services = readFileSync(new URL('../deploy/homepage/config/services.yaml', import.meta.url), 'utf8');

function homepage(hostname, { withCard = true } = {}) {
  const links = [
    { href: 'https://models.home.arpa/control/' },
    { href: 'https://chessmate.home.arpa/play?level=2#board' },
    { href: 'https://ai.home.arpa/usage/?days=7#daily' },
    { href: '/__homecore_service__/http/8083/books?sort=title' },
    { href: 'https://chat.home.arpa/?models=homecompute-images' },
  ];
  const frames = [];
  let observer;
  const card = {
    querySelector: selector => selector === '.homecompute-comfy-controls' ? frames[0] : null,
    appendChild: frame => frames.push(frame),
  };
  const document = {
    documentElement: {},
    querySelector: selector => withCard && selector === '#homecore-comfyui .service-card' ? card : null,
    querySelectorAll(selector) {
      const prefix = selector.match(/^a\[href\^="([^"]+)"\]$/)?.[1];
      const contains = selector.match(/^a\[href\*="([^"]+)"\]$/)?.[1];
      return links.filter(link => prefix ? link.href.startsWith(prefix) : contains ? link.href.includes(contains) : false);
    },
    createElement(tag) {
      assert.equal(tag, 'iframe');
      return { attributes: {}, setAttribute(name, value) { this.attributes[name] = value; } };
    },
  };
  class MutationObserver {
    constructor(callback) { observer = callback; }
    observe(target, options) {
      assert.equal(target, document.documentElement);
      assert.equal(options.childList, true);
      assert.equal(options.subtree, true);
      assert.equal(options.attributes, undefined);
    }
  }
  // No fetch, postMessage or privileged external API is available to this script.
  vm.runInNewContext(source, { window: { location: { hostname, href: `https://${hostname}/` } }, document, MutationObserver, URL });
  return { links, frames, mutate: () => observer() };
}

for (const [host, origin] of [
  ['home-core.home.arpa', 'https://models.home.arpa'],
  ['home-core.tail479ad.ts.net', 'https://home-core.tail479ad.ts.net:18188'],
  ['100.100.10.1', 'https://home-core.tail479ad.ts.net:18188'],
  ['192.168.30.122', 'https://models.home.arpa'],
]) {
  const page = homepage(host);
  assert.equal(page.frames.length, 1);
  const frame = page.frames[0];
  assert.equal(frame.src, `${origin}/control/?compact=1`);
  assert.match(frame.title, /text, image and video model controls/i);
  assert.equal(frame.loading, 'lazy');
  assert.equal(frame.referrerPolicy, 'no-referrer');
  assert.equal(frame.attributes.sandbox, 'allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox');
  for (let update = 0; update < 20; update++) page.mutate();
  assert.equal(page.frames.length, 1, 'Homepage re-renders must not duplicate the controls');
  assert.equal(page.links[0].href, `${origin}/control/`);
  assert.equal(page.links[4].href, 'https://chat.home.arpa/?models=homecompute-images');
  assert.equal(page.links[3].href, `http://${host}:8083/books?sort=title`);
  console.log(`PASS model controls origin, accessible title and single iframe on ${host}`);
}

const tailnet = homepage('home-core.tail479ad.ts.net');
assert.equal(tailnet.links[1].href, 'https://home-core.tail479ad.ts.net:18080/play?level=2#board');
assert.equal(tailnet.links[2].href, 'https://home-core.tail479ad.ts.net:8443/usage/?days=7#daily');
assert.equal(homepage('home-core.home.arpa', { withCard: false }).frames.length, 0);
assert.match(services, /- Spark models:\s+id: homecore-comfyui\s+icon: [^\n]+\s+href: https:\/\/models\.home\.arpa\/control\/\s+description: Text, images and video · LLM returns after 30 minutes idle/);
console.log('PASS service card destination, other service links and absent-card handling');
