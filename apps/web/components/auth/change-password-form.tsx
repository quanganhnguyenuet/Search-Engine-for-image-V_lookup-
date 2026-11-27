"use client";

import Link from "next/link";
import { type FormEvent, useState } from "react";

import { AuthShell, FormAlert, linkClass, PasswordField, SubmitButton } from "@/components/auth/ui";
import { authErrorMessage } from "@/lib/auth";
import { createClient } from "@/lib/supabase/client";

export function ChangePasswordForm() {
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const change = async (): Promise<string> => {
    const supabase = createClient();
    const { data, error: userFailure } = await supabase.auth.getUser();
    const email = data.user?.email;
    if (userFailure || !email) return "Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.";

    // Checks the current password even if the dashboard setting that enforces it is off.
    const { error: signInFailure } = await supabase.auth.signInWithPassword({
      email,
      password: current,
    });
    if (signInFailure?.code === "invalid_credentials") return "Mật khẩu hiện tại không đúng.";
    if (signInFailure) return authErrorMessage(signInFailure);

    const { error: failure } = await supabase.auth.updateUser({
      password,
      current_password: current,
    });
    if (failure) return authErrorMessage(failure);

    await supabase.auth.signOut({ scope: "others" });
    return "";
  };

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    setNotice("");
    if (password !== confirm) {
      setError("Hai mật khẩu mới không khớp.");
      return;
    }
    setBusy(true);
    const message = await change();
    if (message) {
      setError(message);
    } else {
      setCurrent("");
      setPassword("");
      setConfirm("");
      setNotice("Đã đổi mật khẩu. Các thiết bị khác đã được đăng xuất.");
    }
    setBusy(false);
  };

  return (
    <AuthShell
      showcase={false}
      title="Đổi mật khẩu"
      footer={
        <Link href="/" className={linkClass}>
          Quay lại trang tìm kiếm
        </Link>
      }
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <PasswordField label="Mật khẩu hiện tại" value={current} onValue={setCurrent} />
        <PasswordField label="Mật khẩu mới" isNew value={password} onValue={setPassword} />
        <PasswordField label="Nhập lại mật khẩu mới" isNew hint="" value={confirm} onValue={setConfirm} />
        <FormAlert error={error} notice={notice} />
        <SubmitButton busy={busy}>Đổi mật khẩu</SubmitButton>
      </form>
    </AuthShell>
  );
}
