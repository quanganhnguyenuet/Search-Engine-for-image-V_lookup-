"use client";

import { useSyncExternalStore } from "react";

import { setTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

// The class on <html> is the source of truth: the inline script and other tabs change it too.
function subscribe(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}

const isDarkNow = () => document.documentElement.classList.contains("dark");

/** A light/dark switch. */
export function ThemeToggle({ className }: { className?: string }) {
  const dark = useSyncExternalStore(subscribe, isDarkNow, () => false);

  return (
    <button
      type="button"
      role="switch"
      aria-checked={dark}
      aria-label="Giao diện tối"
      title={dark ? "Chuyển sang giao diện sáng" : "Chuyển sang giao diện tối"}
      onClick={() => setTheme(isDarkNow() ? "light" : "dark")}
      className={cn(
        "inline-flex h-11 shrink-0 items-center rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
        className,
      )}
    >
      {/* Position and icons follow the .dark class in CSS, so they are right before hydration. */}
      <span className="relative flex h-8 w-16 items-center justify-between rounded-full border bg-muted px-2 text-muted-foreground">
        <span className="absolute left-0.5 top-0.5 size-[1.625rem] rounded-full border bg-surface shadow-sm transition-transform duration-200 [.dark_&]:translate-x-8" />
        <svg aria-hidden="true" viewBox="0 0 24 24" className="relative size-4 text-foreground [.dark_&]:text-muted-foreground" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
        </svg>
        <svg aria-hidden="true" viewBox="0 0 24 24" className="relative size-4 [.dark_&]:text-foreground" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        </svg>
      </span>
    </button>
  );
}
