export const metadata = {
  title: 'YouTube Shorts Downloader',
  description: 'Paste a YouTube Shorts URL and download the video in seconds.',
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
