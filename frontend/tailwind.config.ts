import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#26332e",
        muted: "#627164",
        line: "#e6e8e2",
        surface: "#f4f5f0",
        panel: "#ffffff",
        brand: "#ef6a4b",
        primary: "#26332e",
        accent: "#ef6a4b"
      }
    }
  },
  plugins: []
};

export default config;
