import type { Metadata } from "next";
import { Rubik } from "next/font/google";
import "./globals.css";
import { TooltipProvider } from "@/components/ui/tooltip";

// Rubik: covers Hebrew and Latin.
const rubik = Rubik({
  variable: "--font-rubik",
  subsets: ["latin", "hebrew"],
  weight: ["300", "400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "Sales Call Coach",
  description: "ElevenLabs voice agent call coaching dashboard",
};

// Applies the saved theme and language before the first paint (no flash of the wrong mode/direction).
const bootScript = `try{var t=localStorage.getItem('coach.theme');if(t==='dark')document.documentElement.classList.add('dark');var l=localStorage.getItem('coach.lang');if(l==='he'){document.documentElement.lang='he';document.documentElement.dir='rtl';}}catch(e){}`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" dir="ltr" suppressHydrationWarning className={`${rubik.variable} h-full antialiased`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: bootScript }} />
      </head>
      <body className="min-h-full flex flex-col font-sans">
        <TooltipProvider>{children}</TooltipProvider>
      </body>
    </html>
  );
}
