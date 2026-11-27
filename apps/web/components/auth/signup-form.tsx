"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { AuthShell, EmailField, FormAlert, linkClass, PasswordField, SubmitButton } from "@/components/auth/ui";
import { authErrorMessage, rememberPendingEmail } from "@/lib/auth";
import { createClient } from "@/lib/supabase/client";

export function SignupForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (password !== confirm) {
      setError("Hai mật khẩu không khớp.");
      return;
    }
    setError("");
    setBusy(true);
    const address = email.trim();
    // An address that already has an account gets the same answer, so it is not revealed.
    const { error: failure } = await createClient().auth.signUp({ email: address, password });
    if (failure) {
      setError(authErrorMessage(failure));
      setBusy(false);
      return;
    }
    rememberPendingEmail(address);
    router.push("/verify-email");
  };

  return (
    <AuthShell
      title="Đăng ký"
      description="Tạo tài khoản bằng email. Chúng tôi sẽ gửi mã xác nhận tới email của bạn."
      footer={
        <p>
          Đã có tài khoản?{" "}
          <Link href="/login" className={linkClass}>
            Đăng nhập
          </Link>
        </p>
      }
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <EmailField value={email} onValue={setEmail} />
        <PasswordField label="Mật khẩu" isNew value={password} onValue={setPassword} />
        <PasswordField label="Nhập lại mật khẩu" isNew hint="" value={confirm} onValue={setConfirm} />
        <FormAlert error={error} />
        <SubmitButton busy={busy}>Đăng ký</SubmitButton>
      </form>
    </AuthShell>
  );
}
