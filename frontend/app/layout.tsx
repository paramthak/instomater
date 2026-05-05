import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Instomator",
  description: "Turn a name and photo into an Instagram reel",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full antialiased dark">
      <body className="bg-zinc-950 text-white h-screen overflow-hidden">{children}</body>
    </html>
  );
}
