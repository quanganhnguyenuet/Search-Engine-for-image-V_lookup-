import type { Metadata } from "next";

import { LoginForm } from "@/components/auth/login-form";

export const metadata: Metadata = { title: "Đăng nhập | V-Lookup" };

export default function Page() {
  return <LoginForm />;
}
