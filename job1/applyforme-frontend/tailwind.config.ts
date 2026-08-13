import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0A1412",
        panel: "#101C1A",
        "panel-raised": "#16302B",
        rail: "#1D3E37",
        signal: "#FF8A3D",
        "signal-dim": "#8C4F29",
        mint: "#5EEAD4",
        danger: "#FF6B5C",
        ivory: "#F1F5F3",
        muted: "#8FA39C",
      },
      fontFamily: {
        display: ["var(--font-space-grotesk)", "sans-serif"],
        body: ["var(--font-inter)", "sans-serif"],
        mono: ["var(--font-plex-mono)", "monospace"],
      },
      backgroundImage: {
        "grid-fade":
          "linear-gradient(to bottom, rgba(94,234,212,0.06) 1px, transparent 1px), linear-gradient(to right, rgba(94,234,212,0.06) 1px, transparent 1px)",
      },
      backgroundSize: {
        grid: "28px 28px",
      },
    },
  },
  plugins: [],
};

export default config;
