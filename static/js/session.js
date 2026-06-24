const SESSION_KEY = 'eek_session_id';
const USERNAME_KEY = 'eek_username';

async function getSession() {
  let sid = localStorage.getItem(SESSION_KEY);
  const res = await fetch('/api/session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sid }),
  });
  const data = await res.json();
  localStorage.setItem(SESSION_KEY, data.session_id);
  return data.session_id;
}

function getSessionId() {
  return localStorage.getItem(SESSION_KEY);
}

function saveUsername(username) {
  localStorage.setItem(USERNAME_KEY, username);
}

function clearSavedUsername() {
  localStorage.removeItem(USERNAME_KEY);
}

function getSavedUsername() {
  return localStorage.getItem(USERNAME_KEY) || '';
}
