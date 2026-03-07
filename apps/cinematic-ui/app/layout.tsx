import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "WHATIFY — An Alternate History Engine",
  description: "A voice-first cinematic alternate history experience",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, padding: 0, background: "#070604", overflow: "hidden" }}>{children}</body>
    </html>
  );
}
