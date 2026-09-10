import type { Config } from "tailwindcss";

// Concordia design language: deep near-black + teal/green glass.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Kept in lockstep with the :root block in app/globals.css — the app
        // reads these both ways (Tailwind classes AND var(--…)), so a value that
        // drifts here shows up as two slightly different greys on one screen.
        bg: "#06080C",
        ink: "#F4F7FA",
        muted: "#9AA7B4",
        subtle: "#7E8A98",
        teal: "#2DD4BF",
        "teal-deep": "#0FA896",
        green: "#34E3A8",
        red: "#FF6B6B",
        amber: "#F5C451",
        // was MISSING: every `border-hair` in the app silently fell back to
        // preflight's #e5e7eb — a near-WHITE 1px line on a near-black page
        // (the owner's "white line" under the ops header, bright table rules).
        hair: "rgba(255,255,255,0.1)",
      },
      // and make an unqualified `border` default to the same hairline, so a
      // future missing color class fails faint, not white
      borderColor: {
        DEFAULT: "rgba(255,255,255,0.1)",
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
