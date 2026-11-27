// Light/dark theme. Until the visitor picks one, the page follows the device setting.

export const THEME_KEY = "vlookup:theme";

export type Theme = "light" | "dark";

/**
 * Runs while the HTML is parsed, so the first paint already has the right theme. It also keeps
 * following the device and other tabs afterwards. Storage may be blocked, hence the try.
 */
export const THEME_SCRIPT = `(function () {
  var root = document.documentElement;
  var media = window.matchMedia("(prefers-color-scheme: dark)");
  function stored() {
    try { return localStorage.getItem("${THEME_KEY}"); } catch (e) { return null; }
  }
  function apply() {
    var theme = stored();
    root.classList.toggle("dark", theme ? theme === "dark" : media.matches);
  }
  apply();
  media.addEventListener("change", function () { if (!stored()) apply(); });
  window.addEventListener("storage", function (event) {
    if (event.key === "${THEME_KEY}" || event.key === null) apply();
  });
})();`;

export function setTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
  try {
    window.localStorage.setItem(THEME_KEY, theme);
  } catch {}
}
