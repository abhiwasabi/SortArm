const form = document.getElementById('geminiSettings');
const key = document.getElementById('geminiKey');
const fields = document.getElementById('geminiKeyFields');
const connectedNote = document.getElementById('geminiConnectedNote');
let configured = false;
let model = null;
let pending = false;
let accessRevision = 0;
const status = document.getElementById('geminiStatus');
const message = document.getElementById('geminiMessage');
const connect = document.getElementById('geminiConnect');
const disconnect = document.getElementById('geminiDisconnect');
const preview = new URLSearchParams(location.search).get('demo') === '1';
const operatorAccess = () => !preview && document.documentElement?.dataset.operator !== 'false';
form.hidden = !operatorAccess();
function show(data) {
  configured = data.configured === true;
  model = data.model || null;
  form.hidden = !operatorAccess();
  status.textContent = configured ? `Connected · ${model || 'Gemini'}` : 'Not connected';
  status.dataset.connected = String(configured);
  fields.hidden = configured;
  key.disabled = configured || !operatorAccess();
  key.required = !configured;
  if (configured) key.value = '';
  connectedNote.hidden = !configured;
  connect.hidden = configured;
  disconnect.hidden = !configured;
  connect.disabled = disconnect.disabled = pending || !operatorAccess();
}
async function update(body) {
  if (!operatorAccess() || pending || (configured && !body.disconnect)) return;
  const revision = accessRevision;
  pending = true;
  connect.disabled = disconnect.disabled = true;
  message.textContent = 'Testing available Gemini models…';
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 55000);
  try {
    const response = await fetch('/api/gemini', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body), signal: controller.signal
    });
    const data = await response.json();
    if (revision !== accessRevision) return;
    if (response.status === 401) {
      message.textContent = 'Operator login required';
      document.dispatchEvent(new Event('operator-required'));
      return;
    }
    if (revision !== accessRevision) return;
    if (!response.ok) throw new Error(data.error || 'Connection failed.');
    show(data);
    message.textContent = data.configured ? '' : 'Disconnected. Enter a key to connect again.';
  } catch (error) {
    message.textContent = error.name === 'AbortError' ? 'Connection timed out. Try again.' : error.message;
  } finally {
    key.value = '';
    pending = false;
    clearTimeout(timer);
    connect.disabled = disconnect.disabled = !operatorAccess();
  }
}
form.addEventListener('submit', event => {
  event.preventDefault();
  if (!operatorAccess() || configured || pending) return;
  const value = key.value.trim();
  key.value = '';
  if (value) update({key: value});
});
disconnect.addEventListener('click', () => update({disconnect: true}));
function loadGeminiStatus() { return fetch('/api/gemini').then(response => {
  if (response.status === 401) {
    message.textContent = 'Operator login required';
    document.dispatchEvent(new Event('operator-required'));
    throw new Error('Operator login required');
  }
  if (!response.ok) throw new Error();
  return response.json();
}).then(show).catch(error => { status.textContent = error.message === 'Operator login required' ? error.message : 'Server unavailable'; }); }
document.addEventListener?.('operator-access', () => {
  accessRevision++;
  key.value = '';
  show({configured, model});
  if (operatorAccess()) loadGeminiStatus();
});
if (!preview) loadGeminiStatus();
