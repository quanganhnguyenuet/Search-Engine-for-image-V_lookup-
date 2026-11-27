/**
 * A script that runs during HTML parsing. React warns about script tags it renders on the
 * client, so there it is marked as plain text (pattern from the Next.js "preventing flash" guide).
 */
export function InlineScript({ html }: { html: string }) {
  return (
    <script
      type={typeof window === "undefined" ? "text/javascript" : "text/plain"}
      suppressHydrationWarning
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
