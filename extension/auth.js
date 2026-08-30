// Signing the extension in as the same person who uses the dashboard.
//
// No SDK and no bundler: Supabase's auth REST endpoints are three plain fetch
// calls, and this repo builds the extension by zipping the folder.
//
// What is stored here is a *token*, not a secret. The project URL and the anon
// (publishable) key identify the project and authorise nothing on their own --
// they are the same values the web app ships in its JavaScript. The OAuth
// client secret and the service-role key never come near this file.
//
// The access token is short-lived and refreshed on demand, so a compromised
// browser profile leaks minutes of access rather than a permanent credential,
// and signing out on the server ends it.

const SESSION_KEY = "authSession";
const CONFIG_KEY = "authConfig";

// Refresh this far before expiry rather than after a 401: a chat stream that
// dies mid-answer cannot be retried transparently.
const REFRESH_SKEW_SECONDS = 60;

export async function getAuthConfig() {
  const { [CONFIG_KEY]: config } = await chrome.storage.local.get(CONFIG_KEY);
  return config ?? { url: "", anonKey: "" };
}

export async function setAuthConfig(url, anonKey) {
  await chrome.storage.local.set({
    [CONFIG_KEY]: { url: url.trim().replace(/\/$/, ""), anonKey: anonKey.trim() },
  });
}

async function getSession() {
  const { [SESSION_KEY]: session } = await chrome.storage.local.get(SESSION_KEY);
  return session ?? null;
}

async function putSession(payload) {
  // expires_in is relative to now; store the absolute moment so a popup that
  // opens two hours later can tell the token is stale without a clock guess.
  await chrome.storage.local.set({
    [SESSION_KEY]: {
      access_token: payload.access_token,
      refresh_token: payload.refresh_token,
      expires_at: Math.floor(Date.now() / 1000) + (payload.expires_in ?? 3600),
      email: payload.user?.email ?? null,
    },
  });
}

export async function signOut() {
  await chrome.storage.local.remove(SESSION_KEY);
}

export async function currentEmail() {
  return (await getSession())?.email ?? null;
}

/** True once a project is configured; false means a single-user local backend. */
export async function authConfigured() {
  const { url, anonKey } = await getAuthConfig();
  return Boolean(url && anonKey);
}

async function authFetch(path, body) {
  const { url, anonKey } = await getAuthConfig();
  if (!url || !anonKey) {
    throw new Error("Set the Supabase project URL and anon key in settings.");
  }
  let res;
  try {
    res = await fetch(`${url}/auth/v1${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", apikey: anonKey },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("Unable to connect to the authentication service.");
  }
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = payload.error_description || payload.msg || payload.message || "";
    if (/invalid login credentials/i.test(message)) {
      throw new Error("Invalid email or password.");
    }
    if (/email not confirmed/i.test(message)) {
      throw new Error("This email is not verified yet.");
    }
    throw new Error(message || `Sign-in failed (HTTP ${res.status}).`);
  }
  return payload;
}

export async function signIn(email, password) {
  const payload = await authFetch("/token?grant_type=password", {
    email: email.trim(),
    password,
  });
  await putSession(payload);
  return payload.user?.email ?? email;
}

/**
 * A usable access token, or null when the extension is running against a
 * backend with authentication off.
 *
 * Refreshes rather than returning an expired token, and clears a session whose
 * refresh token the server has rejected -- otherwise the popup would retry a
 * dead session on every click.
 */
export async function getAccessToken() {
  if (!(await authConfigured())) return null;

  const session = await getSession();
  if (!session) return null;

  const now = Math.floor(Date.now() / 1000);
  if (session.expires_at - REFRESH_SKEW_SECONDS > now) return session.access_token;

  try {
    const payload = await authFetch("/token?grant_type=refresh_token", {
      refresh_token: session.refresh_token,
    });
    await putSession(payload);
    return payload.access_token;
  } catch {
    await signOut();
    throw new Error("Your session has expired. Please sign in again.");
  }
}
