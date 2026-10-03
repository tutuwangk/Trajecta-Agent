import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#171717",
        muted: "#6e6e73",
        line: "#d8d8dc",
        surface: "#f5f5f7",
        panel: "#ffffff",
        brand: "#1d1d1f"
      }
    }
  },
  plugins: []
};

export default config;
