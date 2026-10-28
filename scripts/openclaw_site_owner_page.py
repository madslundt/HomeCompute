"""Owner-only private browser UI. Never used for model observations."""
from __future__ import annotations

import base64
import hashlib
import html

from openclaw_access_page import STYLE, shell, moment

PRIVATE_STYLE = STYLE + '''
#private-display{height:65vh;min-height:360px;background:#000}main{max-width:1100px}
#private-keyboard{position:fixed;left:0;bottom:0;width:1px;height:1px;opacity:0;pointer-events:none;font-size:16px;-webkit-text-security:disc}
'''
BROWSER_CSP = ("default-src 'none'; style-src 'sha256-" +
               base64.b64encode(hashlib.sha256(PRIVATE_STYLE.encode()).digest()).decode() +
               "'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; "
               "script-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'")
CLIENT = '''const display = document.getElementById('private-display');
const status = document.getElementById('viewer-status');
if (display.dataset.connect === 'yes') {
  const keyboard = document.getElementById('private-keyboard');
  const keyboardToggle = document.getElementById('private-keyboard-toggle');
  const socket = new URL('./socket', window.location.href);
  socket.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  let rfb = null, retryAfter = 0, polling = false, stopped = false, loading = false;
  let connected = false, keysyms = null, composing = false;
  const sentinel = '_'.repeat(32);
  const resetInput = () => {
    keyboard.value = sentinel;
    try {keyboard.setSelectionRange(sentinel.length, sentinel.length);} catch (_) {}
  };
  const usable = () => connected && rfb && !stopped;
  const typing = () => usable() && document.activeElement === keyboard;
  const hideKeyboard = () => {
    keyboard.value = '';
    composing = false;
    keyboard.blur();
    keyboardToggle.textContent = 'Show keyboard';
    keyboardToggle.setAttribute('aria-expanded', 'false');
    if (rfb) rfb.focusOnClick = true;
  };
  const disableKeyboard = () => {
    connected = false;
    hideKeyboard();
    keyboard.disabled = keyboardToggle.disabled = true;
  };
  // Like noVNC's official mobile UI, focus a real textarea directly in the
  // owner's click gesture. Only sentinels survive an input event; no typed
  // value enters forms, fetch, storage, logging or clipboard APIs.
  keyboardToggle.addEventListener('mousedown', event => event.preventDefault());
  keyboardToggle.addEventListener('click', () => {
    if (!usable()) return;
    if (document.activeElement === keyboard) hideKeyboard();
    else {resetInput(); keyboard.focus();}
  });
  keyboard.addEventListener('focus', () => {
    if (!usable()) {hideKeyboard(); return;}
    rfb.focusOnClick = false;
    keyboardToggle.textContent = 'Hide keyboard';
    keyboardToggle.setAttribute('aria-expanded', 'true');
  });
  keyboard.addEventListener('blur', () => {
    keyboard.value = '';
    composing = false;
    keyboardToggle.textContent = 'Show keyboard';
    keyboardToggle.setAttribute('aria-expanded', 'false');
    if (rfb) rfb.focusOnClick = true;
  });
  display.addEventListener('mousedown', event => {
    if (document.activeElement === keyboard) event.preventDefault();
  });
  const specialKey = (keysym, code) => {if (typing()) rfb.sendKey(keysym, code);};
  const input = event => {
    event.stopPropagation();
    if (composing || event.isComposing) return;
    if (!typing()) {hideKeyboard(); return;}
    const value = Array.from(keyboard.value);
    if (value.length > 512) {resetInput(); return;}
    let prefix = 0;
    while (prefix < sentinel.length && prefix < value.length && value[prefix] === '_') prefix++;
    // Replacing placeholders (IME/autofill) must not erase remote text. Only
    // an actual deletion event, or a shortened all-sentinel buffer, deletes.
    if (event.inputType && event.inputType.startsWith('delete'))
      specialKey(event.inputType.includes('Forward') ? 0xffff : 0xff08, event.inputType.includes('Forward') ? 'Delete' : 'Backspace');
    else if (value.length < sentinel.length && value.every(character => character === '_'))
      specialKey(0xff08, 'Backspace');
    for (const character of value.slice(prefix, prefix + 256)) {
      const point = character.codePointAt(0);
      if (character === '\\n' || character === '\\r') specialKey(0xff0d, 'Enter');
      else if (character === '\\t') specialKey(0xff09, 'Tab');
      else if (point >= 32 && !(point >= 0x7f && point <= 0x9f) && !(point >= 0xd800 && point <= 0xdfff))
        rfb.sendKey(keysyms.lookup(point), null);
    }
    resetInput();
  };
  keyboard.addEventListener('input', input);
  keyboard.addEventListener('beforeinput', event => {
    event.stopPropagation();
    if (!typing() || composing || event.isComposing) return;
    const special = event.inputType && event.inputType.startsWith('delete')
      ? [event.inputType.includes('Forward') ? 0xffff : 0xff08, event.inputType.includes('Forward') ? 'Delete' : 'Backspace']
      : {insertLineBreak: [0xff0d, 'Enter'], insertParagraph: [0xff0d, 'Enter']}[event.inputType];
    if (special && event.cancelable) {event.preventDefault(); specialKey(...special); resetInput();}
  });
  keyboard.addEventListener('compositionstart', event => {event.stopPropagation(); composing = true;});
  keyboard.addEventListener('compositionend', event => {composing = false; input(event);});
  keyboard.addEventListener('keydown', event => {
    event.stopPropagation();
    if (!typing() || event.isComposing || composing) return;
    const special = {Enter: [0xff0d, 'Enter'], Tab: [0xff09, 'Tab']}[event.key];
    if (special) {event.preventDefault(); specialKey(...special); resetInput();}
  });
  keyboard.addEventListener('keyup', event => event.stopPropagation());
  window.addEventListener('blur', hideKeyboard);
  document.addEventListener('visibilitychange', () => {if (document.hidden) hideKeyboard();});
  const clear = () => {
    disableKeyboard();
    const previous = rfb;
    rfb = null;
    if (previous) previous.disconnect();
    display.replaceChildren();
  };
  const connect = async () => {
    if (rfb || loading || Date.now() < retryAfter || stopped) return;
    loading = true;
    try {
      const {default: RFB} = await import('./vendor/core/rfb.js');
      const {default: symbols} = await import('./vendor/core/input/keysymdef.js');
      if (stopped) return;
      keysyms = symbols;
      const viewer = new RFB(display, socket.href, {shared: false});
      rfb = viewer;
      viewer.scaleViewport = true;
      viewer.resizeSession = true;
      viewer.addEventListener('connect', () => {
        if (rfb !== viewer) return;
        connected = true;
        keyboard.disabled = keyboardToggle.disabled = false;
        status.textContent = 'Private viewer connected';
      });
      viewer.addEventListener('disconnect', () => {
        if (rfb !== viewer) return;
        disableKeyboard();
        rfb = null;
        display.replaceChildren();
        retryAfter = Date.now() + 1500;
        status.textContent = 'Waiting to reconnect the private viewer…';
      });
      viewer.addEventListener('credentialsrequired', () => {
        retryAfter = Infinity;
        clear();
        status.textContent = 'Viewer unavailable; no credentials should be entered here.';
      });
    } catch (_) {clear(); retryAfter = Date.now() + 3000;}
    finally {loading = false;}
  };
  const poll = async () => {
    if (polling || stopped) return;
    polling = true;
    try {
      const response = await fetch('./state', {credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(3000)});
      if (!response.ok) throw new Error('closed');
      const state = await response.json();
      if (stopped) return;
      if (state.status === 'awaiting_owner') await connect();
      else if (state.status === 'starting') {clear(); status.textContent = 'Starting the private viewer…';}
      else if (state.status === 'ready') {clear(); status.textContent = 'Signed in. Choose Take control to reopen your private screen.';}
      else throw new Error('closed');
    } catch (_) {
      stopped = true;
      window.clearInterval(interval);
      clear();
      status.textContent = 'Private session closed or permission unavailable.';
    }
    finally {polling = false;}
  };
  const interval = window.setInterval(poll, 750);
  window.addEventListener('pagehide', () => {stopped = true; window.clearInterval(interval); clear();}, {once: true});
  poll();
}
'''


def private_browser(item: dict, state: dict, challenges: dict[str, str], *, fill_result: str | None = None) -> str:
    reference = item['request_id']
    account = item['account']
    friendly = 'Hotels.com' if account == 'hotels.com' else account
    labels = {'start': 'Start private browser', 'fill': 'Fill ' + friendly + ' sign-in from Bitwarden',
              'ready': "I've signed in", 'close': 'Close session'}
    if state['status'] == 'ready':
        labels['start'] = 'Take control (pause agent)'
    forms = ''
    for action, nonce in challenges.items():
        forms += ('<form method="post" action="/browser/' + reference + '/' + action + '">'
                  '<input type="hidden" name="digest" value="' + item['request_digest'] + '">'
                  '<input type="hidden" name="csrf" value="' + html.escape(nonce, quote=True) + '">'
                  '<button type="submit">' + html.escape(labels[action]) + '</button></form>')
    connected = 'yes' if state['status'] in {'starting', 'awaiting_owner', 'ready'} else 'no'
    notices = {'filled_one': 'Filled one sign-in field. Continue privately, then fill again for the next step.',
               'filled_two': 'Filled both sign-in fields.',
               'no_fields': 'Nothing filled. Complete verification privately or check the registered sign-in page.'}
    notice = ('<p role="status" id="fill-result">' + html.escape(notices[fill_result]) + '</p>'
              if isinstance(fill_result, str) and fill_result in notices else '')
    body = ('<section class="card"><p>Account: ' + html.escape(item['account']) +
            '<br>Permission expires: ' + html.escape(moment(item['expires_at'])) +
            '<br>Session: ' + html.escape(state['status']) + '</p><div class="actions">' + forms + '</div></section>'
            '<p class="note">This viewer is only for you, the authenticated owner. Credentials, cookies, screenshots and '
            'keyboard events are never sent to the agent. The session closes when permission expires or is revoked. '
            'Signing in never gives the agent permission to purchase or book.</p>'
            '<p>Use the private screen below for Continue, legal consent, CAPTCHA or account recovery. '
            'The fill button only fills the registered sign-in fields; you control submission. '
            'After you sign in, choose “I\'ve signed in”.</p>'
            '<p class="note" id="mfa-help">For MFA, enter email, SMS or authenticator codes only in the private website screen below. '
            'Approve sign-in pushes on your trusted device. Never send codes, recovery codes or authenticator setup seeds '
            'to the agent or Mattermost. MFA or sign-in may be required again; choose Take control when needed. '
            'Permission still expires and can be revoked.</p>'
            + notice + '<p id="viewer-status">Private viewer</p>'
            '<p>On a phone, tap a field in the private screen, then choose Show keyboard. Typing goes directly to that screen.</p>'
            '<button type="button" id="private-keyboard-toggle" aria-controls="private-keyboard" aria-expanded="false" disabled>Show keyboard</button>'
            '<textarea id="private-keyboard" aria-label="Private screen keyboard" tabindex="-1" inputmode="text" '
            'autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" maxlength="512" disabled></textarea>'
            '<div id="private-display" data-connect="' + connected + '"></div>'
            '<script type="module" src="/browser/' + reference + '/client.js"></script>'
            '<p><a href="/review/' + reference + '">Back to permission</a></p>')
    return shell('Private ' + friendly + ' sign-in', body).replace('<style>' + STYLE, '<style>' + PRIVATE_STYLE)
