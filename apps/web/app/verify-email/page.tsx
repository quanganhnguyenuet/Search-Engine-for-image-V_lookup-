import type { Metadata } from "next";

import { VerifyEmailForm } from "@/components/auth/verify-email-form";

export const metadata: Metadata = { title: "Xác nhận email | V-Lookup" };

export default function Page() {
  return <VerifyEmailForm />;
}
