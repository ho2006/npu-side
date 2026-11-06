"use client";

import { useEffect, useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";

const THEME_KEY = "npu-side.theme";
const THEME_CYCLE = ["light", "dark", "system"] as const;
type ThemeMode = (typeof THEME_CYCLE)[number];

function isThemeMode(value: string | null): value is ThemeMode {
  return value === "light" || value === "dark" || value === "system";
}

function applyTheme(mode: ThemeMode) {
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const dark = mode === "dark" || (mode === "system" && systemDark);
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.dataset.themeMode = mode;
}

export function ThemeSwitcher() {
  const [mode, setMode] = useState<ThemeMode | null>(null);

  useEffect(() => {
    const stored = localStorage.getItem(THEME_KEY);
    const next = isThemeMode(stored) ? stored : "system";
    applyTheme(next);
    setMode(next);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      const current = localStorage.getItem(THEME_KEY);
      if (!isThemeMode(current) || current === "system") applyTheme("system");
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  function cycleTheme() {
    const current = mode ?? "system";
    const next = THEME_CYCLE[(THEME_CYCLE.indexOf(current) + 1) % THEME_CYCLE.length] ?? "system";
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
    setMode(next);
  }

  return (
    <Button size="icon" onClick={cycleTheme} aria-label={mode ? `当前主题 ${mode}` : "主题"}>
      <Monitor className="hidden [html[data-theme-mode=system]_&]:block" />
      <Sun className="hidden dark:block [html[data-theme-mode=system]_&]:hidden" />
      <Moon className="block dark:hidden [html[data-theme-mode=system]_&]:hidden" />
    </Button>
  );
}
