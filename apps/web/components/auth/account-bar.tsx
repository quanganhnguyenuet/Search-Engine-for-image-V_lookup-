"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { linkClass } from "@/components/auth/ui";
import { Button } from "@/components/ui/button";
import { createClient } from "@/lib/supabase/client";

/** Signed-in address with the account actions. */
export function AccountBar() {
  const router = useRouter();
  const pathname = usePathname();
  const [email, setEmail] = useState("");
  // Only decides whether to show the link; the admin pages and API check the role themselves.
  const [isAdmin, setIsAdmin] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    void createClient()
      .auth.getClaims()
      .then(({ data }) => {
        setEmail(data?.claims.email ?? "");
        setIsAdmin(data?.claims.user_role === "admin");
      });
  }, []);

  const signOut = async () => {
    setLeaving(true);
    setFailed(false);
    // "local" signs out this browser only.
    const { error } = await createClient().auth.signOut({ scope: "local" });
    if (error) {
      setFailed(true);
      setLeaving(false);
      return;
    }
    router.replace("/login");
    router.refresh();
  };

  return (
    <nav aria-label="Tài khoản" className="ml-auto flex flex-wrap items-center justify-end gap-x-4 gap-y-1 text-sm">
      <span className="hidden min-w-0 truncate text-muted-foreground sm:inline">{email}</span>
      {isAdmin && !pathname.startsWith("/admin") && (
        <Link href="/admin" className={linkClass}>
          Quản trị
        </Link>
      )}
      <Link href="/account/password" className={linkClass}>
        Đổi mật khẩu
      </Link>
      <Button variant="outline" className="h-11" onClick={() => void signOut()} disabled={leaving}>
        {leaving ? "Đang đăng xuất…" : "Đăng xuất"}
      </Button>
      {failed && (
        <p role="alert" className="w-full text-right text-danger">
          Chưa đăng xuất được. Thử lại.
        </p>
      )}
    </nav>
  );
}
