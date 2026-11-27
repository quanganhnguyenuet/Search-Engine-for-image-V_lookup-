import type { Metadata } from "next";

import { SignupForm } from "@/components/auth/signup-form";

export const metadata: Metadata = { title: "Đăng ký | V-Lookup" };

export default function Page() {
  return <SignupForm />;
}
