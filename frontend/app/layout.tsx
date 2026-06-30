import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "旅行路线整理",
  description: "把旅行笔记整理成可执行路线"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
