import type { Metadata } from "next";

import { ForgotPasswordForm } from "@/components/auth/forgot-password-form";

export const metadata: Metadata = { title: "Đặt lại mật khẩu | V-Lookup" };

export default function Page() {
  return <ForgotPasswordForm />;
}
