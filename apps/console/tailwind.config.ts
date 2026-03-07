import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0A0F17",
        ember: "#F46E30",
        steel: "#516179",
        storm: "#1B2433",
        mint: "#A7F3D0"
      }
    }
  },
  plugins: []
};

export default config;
