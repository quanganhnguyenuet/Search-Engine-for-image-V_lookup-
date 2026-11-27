// Minimal Gmail API client: refresh-token OAuth plus users.messages.send.

const TOKEN_URL = "https://oauth2.googleapis.com/token";
const SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send";
// Refresh a bit early so a cached token cannot expire while a request is in flight.
const EXPIRY_MARGIN_MS = 60_000;

export type GmailConfig = {
  clientId: string;
  clientSecret: string;
  refreshToken: string;
};

export type SendRaw = (raw: string, signal: AbortSignal) => Promise<void>;

export function createGmailSender(
  config: GmailConfig,
  fetchImpl: typeof fetch = fetch,
  now: () => number = Date.now,
): SendRaw {
  // Lives as long as the warm function instance, saving a round trip per email.
  let cached: { value: string; expiresAt: number } | null = null;

  async function accessToken(signal: AbortSignal): Promise<string> {
    if (cached && cached.expiresAt > now()) return cached.value;
    const response = await fetchImpl(TOKEN_URL, {
      method: "POST",
      body: new URLSearchParams({
        client_id: config.clientId,
        client_secret: config.clientSecret,
        refresh_token: config.refreshToken,
        grant_type: "refresh_token",
      }),
      signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.access_token) {
      // Only the error code is reported, never the request that carries the secrets.
      throw new Error(`token refresh failed: ${response.status} ${data.error ?? ""}`.trim());
    }
    cached = {
      value: data.access_token,
      expiresAt: now() + data.expires_in * 1000 - EXPIRY_MARGIN_MS,
    };
    return cached.value;
  }

  async function post(raw: string, signal: AbortSignal): Promise<Response> {
    return fetchImpl(SEND_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${await accessToken(signal)}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ raw }),
      signal,
    });
  }

  return async function send(raw, signal) {
    let response = await post(raw, signal);
    if (response.status === 401) {
      // The cached token was revoked early. A 401 means nothing was sent, so retrying is safe.
      await response.body?.cancel();
      cached = null;
      response = await post(raw, signal);
    }
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(`gmail send failed: ${response.status} ${data.error?.message ?? ""}`.trim());
    }
    await response.body?.cancel();
  };
}
