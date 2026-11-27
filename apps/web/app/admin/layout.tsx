import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AdminShell } from "@/components/admin/admin-shell";

export const metadata: Metadata = { title: "Quản trị · V-Lookup" };

// proxy.ts sends signed-out visitors to /login and plain users to /; the API checks the role on
// every admin request.
export default function AdminLayout({ children }: { children: ReactNode }) {
  return <AdminShell>{children}</AdminShell>;
}
