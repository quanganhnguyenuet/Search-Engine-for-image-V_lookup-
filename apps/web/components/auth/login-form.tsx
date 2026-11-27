"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { AuthShell, EmailField, FormAlert, linkClass, PasswordField, SubmitButton } from "@/components/auth/ui";
import { authErrorMessage, rememberPendingEmail, sendErrorMessage } from "@/lib/auth";
import { createClient } from "@/lib/supabase/client";

export function LoginForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    setBusy(true);
    const address = email.trim();
    const supabase = createClient();
    const { error: failure } = await supabase.auth.signInWithPassword({
      email: address,
      password,
    });
    if (failure?.code === "email_not_confirmed") {
      // Send a fresh code. If that fails, the next page must say so; an earlier code still works.
      const { error: resendFailure } = await supabase.auth.resend({
        type: "signup",
        email: address,
      });
      rememberPendingEmail(address, resendFailure ? sendErrorMessage(resendFailure) : "");
      router.push("/verify-email");
      return;
    }
    if (failure) {
      setError(authErrorMessage(failure));
      setBusy(false);
      return;
    }
    router.replace("/");
    router.refresh();
  };

  return (
    <AuthShell
      title="Đăng nhập"
      description="Đăng nhập để tìm kiếm ảnh sản phẩm."
      footer={
        <p>
          Chưa có tài khoản?{" "}
          <Link href="/signup" className={linkClass}>
            Đăng ký
          </Link>
        </p>
      }
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <EmailField value={email} onValue={setEmail} />
        <PasswordField label="Mật khẩu" value={password} onValue={setPassword} />
        <Link href="/forgot-password" className={`${linkClass} -mt-2 self-end text-sm`}>
          Quên mật khẩu?
        </Link>
        <FormAlert error={error} />
        <SubmitButton busy={busy}>Đăng nhập</SubmitButton>
      </form>
    </AuthShell>
  );
}
