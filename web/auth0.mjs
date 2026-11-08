// Optional Auth0 operator login. The server remains the authority for every command.
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
let operatorAuthorized = false;
let authReady;

function showGate(message, authenticated = false) {
  authGate.hidden = authenticated || !authEnabled;
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
  showGate('Checking operator login…');
  await loadSdk();
  authClient = await window.auth0.createAuth0Client({
    domain: config.domain,
    clientId: config.clientId,
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
    showGate('Sign in to control the sorter. STOP remains available.');
  }
}

authSignIn.addEventListener('click', async () => {
  if (!authClient) return;
  authSignIn.disabled = true;
  try { await authClient.loginWithRedirect(); }
  catch { authMessage.textContent = 'Sign in could not start. Try again.'; authSignIn.disabled = false; }
});
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

// Preserve the existing dashboard fetch calls while adding a token only to protected API writes.
window.fetch = async (input, options = {}) => {
  const url = new URL(typeof input === 'string' ? input : input.url, location.href);
  const method = (options.method || (typeof input !== 'string' && input.method) || 'GET').toUpperCase();
  if (url.origin !== location.origin || method !== 'POST' ||
      !url.pathname.startsWith('/api/') || url.pathname === '/api/stop') {
    return originalFetch(input, options);
  }
  await authReady;
  if (!authEnabled) return originalFetch(input, options);
  if (!authClient || !operatorAuthorized || !(await authClient.isAuthenticated())) {
    throw new Error('Sign in as an operator before using this control.');
  }
  const token = await authClient.getTokenSilently();
  const headers = new Headers(options.headers || {});
  headers.set('Authorization', 'Bearer ' + token);
  return originalFetch(input, {...options, headers});
};
