import type { NextRequest } from "next/server";

import { updateSession } from "@/lib/supabase/proxy";

export function proxy(request: NextRequest) {
  return updateSession(request);
}

export const config = {
  // Everything except Next.js internals, static files and the API. /api/ is rewritten to the
  // API, which checks the token itself (images are public).
  matcher: [
    "/((?!api/|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|webp|ico)$).*)",
  ],
};
