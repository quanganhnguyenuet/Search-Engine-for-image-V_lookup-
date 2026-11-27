"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { AccountBar } from "@/components/auth/account-bar";
import { ThemeToggle } from "@/components/theme/theme-toggle";
import { cn } from "@/lib/utils";

const NAV: [string, string][] = [
  ["/admin", "Tổng quan"],
  ["/admin/entities", "Sản phẩm"],
  ["/admin/imports", "Nhập CSV"],
];

export function AdminShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const current = (href: string) =>
    href === "/admin" ? pathname === "/admin" : pathname === href || pathname.startsWith(`${href}/`);

  return (
    <div className="min-h-dvh">
      <header className="border-b bg-surface">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6">
          <Link href="/" className="rounded text-lg font-bold tracking-tight text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
            V-Lookup
          </Link>
          <span className="rounded bg-brand-soft px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-brand">
            Quản trị
          </span>
          <nav aria-label="Quản trị" className="order-last flex w-full gap-1 overflow-x-auto sm:order-none sm:w-auto">
            {NAV.map(([href, label]) => (
              <Link
                key={href}
                href={href}
                aria-current={current(href) ? "page" : undefined}
                className={cn(
                  "inline-flex min-h-11 items-center rounded-md px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
                  current(href) ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                {label}
              </Link>
            ))}
            <Link
              href="/"
              className="inline-flex min-h-11 items-center rounded-md px-3 text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
            >
              Về trang tìm kiếm
            </Link>
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <ThemeToggle />
            <AccountBar />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">{children}</main>
    </div>
  );
}

export function PageTitle({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0 max-w-3xl">
        <h1 className="text-2xl font-bold tracking-tight [overflow-wrap:anywhere] sm:text-3xl">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions}
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-danger/40 bg-danger-soft p-4 text-sm">
      <p>{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="mt-2 font-medium text-brand underline-offset-4 hover:underline">
          Thử lại
        </button>
      )}
    </div>
  );
}

export function PendingBadge({ children = "Chờ áp dụng" }: { children?: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 rounded bg-warn-soft px-1.5 py-0.5 text-xs font-medium text-warn">
      <span aria-hidden="true">●</span>
      {children}
    </span>
  );
}
