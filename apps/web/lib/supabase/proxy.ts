import { createServerClient } from "@supabase/ssr";
import { type NextRequest, NextResponse } from "next/server";

import { supabaseEnv } from "@/lib/supabase/env";

/** Pages that only make sense while signed out. */
const GUEST_ONLY = new Set(["/login", "/signup", "/verify-email"]);
/**
 * Open to everyone. The password reset page must stay reachable after its code step, which
 * already signs the user in.
 */
const PUBLIC = new Set([...GUEST_ONLY, "/forgot-password"]);

function isAdminPath(pathname: string): boolean {
  return pathname === "/admin" || pathname.startsWith("/admin/");
}

/** Refreshes the session cookies and redirects according to the sign-in state. */
export async function updateSession(request: NextRequest): Promise<NextResponse> {
  let response = NextResponse.next({ request });
  let cacheHeaders: Record<string, string> = {};
  const { url, publishableKey } = supabaseEnv();

  const supabase = createServerClient(url, publishableKey, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll(cookiesToSet, headers) {
        for (const { name, value } of cookiesToSet) request.cookies.set(name, value);
        response = NextResponse.next({ request });
        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options);
        }
        cacheHeaders = headers;
        for (const [key, value] of Object.entries(headers)) response.headers.set(key, value);
      },
    },
  });

  // getClaims verifies the token signature; do not run other code between createServerClient
  // and this call, or refreshed cookies can be lost.
  const { data } = await supabase.auth.getClaims();
  const signedIn = Boolean(data?.claims);
  // Set by the Custom Access Token Hook (supabase/migrations/0001_profiles_roles.sql). This only
  // keeps plain users off the admin pages; the API checks the role on every admin request.
  const isAdmin = data?.claims?.user_role === "admin";

  const { pathname } = request.nextUrl;
  let target: string | null = null;
  if (!signedIn && !PUBLIC.has(pathname)) target = "/login";
  if (signedIn && GUEST_ONLY.has(pathname)) target = "/";
  if (signedIn && !isAdmin && isAdminPath(pathname)) target = "/";
  if (!target) return response;

  const destination = request.nextUrl.clone();
  destination.pathname = target;
  destination.search = "";
  const redirect = NextResponse.redirect(destination);
  // The redirect must carry any refreshed cookies, otherwise the session is dropped.
  for (const cookie of response.cookies.getAll()) redirect.cookies.set(cookie);
  for (const [key, value] of Object.entries(cacheHeaders)) redirect.headers.set(key, value);
  return redirect;
}
