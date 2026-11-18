import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Icon } from "../components/Icon";
export type Theme = "light" | "dark" | "system";
const valid = (v: string | null): Theme =>
  v === "light" || v === "dark" ? v : "system";
const ThemeContext = createContext<{
  theme: Theme;
  setTheme: (v: Theme) => void;
}>({ theme: "system", setTheme: () => {} });
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return valid(localStorage.getItem("video-service-theme"));
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const dark = theme === "dark" || (theme === "system" && media.matches);
      document.documentElement.dataset.theme = dark ? "dark" : "light";
      document.documentElement.style.colorScheme = dark ? "dark" : "light";
      document
        .querySelector('meta[name="theme-color"]')
        ?.setAttribute("content", dark ? "#0f0f0f" : "#ffffff");
    };
    apply();
    try {
      localStorage.setItem("video-service-theme", theme);
    } catch {
      /* Appearance still works without storage. */
    }
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);
  useEffect(() => {
    const sync = (e: StorageEvent) => {
      if (e.key === "video-service-theme") setTheme(valid(e.newValue));
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);
  return (
    <ThemeContext.Provider value={{ theme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}
export function ThemeSettings() {
  const { theme, setTheme } = useContext(ThemeContext);
  return (
    <fieldset className="theme-settings">
      <legend>Оформление</legend>
      {(
        [
          ["light", "Светлая", "sun"],
          ["dark", "Тёмная", "moon"],
          ["system", "Как в системе", "monitor"],
        ] as const
      ).map(([value, label, icon]) => (
        <label key={value}>
          <Icon name={icon} size={20} />
          <span>{label}</span>
          <input
            type="radio"
            name="appearance"
            value={value}
            checked={theme === value}
            onChange={() => setTheme(value)}
          />
        </label>
      ))}
    </fieldset>
  );
}
