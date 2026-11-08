// Auth0 operator login. The server remains the authority for every command.
const authPreview = new URLSearchParams(location.search).get('demo') === '1';
const authGate = document.getElementById('authGate');
const authMessage = document.getElementById('authMessage');
const authSignIn = document.getElementById('authSignIn');
const authGateSignOut = document.getElementById('authGateSignOut');
const authBar = document.getElementById('authBar');
const authIdentity = document.getElementById('authIdentity');
const authSignOut = document.getElementById('authSignOut');
const content = document.querySelector('.content');
const originalFetch = window.fetch.bind(window);
let authClient = null;
let authEnabled = false;
let publicViewer = false;
let operatorAuthorized = false;
let authReady;

function showGate(message, authenticated = false) {
  authGate.hidden = authenticated || !authEnabled;
  // The sorter stays covered until this page has an Auth0 login. STOP sits outside this content.
  content.hidden = authEnabled && !authenticated;
  authBar.hidden = !authEnabled || !authenticated;
  authSignIn.hidden = authenticated;
  authGateSignOut.hidden = true;
  authMessage.textContent = message;
}

function loadSdk() {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://cdn.auth0.com/js/auth0-spa-js/2.30/auth0-spa-js.production.js';
    script.onload = () => window.auth0?.createAuth0Client ? resolve() : reject(new Error('Auth0 SDK unavailable'));
    script.onerror = () => reject(new Error('Auth0 SDK could not load'));
    document.head.append(script);
  });
}

async function initializeAuth() {
  if (authPreview) return;
  const response = await originalFetch('/api/auth-config', {cache: 'no-store'});
  if (!response.ok) throw new Error('Operator access could not be checked');
  const config = await response.json();
  if (config.enabled !== true) {
    content.hidden = false;
    authGate.hidden = true;
    return;
  }
  authEnabled = true;
  publicViewer = config.public === true;
  if (publicViewer && document.documentElement) document.documentElement.dataset.auth0 = 'true';
  showGate('Checking operator login…');
  await loadSdk();
  authClient = await window.auth0.createAuth0Client({
    domain: config.domain,
    clientId: config.clientId,
    cacheLocation: 'memory',
    authorizationParams: {redirect_uri: location.origin + '/', audience: config.audience}
  });
  const query = new URLSearchParams(location.search);
  if (query.has('code') && query.has('state')) {
    await authClient.handleRedirectCallback();
    history.replaceState({}, '', location.pathname + location.hash);
  }
  const authenticated = await authClient.isAuthenticated();
  if (authenticated) {
    const token = await authClient.getTokenSilently();
    const authorization = await originalFetch('/api/operator',
      {headers: {Authorization: 'Bearer ' + token}, cache: 'no-store'});
    if (!authorization.ok) {
      showGate('Signed in, but this account lacks the sorter:operate permission.');
      authSignIn.hidden = true;
      authGateSignOut.hidden = false;
      return;
    }
    operatorAuthorized = true;
    const user = await authClient.getUser();
    authIdentity.textContent = user?.name || user?.email || 'Operator';
    showGate('', true);
  } else {
    showGate('Auth0 login is required before starting. STOP remains available.');
    await startLogin();
  }
}

async function startLogin() {
  if (!authClient) return;
  authSignIn.disabled = true;
  try { await authClient.loginWithRedirect({authorizationParams: {prompt: 'login'}}); }
  catch { authMessage.textContent = 'Sign in could not start. Try again.'; authSignIn.disabled = false; }
}

authSignIn.addEventListener('click', () => startLogin());
authSignOut.addEventListener('click', () => {
  if (authClient) authClient.logout({logoutParams: {returnTo: location.origin + '/'}});
});
authGateSignOut.addEventListener('click', () => {
  if (authClient) authClient.logout({logoutParams: {returnTo: location.origin + '/'}});
});

if (!authPreview) {
  content.hidden = true;
  authGate.hidden = false;
  authSignIn.hidden = true;
  authMessage.textContent = 'Checking operator access…';
}
authReady = initializeAuth().catch(() => {
  authEnabled = true;
  showGate('Operator login is unavailable. Check Auth0 and the dashboard connection.');
  authSignIn.hidden = true;
});

const openPaths = new Set([
  '/api/stop', '/api/frame.jpg', '/api/auth-config', '/api/operator', '/api/login', '/api/logout'
]);
const tokenGetPaths = new Set(['/api/state', '/api/cameras', '/api/gemini']);

function withToken(options, token) {
  const headers = new Headers(options.headers || {});
  headers.set('Authorization', 'Bearer ' + token);
  return {...options, headers};
}

// Password login and logout stay open. Other API calls get a token only when Auth0
// is already authorized. A missing or failed token still sends the request, so the
// bridge can accept a password cookie.
window.fetch = async (input, options = {}) => {
  const url = new URL(typeof input === 'string' ? input : input.url, location.href);
  const method = (options.method || (typeof input !== 'string' && input.method) || 'GET').toUpperCase();
  if (url.origin !== location.origin || !url.pathname.startsWith('/api/') || openPaths.has(url.pathname)) {
    return originalFetch(input, options);
  }
  const sendPlain = () => originalFetch(input, options);
  if (method === 'GET') {
    if (!tokenGetPaths.has(url.pathname)) return sendPlain();
    await authReady;
    if (!operatorAuthorized || !authClient) return sendPlain();
    try {
      if (!(await authClient.isAuthenticated())) return sendPlain();
      return originalFetch(input, withToken(options, await authClient.getTokenSilently()));
    } catch {
      return sendPlain();
    }
  }
  if (method !== 'POST') return sendPlain();
  await authReady;
  if (!authEnabled || !authClient || !operatorAuthorized) return sendPlain();
  try {
    if (!(await authClient.isAuthenticated())) return sendPlain();
    return originalFetch(input, withToken(options, await authClient.getTokenSilently()));
  } catch {
    return sendPlain();
  }
};
