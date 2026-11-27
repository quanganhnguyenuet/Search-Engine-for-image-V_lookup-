"use client";

import { type KeyboardEvent, type ReactNode, useEffect, useId, useState } from "react";

import { PendingBadge } from "@/components/admin/admin-shell";
import { admin, formatValue, type VocabItem, type VocabKind } from "@/lib/admin";
import { cn } from "@/lib/utils";

export const inputClass =
  "h-11 w-full rounded-md border bg-surface px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:cursor-not-allowed disabled:opacity-60";

/** Suggestions from /admin/vocab for a typed prefix, debounced. */
export function useVocab(kind: VocabKind, text: string, limit = 12): VocabItem[] {
  const [items, setItems] = useState<VocabItem[]>([]);
  useEffect(() => {
    let live = true;
    const timer = window.setTimeout(() => {
      admin.vocab(kind, text, limit).then(
        (result) => live && setItems(result),
        () => live && setItems([]),
      );
    }, 200);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [kind, text, limit]);
  return items;
}

/** A labelled field with its released value and pending marker. */
export function Field({
  label,
  htmlFor,
  pending,
  live,
  changed,
  hint,
  children,
}: {
  label: string;
  htmlFor?: string;
  pending?: boolean;
  /** Released value, shown when the field differs from it. */
  live?: unknown;
  changed?: boolean;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={htmlFor} className="text-sm font-medium">
          {label}
        </label>
        {pending && <PendingBadge />}
        {changed && <span className="text-xs font-medium text-brand">chưa lưu</span>}
      </div>
      {children}
      {(pending || changed) && live !== undefined && (
        <p className="text-xs text-muted-foreground">Đang chạy: {formatValue(live)}</p>
      )}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** Text input with suggestions from a vocabulary (native datalist). */
export function SuggestInput({
  id,
  kind,
  value,
  onValue,
  placeholder,
  disabled,
}: {
  id: string;
  kind: VocabKind;
  value: string;
  onValue: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  const listId = useId();
  const items = useVocab(kind, value);
  return (
    <>
      <input
        id={id}
        list={listId}
        value={value}
        disabled={disabled}
        onChange={(event) => onValue(event.target.value)}
        placeholder={placeholder}
        maxLength={80}
        className={inputClass}
      />
      <datalist id={listId}>
        {items.map((item) => (
          <option key={item.id} value={item.label}>
            {`${item.count} sản phẩm`}
          </option>
        ))}
      </datalist>
    </>
  );
}

/** A list of values as removable chips, plus an input that adds one on Enter. */
export function ChipsInput({
  id,
  kind,
  values,
  onValues,
  placeholder,
  max = 12,
}: {
  id: string;
  kind: VocabKind;
  values: string[];
  onValues: (values: string[]) => void;
  placeholder: string;
  max?: number;
}) {
  const [text, setText] = useState("");
  const listId = useId();
  const items = useVocab(kind, text);
  const add = () => {
    const value = text.trim().replace(/\s+/g, " ");
    if (!value) return;
    if (!values.some((v) => v.toLocaleLowerCase("vi") === value.toLocaleLowerCase("vi")) && values.length < max) {
      onValues([...values, value]);
    }
    setText("");
  };
  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      add();
    } else if (event.key === "Backspace" && !text && values.length) {
      onValues(values.slice(0, -1));
    }
  };
  return (
    <div className="flex flex-col gap-2">
      {values.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label="Giá trị hiện có">
          {values.map((value) => (
            <li key={value} className="inline-flex items-center gap-1 rounded-full border bg-muted py-0.5 pl-3 pr-1 text-sm">
              {value}
              <button
                type="button"
                onClick={() => onValues(values.filter((v) => v !== value))}
                aria-label={`Bỏ ${value}`}
                className="inline-flex size-7 items-center justify-center rounded-full text-muted-foreground hover:bg-background hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
              >
                <span aria-hidden="true">×</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        <input
          id={id}
          list={listId}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKey}
          placeholder={values.length >= max ? `Tối đa ${max} giá trị` : placeholder}
          disabled={values.length >= max}
          maxLength={80}
          className={inputClass}
        />
        <button
          type="button"
          onClick={add}
          disabled={!text.trim()}
          className="h-11 shrink-0 rounded-md border px-3 text-sm font-medium hover:bg-muted disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
        >
          Thêm
        </button>
      </div>
      <datalist id={listId}>
        {items.map((item) => (
          <option key={item.id} value={item.label}>
            {`${item.count} sản phẩm`}
          </option>
        ))}
      </datalist>
    </div>
  );
}

/** Brand picker: an existing brand by name, "no brand", or a new brand from the typed name. */
export function BrandInput({
  id,
  value,
  label,
  onValue,
}: {
  id: string;
  /** brand id, "new:<name>" or null */
  value: string | null;
  /** display name of the current value */
  label: string;
  onValue: (value: string | null, label: string) => void;
}) {
  const [text, setText] = useState(label);
  const [open, setOpen] = useState(false);
  const items = useVocab("brand", text, 8);
  useEffect(() => setText(label), [label]);
  const exact = items.find((item) => item.label.toLocaleLowerCase("vi") === text.trim().toLocaleLowerCase("vi"));
  const choose = (next: string | null, name: string) => {
    onValue(next, name);
    setText(name);
    setOpen(false);
  };
  return (
    <div className="relative">
      <input
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-autocomplete="list"
        value={text}
        maxLength={60}
        placeholder="Gõ tên hãng"
        onChange={(event) => {
          setText(event.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false);
          if (event.key === "Enter") {
            event.preventDefault();
            const name = text.trim();
            if (!name) choose(null, "");
            else if (exact) choose(exact.id, exact.label);
            else choose(`new:${name}`, name);
          }
        }}
        className={inputClass}
      />
      {open && (
        <ul
          id={`${id}-list`}
          role="listbox"
          className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-md border bg-surface py-1 text-sm shadow-lg"
        >
          {items.map((item) => (
            <li key={item.id} role="option" aria-selected={item.id === value}>
              <button
                type="button"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(item.id, item.label)}
                className={cn("flex w-full justify-between gap-3 px-3 py-2 text-left hover:bg-muted", item.id === value && "font-medium text-brand")}
              >
                <span>{item.label}</span>
                <span className="tabular-nums text-muted-foreground">{item.count}</span>
              </button>
            </li>
          ))}
          {text.trim() && !exact && (
            <li role="option" aria-selected={false}>
              <button
                type="button"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(`new:${text.trim()}`, text.trim())}
                className="w-full px-3 py-2 text-left text-brand hover:bg-muted"
              >
                Tạo hãng mới “{text.trim()}”
              </button>
            </li>
          )}
          <li role="option" aria-selected={value === null}>
            <button
              type="button"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(null, "")}
              className="w-full px-3 py-2 text-left text-muted-foreground hover:bg-muted"
            >
              Không có hãng
            </button>
          </li>
        </ul>
      )}
    </div>
  );
}
