"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";

import {
  AuthShell,
  CodeField,
  EmailField,
  FormAlert,
  linkClass,
  ResendButton,
  SubmitButton,
} from "@/components/auth/ui";
import {
  authErrorMessage,
  clearPendingEmail,
  clockTime,
  readPendingEmail,
  sendErrorMessage,
} from "@/lib/auth";
import { createClient } from "@/lib/supabase/client";

export function VerifyEmailForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  // Read after mounting: storage does not exist during server rendering.
  useEffect(() => {
    const pending = readPendingEmail();
    setEmail(pending.email);
    setError(pending.sendError);
  }, []);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    setNotice("");
    setBusy(true);
    const { error: failure } = await createClient().auth.verifyOtp({
      email: email.trim(),
      token: code,
      type: "email",
    });
    if (failure) {
      setError(authErrorMessage(failure));
      setBusy(false);
      return;
    }
    clearPendingEmail();
    router.replace("/");
    router.refresh();
  };

  const resend = async () => {
    setError("");
    setNotice("");
    if (!email.trim()) {
      setError("Nhập email trước khi gửi lại mã.");
      return false;
    }
    const { error: failure } = await createClient().auth.resend({
      type: "signup",
      email: email.trim(),
    });
    if (failure) {
      setError(sendErrorMessage(failure));
      return false;
    }
    setCode("");
    setNotice(
      `Yêu cầu lúc ${clockTime()}. Nếu email này đang chờ xác nhận, mã mới đã được gửi và mã cũ không còn dùng được.`,
    );
    return true;
  };

  return (
    <AuthShell
      title="Xác nhận email"
      description="Nhập mã trong email chúng tôi vừa gửi. Nhớ kiểm tra cả mục Spam."
      footer={
        <>
          <p>Không nhận được mã? Có thể email này đã có tài khoản.</p>
          <p className="flex gap-4">
            <Link href="/login" className={linkClass}>
              Đăng nhập
            </Link>
            <Link href="/forgot-password" className={linkClass}>
              Quên mật khẩu
            </Link>
          </p>
        </>
      }
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <EmailField value={email} onValue={setEmail} />
        <CodeField value={code} onValue={setCode} />
        <FormAlert error={error} notice={notice} />
        <SubmitButton busy={busy}>Xác nhận</SubmitButton>
        <ResendButton onResend={resend} disabled={busy} />
      </form>
    </AuthShell>
  );
}
