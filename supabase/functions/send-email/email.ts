// Email content and RFC 2822 message building. Pure functions, no runtime-specific APIs.

// Keep in sync with "Email OTP expiration" (600 seconds) in the Supabase dashboard.
const OTP_TTL_MINUTES = 10;

const CODE_EMAILS: Record<string, { subject: string; intro: string; ignore: string }> = {
  signup: {
    subject: "Mã xác nhận tài khoản V-Lookup",
    intro: "Mã xác nhận tài khoản V-Lookup của bạn:",
    ignore: "Nếu bạn không đăng ký, hãy bỏ qua email này.",
  },
  recovery: {
    subject: "Mã đặt lại mật khẩu V-Lookup",
    intro: "Mã đặt lại mật khẩu V-Lookup của bạn:",
    ignore: "Nếu bạn không yêu cầu đặt lại mật khẩu, hãy bỏ qua email này.",
  },
  magiclink: {
    subject: "Mã đăng nhập V-Lookup",
    intro: "Mã đăng nhập V-Lookup của bạn:",
    ignore: "Nếu bạn không yêu cầu đăng nhập, hãy bỏ qua email này.",
  },
  reauthentication: {
    subject: "Mã xác minh V-Lookup",
    intro: "Mã xác minh thao tác trên tài khoản V-Lookup của bạn:",
    ignore: "Nếu bạn không thực hiện thao tác này, hãy đổi mật khẩu.",
  },
};

export type Email = { subject: string; html: string };

/** Returns null for action types this app does not send. */
export function renderCodeEmail(actionType: string, token: string): Email | null {
  if (!Object.hasOwn(CODE_EMAILS, actionType)) return null;
  // Supabase OTPs are 6-10 digits; anything else must never reach the HTML body.
  if (!/^\d{6,10}$/.test(token)) throw new Error("unexpected token format");

  const { subject, intro, ignore } = CODE_EMAILS[actionType];
  const html = [
    `<p>${intro}</p>`,
    `<p style="font-size:28px;font-weight:bold;letter-spacing:4px">${token}</p>`,
    `<p>Mã có hiệu lực trong ${OTP_TTL_MINUTES} phút và chỉ dùng được một lần. ${ignore}</p>`,
  ].join("\n");
  return { subject, html };
}

function toBase64(text: string): string {
  let binary = "";
  for (const byte of new TextEncoder().encode(text)) binary += String.fromCharCode(byte);
  return btoa(binary);
}

/** RFC 2047 encoded-words, split on code points so each word stays under 75 characters. */
export function encodeHeader(value: string): string {
  const encoder = new TextEncoder();
  const chunks: string[] = [];
  let chunk = "";
  for (const char of value) {
    if (encoder.encode(chunk + char).length > 45) {
      chunks.push(chunk);
      chunk = "";
    }
    chunk += char;
  }
  chunks.push(chunk);
  return chunks.map((part) => `=?UTF-8?B?${toBase64(part)}?=`).join("\r\n ");
}

// Rejects whitespace (including CR/LF) and separators, which would allow header injection
// or extra recipients.
const ADDRESS = /^[^\s@<>(),;:"\\[\]]+@[^\s@<>(),;:"\\[\]]+$/;

/** Builds the base64url value for the Gmail API `raw` field. */
export function buildRawMessage(
  message: Email & { fromName: string; fromAddress: string; to: string },
): string {
  const { fromName, fromAddress, to, subject, html } = message;
  for (const address of [fromAddress, to]) {
    if (!ADDRESS.test(address)) throw new Error("invalid email address");
  }
  const body = toBase64(html).replace(/.{76}/g, "$&\r\n");
  const mime = [
    `From: ${encodeHeader(fromName)} <${fromAddress}>`,
    `To: ${to}`,
    `Subject: ${encodeHeader(subject)}`,
    "MIME-Version: 1.0",
    'Content-Type: text/html; charset="UTF-8"',
    "Content-Transfer-Encoding: base64",
    "",
    body,
  ].join("\r\n");
  return toBase64(mime).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
