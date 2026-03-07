import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        backdrop: "#090E19",
        brass: "#DFA251",
        cinder: "#121B2D",
        haze: "#425776",
        flare: "#F75E3C"
      },
      keyframes: {
        drift: {
          "0%": { transform: "translateY(0px)", opacity: "0.6" },
          "100%": { transform: "translateY(-12px)", opacity: "1" }
        },
        wipe: {
          "0%": { clipPath: "inset(0 100% 0 0)" },
          "100%": { clipPath: "inset(0 0 0 0)" }
        }
      },
      animation: {
        drift: "drift 3s ease-in-out infinite alternate",
        wipe: "wipe 900ms ease-out forwards"
      }
    }
  },
  plugins: []
};

export default config;
