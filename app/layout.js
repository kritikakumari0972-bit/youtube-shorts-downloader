import './globals.css';

export const metadata = {
  title: 'YouTube Shorts & Videos Downloader',
  description: 'Download YouTube Shorts & Videos with preview, multiple quality options, and audio extraction.',
  viewport: 'width=device-width, initial-scale=1',
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="UTF-8" />
        <meta name="theme-color" content="#0f172a" />
      </head>
      <body>{children}</body>
    </html>
  );
}
