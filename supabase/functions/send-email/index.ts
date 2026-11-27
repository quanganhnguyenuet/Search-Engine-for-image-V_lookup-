// Supabase Edge Function: sends auth emails through the Gmail API.
// Deploy with --no-verify-jwt; requests are authenticated by the webhook signature.
import { Webhook } from "npm:standardwebhooks@1.0.0";

import { createGmailSender } from "./gmail.ts";
import { createHandler } from "./handler.ts";

function requireEnv(name: string): string {
  const value = Deno.env.get(name);
  if (!value) throw new Error(`missing environment variable: ${name}`);
  return value;
}

// The dashboard shows the secret as "v1,whsec_<base64>"; the library wants only the base64 part.
const webhook = new Webhook(requireEnv("SEND_EMAIL_HOOK_SECRET").replace("v1,whsec_", ""));

Deno.serve(
  createHandler({
    verify: (body, headers) => webhook.verify(body, headers),
    send: createGmailSender({
      clientId: requireEnv("GMAIL_CLIENT_ID"),
      clientSecret: requireEnv("GMAIL_CLIENT_SECRET"),
      refreshToken: requireEnv("GMAIL_REFRESH_TOKEN"),
    }),
    fromName: "V-Lookup",
    fromAddress: requireEnv("GMAIL_SENDER"),
  }),
);
