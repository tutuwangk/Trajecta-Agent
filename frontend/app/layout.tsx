import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Trajecta 迹旅 · 把想去的地方串成旅程",
  description: "从旅行笔记出发，安排每天的行程、地点与路线。"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
