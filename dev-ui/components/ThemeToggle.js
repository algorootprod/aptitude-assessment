"use client";

export default function ThemeToggle() {
  return (
    <button
      className="tbtn"
      onClick={() => {
        const root = document.documentElement;
        const dark =
          getComputedStyle(root).getPropertyValue("--plane").trim() === "#0d0d0d";
        root.setAttribute("data-theme", dark ? "light" : "dark");
      }}
    >
      ◐ Theme
    </button>
  );
}
