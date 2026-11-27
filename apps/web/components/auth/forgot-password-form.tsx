"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import {
  AuthShell,
  CodeField,
  EmailField,
  FormAlert,
  linkClass,
  PasswordField,
  ResendButton,
  SubmitButton,
} from "@/components/auth/ui";
import { authErrorMessage, clockTime, isEmailRateLimit, sendErrorMessage } from "@/lib/auth";
import { createClient } from "@/lib/supabase/client";

type Step = "email" | "code" | "password";

const DESCRIPTION: Record<Exclude<Step, "code">, string> = {
  email: "Nhập email của tài khoản để nhận mã đặt lại mật khẩu.",
  password: "Mã đúng. Đặt mật khẩu mới cho tài khoản.",
};

const requestedNotice = () =>
  `Yêu cầu lúc ${clockTime()}. Hãy dùng mã trong thư đến từ thời điểm này; mã cũ hơn không còn dùng được.`;

export function ForgotPasswordForm() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const sendCode = async () => {
    setError("");
    setNotice("");
    // Supabase also reports success for an address without an account, and sends nothing.
    const { error: failure } = await createClient().auth.resetPasswordForEmail(email.trim());
    if (failure) setError(sendErrorMessage(failure));
    return failure;
  };

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    setNotice("");
    if (step === "password" && password !== confirm) {
      setError("Hai mật khẩu không khớp.");
      return;
    }
    setBusy(true);
    if (step === "email") {
      const failure = await sendCode();
      // When rate limited, a code sent earlier is still valid and must remain enterable.
      if (!failure || isEmailRateLimit(failure)) {
        setNotice(failure ? "" : requestedNotice());
        setStep("code");
      }
    } else if (step === "code") {
      // A correct recovery code signs the user in, which allows the password change.
      const { error: failure } = await createClient().auth.verifyOtp({
        email: email.trim(),
        token: code,
        type: "recovery",
      });
      if (failure) setError(authErrorMessage(failure));
      else setStep("password");
    } else {
      const supabase = createClient();
      const { error: failure } = await supabase.auth.updateUser({ password });
      if (failure) {
        setError(authErrorMessage(failure));
      } else {
        // Whoever knew the old password must not stay signed in elsewhere.
        await supabase.auth.signOut({ scope: "others" });
        router.replace("/");
        router.refresh();
        return;
      }
    }
    setBusy(false);
  };

  return (
    <AuthShell
      title="Đặt lại mật khẩu"
      description={
        step === "code" ? (
          <>
            Nhập mã đặt lại mật khẩu gửi tới <strong className="break-all">{email.trim()}</strong>.
            Mã chỉ được gửi nếu địa chỉ này đã có tài khoản. Nhớ kiểm tra cả mục Spam.
          </>
        ) : (
          DESCRIPTION[step]
        )
      }
      footer={
        step !== "password" && (
          <Link href="/login" className={linkClass}>
            Quay lại đăng nhập
          </Link>
        )
      }
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        {step === "email" && <EmailField value={email} onValue={setEmail} />}
        {step === "code" && <CodeField value={code} onValue={setCode} />}
        {step === "password" && (
          <>
            <PasswordField label="Mật khẩu mới" isNew value={password} onValue={setPassword} />
            <PasswordField label="Nhập lại mật khẩu mới" isNew hint="" value={confirm} onValue={setConfirm} />
          </>
        )}
        <FormAlert error={error} notice={notice} />
        <SubmitButton busy={busy}>
          {step === "email" ? "Gửi mã" : step === "code" ? "Xác nhận mã" : "Đổi mật khẩu"}
        </SubmitButton>
        {step === "code" && (
          <>
            <ResendButton
              disabled={busy}
              onResend={async () => {
                const failure = await sendCode();
                if (!failure) {
                  setCode("");
                  setNotice(requestedNotice());
                }
                return !failure;
              }}
            />
            <button
              type="button"
              className={`${linkClass} self-center text-sm`}
              onClick={() => {
                setCode("");
                setError("");
                setNotice("");
                setStep("email");
              }}
            >
              Đổi email khác
            </button>
          </>
        )}
      </form>
    </AuthShell>
  );
}
