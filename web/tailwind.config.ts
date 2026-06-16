import type { Config } from "tailwindcss";

// Concordia design language: deep near-black + teal/green glass.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#06080C",
        ink: "#F4F7FA",
        muted: "#9AA7B4",
        subtle: "#7E8A98",
        teal: "#2DD4BF",
        "teal-deep": "#0FA896",
        green: "#34E3A8",
        red: "#FF6B6B",
        amber: "#F5C451",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        display: ["var(--font-outfit)", "var(--font-inter)", "sans-serif"],
      },
      borderRadius: {
        xl2: "18px",
      },
    },
  },
  plugins: [],
};
export default config;
