import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('./auth0.mjs', import.meta.url), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));

function harness(config, authenticated = false) {
  const nodes = new Map();
  const element = id => {
    if (!nodes.has(id)) nodes.set(id, {hidden: false, disabled: false, textContent: '',
      listeners: {}, addEventListener(event, fn) { this.listeners[event] = fn; }});
    return nodes.get(id);
  };
  const calls = [];
  const window = {
    fetch: async (url, options = {}) => {
      calls.push({url, options});
      return {ok: true, json: async () => url === '/api/operator'
        ? {authorized: true, sub: 'operator'} : config};
    },
    auth0: {createAuth0Client: async () => ({
      isAuthenticated: async () => authenticated,
      getUser: async () => ({name: 'Test Operator'}),
      getTokenSilently: async () => 'signed-token',
      loginWithRedirect: async () => { calls.push({url: 'login'}); },
      logout: () => { calls.push({url: 'logout'}); }
    })}
  };
  const document = {getElementById: element, querySelector: () => element('content'),
    head: {append(script) { script.onload(); }},
    createElement: () => ({})};
  const context = vm.createContext({window, document, URL, URLSearchParams, Headers,
    location: {search: '', href: 'http://127.0.0.1:4173/', origin: 'http://127.0.0.1:4173',
      pathname: '/', hash: ''}, history: {replaceState() {}}});
  vm.runInContext(source, context);
  return {window, calls, element, ready: vm.runInContext('authReady', context)};
}

test('unconfigured Auth0 keeps the current dashboard and POST behavior', async () => {
  const h = harness({enabled: false});
  await h.ready;
  assert.equal(h.element('content').hidden, false);
  assert.equal(h.element('authGate').hidden, true);
  await h.window.fetch('/api/home', {method: 'POST'});
  assert.equal(h.calls[1].options.headers, undefined);
});

test('configured Auth0 hides controls until signed in and leaves STOP public', async () => {
  const config = {enabled: true, domain: 'example.us.auth0.com', clientId: 'client', audience: 'https://sorter'};
  const signedOut = harness(config);
  await signedOut.ready;
  assert.equal(signedOut.element('content').hidden, true);
  assert.equal(signedOut.element('authGate').hidden, false);
  await assert.rejects(() => signedOut.window.fetch('/api/home', {method: 'POST'}), /Sign in/);
  await signedOut.window.fetch('/api/stop', {method: 'POST'});
  assert.equal(signedOut.calls.at(-1).options.headers, undefined);
  await signedOut.element('authSignIn').listeners.click();
  assert.equal(signedOut.calls.at(-1).url, 'login');

  const signedIn = harness(config, true);
  await signedIn.ready;
  assert.equal(signedIn.element('content').hidden, false);
  assert.equal(signedIn.element('authIdentity').textContent, 'Test Operator');
  await signedIn.window.fetch('/api/home', {method: 'POST', headers: {'Content-Type': 'application/json'}});
  assert.equal(signedIn.calls.at(-1).options.headers.get('Authorization'), 'Bearer signed-token');
  signedIn.element('authSignOut').listeners.click();
  assert.equal(signedIn.calls.at(-1).url, 'logout');
});
