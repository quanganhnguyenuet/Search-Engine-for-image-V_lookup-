import type { Metadata } from "next";

import { ChangePasswordForm } from "@/components/auth/change-password-form";

export const metadata: Metadata = { title: "Đổi mật khẩu | V-Lookup" };

export default function Page() {
  return <ChangePasswordForm />;
}
