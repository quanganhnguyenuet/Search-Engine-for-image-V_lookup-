// HTTP handling for the Supabase "Send Email" auth hook.
import { buildRawMessage, renderCodeEmail } from "./email.ts";
import type { SendRaw } from "./gmail.ts";

// Supabase gives the whole hook invocation 5 seconds.
const SEND_TIMEOUT_MS = 4000;

type HookPayload = {
  user: { email: string };
  email_data: { token: string; email_action_type: string };
};

export type HandlerDeps = {
  /** Checks the webhook signature and returns the parsed payload; throws if invalid. */
  verify: (body: string, headers: Record<string, string>) => unknown;
  send: SendRaw;
  fromName: string;
  fromAddress: string;
};

// The message is passed back to Supabase Auth, so it must not carry internal details.
function failure(status: number, message: string): Response {
  return Response.json({ error: { http_code: status, message } }, { status });
}

export function createHandler(deps: HandlerDeps): (request: Request) => Promise<Response> {
  return async (request) => {
    if (request.method !== "POST") return failure(405, "method not allowed");

    const body = await request.text();
    let payload: HookPayload;
    try {
      // The function URL is public: the signature is the only authentication.
      payload = deps.verify(body, Object.fromEntries(request.headers)) as HookPayload;
    } catch {
      return failure(401, "invalid signature");
    }

    const actionType = payload.email_data.email_action_type;
    // Security notices are optional; failing them would fail the user's action.
    if (actionType.endsWith("_notification")) return Response.json({});

    try {
      const email = renderCodeEmail(actionType, payload.email_data.token);
      if (!email) {
        console.error(`unsupported email type: ${actionType}`);
        return failure(500, "unsupported email type");
      }
      const raw = buildRawMessage({
        ...email,
        fromName: deps.fromName,
        fromAddress: deps.fromAddress,
        to: payload.user.email,
      });
      await deps.send(raw, AbortSignal.timeout(SEND_TIMEOUT_MS));
    } catch (error) {
      // Never log the code or the recipient.
      console.error(`${actionType}: ${error instanceof Error ? error.message : error}`);
      return failure(500, "could not send email");
    }
    return Response.json({});
  };
}
