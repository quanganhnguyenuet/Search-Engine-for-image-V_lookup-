import type { Metadata } from "next";
import { Be_Vietnam_Pro } from "next/font/google";
import type { ReactNode } from "react";

import { InlineScript } from "@/components/theme/inline-script";
import { THEME_SCRIPT } from "@/lib/theme";

import "./globals.css";

// Drawn for Vietnamese: stacked diacritics stay clear at every size.
const font = Be_Vietnam_Pro({
  subsets: ["latin", "vietnamese"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "V-Lookup",
  description: "Tìm ảnh sản phẩm bằng tiếng Việt.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    // The theme script adds a class to <html> before React hydrates.
    <html lang="vi" suppressHydrationWarning>
      <head>
        <InlineScript html={THEME_SCRIPT} />
      </head>
      <body className={font.className}>{children}</body>
    </html>
  );
}
