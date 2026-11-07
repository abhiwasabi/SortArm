const form = document.getElementById('geminiSettings');
const key = document.getElementById('geminiKey');
const fields = document.getElementById('geminiKeyFields');
const connectedNote = document.getElementById('geminiConnectedNote');
let configured = false;
let pending = false;
const status = document.getElementById('geminiStatus');
const message = document.getElementById('geminiMessage');
const connect = document.getElementById('geminiConnect');
const disconnect = document.getElementById('geminiDisconnect');
const preview = new URLSearchParams(location.search).get('demo') === '1';
form.hidden = preview;
function show(data) {
  configured = data.configured === true;
  status.textContent = configured ? `Connected · ${data.model || 'Gemini'}` : 'Not connected';
  status.dataset.connected = String(configured);
  fields.hidden = configured;
  key.disabled = configured;
  key.required = !configured;
  if (configured) key.value = '';
  connectedNote.hidden = !configured;
  connect.hidden = configured;
  disconnect.hidden = !configured;
}
async function update(body) {
  if (pending || (configured && !body.disconnect)) return;
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
    if (!response.ok) throw new Error(data.error || 'Connection failed.');
    show(data);
    message.textContent = data.configured ? '' : 'Disconnected. Enter a key to connect again.';
  } catch (error) {
    message.textContent = error.name === 'AbortError' ? 'Connection timed out. Try again.' : error.message;
  } finally {
    key.value = '';
    pending = false;
    clearTimeout(timer);
    connect.disabled = disconnect.disabled = false;
  }
}
form.addEventListener('submit', event => {
  event.preventDefault();
  if (configured || pending) return;
  const value = key.value.trim();
  key.value = '';
  if (value) update({key: value});
});
disconnect.addEventListener('click', () => update({disconnect: true}));
if (!preview) fetch('/api/gemini').then(response => {
  if (!response.ok) throw new Error();
  return response.json();
}).then(show).catch(() => { status.textContent = 'Server unavailable'; });
