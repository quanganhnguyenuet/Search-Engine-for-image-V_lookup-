// Run: node --experimental-strip-types --test supabase/functions/send-email/handler.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";

import { buildRawMessage, encodeHeader, renderCodeEmail } from "./email.ts";
import { createGmailSender } from "./gmail.ts";
import { createHandler } from "./handler.ts";

const decodeWords = (header: string) =>
  [...header.matchAll(/=\?UTF-8\?B\?([^?]*)\?=/g)]
    .map(([, data]) => Buffer.from(data, "base64").toString("utf8"))
    .join("");

function decodeMessage(raw: string) {
  const mime = Buffer.from(raw, "base64url").toString("utf8");
  const split = mime.indexOf("\r\n\r\n");
  return {
    headers: mime.slice(0, split),
    html: Buffer.from(mime.slice(split + 4), "base64").toString("utf8"),
  };
}

function setup(actionType = "signup", { to = "user@example.com", token = "123456" } = {}) {
  const sent: string[] = [];
  const handler = createHandler({
    verify: (_body, headers) => {
      if (headers["webhook-signature"] !== "good") throw new Error("bad signature");
      return { user: { email: to }, email_data: { token, email_action_type: actionType } };
    },
    send: async (raw) => {
      sent.push(raw);
    },
    fromName: "V-Lookup",
    fromAddress: "sender@example.com",
  });
  const call = (signature = "good") =>
    handler(
      new Request("http://localhost/", {
        method: "POST",
        headers: { "webhook-signature": signature },
        body: "{}",
      }),
    );
  return { sent, call, handler };
}

for (const actionType of ["signup", "recovery", "magiclink", "reauthentication"]) {
  test(`${actionType} sends the code`, async () => {
    const { sent, call } = setup(actionType);
    const response = await call();
    assert.equal(response.status, 200);
    assert.equal(sent.length, 1);
    const { headers, html } = decodeMessage(sent[0]);
    assert.match(headers, /^To: user@example\.com$/m);
    assert.match(html, />123456</);
  });
}

test("invalid signature sends nothing", async () => {
  const { sent, call } = setup();
  assert.equal((await call("forged")).status, 401);
  assert.equal(sent.length, 0);
});

test("non-POST is rejected", async () => {
  const { handler } = setup();
  assert.equal((await handler(new Request("http://localhost/"))).status, 405);
});

test("notification types succeed without sending", async () => {
  const { sent, call } = setup("password_changed_notification");
  assert.equal((await call()).status, 200);
  assert.equal(sent.length, 0);
});

test("unsupported types fail without sending", async () => {
  for (const actionType of ["invite", "email_change", "toString"]) {
    const { sent, call } = setup(actionType);
    assert.equal((await call()).status, 500);
    assert.equal(sent.length, 0);
  }
});

test("header injection and extra recipients are rejected", async () => {
  for (const to of ["a@example.com\r\nBcc: b@example.com", "a@example.com,b@example.com"]) {
    const { sent, call } = setup("signup", { to });
    assert.equal((await call()).status, 500);
    assert.equal(sent.length, 0);
  }
});

test("a non-numeric token never reaches the HTML", () => {
  assert.throws(() => renderCodeEmail("signup", "<b>1</b>"));
});

test("Vietnamese subjects survive encoding within the line limit", () => {
  const subject = "Mã xác nhận tài khoản V-Lookup – đặt lại mật khẩu và đăng nhập";
  const encoded = encodeHeader(subject);
  assert.equal(decodeWords(encoded), subject);
  for (const line of encoded.split("\r\n ")) assert.ok(line.length <= 75, line);

  const email = renderCodeEmail("signup", "123456")!;
  const raw = buildRawMessage({ ...email, fromName: "V-Lookup", fromAddress: "s@x.vn", to: "u@x.vn" });
  assert.match(raw, /^[A-Za-z0-9_-]+$/);
  assert.match(decodeMessage(raw).html, /Mã xác nhận tài khoản/);
});

function fakeGoogle(sendStatuses: number[], tokenStatus = 200) {
  const calls: string[] = [];
  let issued = 0;
  const fetchImpl = (async (url: string, init: RequestInit) => {
    if (url.includes("oauth2")) {
      calls.push("token");
      if (tokenStatus !== 200) return Response.json({ error: "invalid_grant" }, { status: tokenStatus });
      return Response.json({ access_token: `t${++issued}`, expires_in: 3600 });
    }
    calls.push(`send ${(init.headers as Record<string, string>).Authorization}`);
    return Response.json({}, { status: sendStatuses.shift() ?? 200 });
  }) as unknown as typeof fetch;
  return { calls, fetchImpl };
}

const config = { clientId: "id", clientSecret: "top-secret", refreshToken: "refresh-secret" };
const signal = () => AbortSignal.timeout(1000);

test("access token is cached until shortly before expiry", async () => {
  const { calls, fetchImpl } = fakeGoogle([]);
  let time = 0;
  const send = createGmailSender(config, fetchImpl, () => time);
  await send("a", signal());
  await send("b", signal());
  time = 3_541_000;
  await send("c", signal());
  assert.deepEqual(calls, ["token", "send Bearer t1", "send Bearer t1", "token", "send Bearer t2"]);
});

test("a 401 refreshes the token and retries once", async () => {
  const { calls, fetchImpl } = fakeGoogle([401, 200]);
  await createGmailSender(config, fetchImpl)("a", signal());
  assert.deepEqual(calls, ["token", "send Bearer t1", "token", "send Bearer t2"]);
});

test("other send errors are not retried", async () => {
  const { calls, fetchImpl } = fakeGoogle([503]);
  await assert.rejects(createGmailSender(config, fetchImpl)("a", signal()), /gmail send failed: 503/);
  assert.equal(calls.length, 2);
});

test("token errors report the code without secrets", async () => {
  const { fetchImpl } = fakeGoogle([], 400);
  await assert.rejects(createGmailSender(config, fetchImpl)("a", signal()), (error: Error) => {
    assert.equal(error.message, "token refresh failed: 400 invalid_grant");
    return true;
  });
});
