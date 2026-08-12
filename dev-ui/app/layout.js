import "./globals.css";

export const metadata = {
  title: "Daily 20 — aptitude-assessment test UI",
  description: "Manual test client for the aptitude-assessment service.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
