import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LiveKit Voice Assistant",
  description: "Talk with an AI voice assistant",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
