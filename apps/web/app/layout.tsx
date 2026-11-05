import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "VietNam Culture — Khám phá nét Việt",
  description: "Khám phá văn hóa Việt Nam qua ảnh: ẩm thực, kiến trúc, phong cảnh, lễ hội, trang phục và thủ công mỹ nghệ.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="vi">
      <body>{children}</body>
    </html>
  );
}

