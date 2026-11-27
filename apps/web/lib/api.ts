import { createClient } from "@/lib/supabase/client";

/** fetch for the API routes that need a signed-in user. */
export async function authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  // getSession refreshes an expired access token before returning it. The API verifies the token.
  const { data } = await createClient().auth.getSession();
  const headers = new Headers(init.headers);
  if (data.session) headers.set("Authorization", `Bearer ${data.session.access_token}`);
  const response = await fetch(url, { ...init, headers });
  if (response.status === 401) {
    throw new Error(
      "Máy chủ tìm kiếm không chấp nhận phiên đăng nhập này. Hãy đăng xuất rồi đăng nhập lại.",
    );
  }
  return response;
}
