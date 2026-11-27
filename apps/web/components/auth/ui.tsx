"use client";

import { type InputHTMLAttributes, type ReactNode, useEffect, useId, useState } from "react";

import { SearchShowcase } from "@/components/auth/search-showcase";
import { ThemeToggle } from "@/components/theme/theme-toggle";
import { Button } from "@/components/ui/button";
import { CODE_LENGTH, MIN_PASSWORD_LENGTH, RESEND_COOLDOWN_SECONDS } from "@/lib/auth";

export function AuthShell({
  title,
  description,
  children,
  footer,
  showcase = true,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  /** Show the example search beside the form. Off for signed-in account pages. */
  showcase?: boolean;
}) {
  return (
    <div
      className={
        showcase
          ? "relative flex min-h-dvh flex-col lg:grid lg:grid-cols-[minmax(0,6fr)_minmax(0,5fr)]"
          : "relative flex min-h-dvh flex-col"
      }
    >
      <ThemeToggle className="absolute right-4 top-3 z-10" />
      {showcase && <SearchShowcase />}
      <main
        className={`mx-auto flex w-full max-w-md flex-1 flex-col gap-6 px-5 py-8 sm:py-12 ${
          showcase ? "lg:justify-center" : "justify-center"
        }`}
      >
        {!showcase && <p className="text-lg font-bold tracking-tight text-brand">V-Lookup</p>}
        <header className="flex flex-col gap-2">
          <h1 className="text-3xl font-bold tracking-tight">{title}</h1>
          {description && <p className="text-muted-foreground">{description}</p>}
        </header>
        {children}
        {footer && <div className="flex flex-col items-start text-sm text-muted-foreground">{footer}</div>}
      </main>
    </div>
  );
}

export const linkClass =
  "inline-flex min-h-11 items-center rounded font-medium text-brand underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

const inputClass =
  "h-12 w-full rounded-md border bg-surface px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-60";

type FieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, "onChange" | "value"> & {
  label: string;
  value: string;
  onValue: (value: string) => void;
  hint?: string;
};

export function Field({ label, value, onValue, hint, className, ...props }: FieldProps) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        value={value}
        onChange={(event) => onValue(event.target.value)}
        aria-describedby={hint ? `${id}-hint` : undefined}
        className={`${inputClass} ${className ?? ""}`}
        {...props}
      />
      {hint && (
        <p id={`${id}-hint`} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
    </div>
  );
}

export function EmailField(props: { value: string; onValue: (value: string) => void }) {
  return (
    <Field
      label="Email"
      type="email"
      name="email"
      autoComplete="email"
      inputMode="email"
      required
      {...props}
    />
  );
}

export function PasswordField({
  isNew = false,
  ...props
}: Omit<FieldProps, "type"> & { isNew?: boolean }) {
  const [visible, setVisible] = useState(false);
  const id = useId();
  const hint = props.hint ?? (isNew ? `Ít nhất ${MIN_PASSWORD_LENGTH} ký tự.` : undefined);
  const { label, value, onValue, ...rest } = props;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <div className="relative">
        <input
          {...rest}
          id={id}
          type={visible ? "text" : "password"}
          value={value}
          onChange={(event) => onValue(event.target.value)}
          autoComplete={isNew ? "new-password" : "current-password"}
          minLength={isNew ? MIN_PASSWORD_LENGTH : undefined}
          aria-describedby={hint ? `${id}-hint` : undefined}
          required
          className={`${inputClass} pr-16`}
        />
        <button
          type="button"
          onClick={() => setVisible((current) => !current)}
          aria-pressed={visible}
          aria-label={`Hiện ${label.toLowerCase()}`}
          className="absolute inset-y-0 right-0 min-w-14 rounded-md px-3 text-sm font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
        >
          {visible ? "Ẩn" : "Hiện"}
        </button>
      </div>
      {hint && (
        <p id={`${id}-hint`} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
    </div>
  );
}

export function CodeField({ value, onValue }: { value: string; onValue: (value: string) => void }) {
  return (
    <Field
      label={`Mã ${CODE_LENGTH} chữ số`}
      name="code"
      type="text"
      inputMode="numeric"
      autoComplete="one-time-code"
      pattern={`\\d{${CODE_LENGTH}}`}
      title={`Nhập đủ ${CODE_LENGTH} chữ số`}
      required
      value={value}
      // No maxLength: it would cut a pasted "123 456" before the spaces are removed.
      onValue={(next) => onValue(next.replace(/\D/g, "").slice(0, CODE_LENGTH))}
      className="text-center font-mono text-2xl tracking-[0.5em]"
    />
  );
}

export function FormAlert({ error, notice }: { error?: string; notice?: string }) {
  return (
    // The live regions stay mounted so that screen readers announce new messages. While empty,
    // the wrapper is taken out of the layout so that it adds no gap to the form.
    <div className={error || notice ? "flex flex-col gap-2" : "sr-only"}>
      <p
        role="alert"
        className={error ? "rounded-md border border-danger/40 bg-danger-soft p-3 text-sm" : "sr-only"}
      >
        {error}
      </p>
      <p role="status" className={notice ? "rounded-md bg-brand-soft p-3 text-sm" : "sr-only"}>
        {notice}
      </p>
    </div>
  );
}

export function SubmitButton({ busy, children }: { busy: boolean; children: ReactNode }) {
  return (
    <Button type="submit" variant="brand" size="lg" className="h-12 w-full" disabled={busy}>
      {busy ? "Đang xử lý…" : children}
    </Button>
  );
}

/** "Send a new code" with the same cooldown that Supabase enforces. */
export function ResendButton({
  onResend,
  disabled,
}: {
  /** Resolves to true when the request was accepted, which restarts the cooldown. */
  onResend: () => Promise<boolean>;
  disabled?: boolean;
}) {
  // A code has just been sent whenever this button first appears.
  const [seconds, setSeconds] = useState(RESEND_COOLDOWN_SECONDS);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (seconds <= 0) return;
    const timer = window.setTimeout(() => setSeconds((current) => current - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [seconds]);

  return (
    <Button
      type="button"
      variant="outline"
      className="h-11 w-full"
      disabled={disabled || sending || seconds > 0}
      onClick={async () => {
        setSending(true);
        if (await onResend()) setSeconds(RESEND_COOLDOWN_SECONDS);
        setSending(false);
      }}
    >
      {seconds > 0 ? `Gửi lại mã sau ${seconds} giây` : "Gửi lại mã"}
    </Button>
  );
}
