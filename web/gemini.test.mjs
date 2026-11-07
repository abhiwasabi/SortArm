import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

test('connected hides key and connect; disconnect restores both; reconnect hides them', async () => {
  const nodes = {};
  const document = {getElementById(id) { return nodes[id] ??= {hidden:false, disabled:false, value:'', dataset:{}, listeners:{}, addEventListener(event, fn) {this.listeners[event]=fn;}}; }};
  let connected = true;
  const posts = [];
  const context = vm.createContext({document, location:{search:''}, URLSearchParams, AbortController, setTimeout, clearTimeout,
    fetch: async (url, options) => {
      if (options) {const body = JSON.parse(options.body); posts.push(body); connected = !body.disconnect;}
      return {ok:true, json:async () => ({configured:connected, model:'test-model'})};
    }});
  vm.runInContext(readFileSync(new URL('./gemini.mjs', import.meta.url), 'utf8'), context);
  const settle = () => new Promise(resolve => setImmediate(resolve));
  await settle();
  assert.equal(nodes.geminiStatus.textContent, 'Connected · test-model');
  assert.equal(nodes.geminiStatus.dataset.connected, 'true');
  assert.equal(nodes.geminiKeyFields.hidden, true);
  assert.equal(nodes.geminiConnect.hidden, true);
  assert.equal(nodes.geminiDisconnect.hidden, false);
  nodes.geminiSettings.listeners.submit({preventDefault(){}});
  assert.equal(posts.length, 0);
  nodes.geminiDisconnect.listeners.click();
  await settle();
  assert.equal(nodes.geminiKeyFields.hidden, false);
  assert.equal(nodes.geminiKey.disabled, false);
  assert.equal(nodes.geminiConnect.hidden, false);
  assert.equal(nodes.geminiDisconnect.hidden, true);
  nodes.geminiKey.value = 'example-key';
  nodes.geminiSettings.listeners.submit({preventDefault(){}});
  await settle();
  assert.equal(nodes.geminiKeyFields.hidden, true);
  assert.equal(nodes.geminiConnect.hidden, true);
  assert.equal(nodes.geminiDisconnect.hidden, false);
  assert.equal(nodes.geminiKey.value, '');
});
