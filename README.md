# 🎥 YouTube Shorts & Videos Downloader

A powerful, fully-featured YouTube video and Shorts downloader built with **Next.js** and **ytdl-core**. Download videos in multiple quality options, extract audio as MP3, and download high-resolution thumbnails.

## ✨ Features

- ✅ **Video Preview** - See metadata before downloading (title, duration, views, channel)
- ✅ **Multiple Quality Options** - Download in 144p to 4K resolution
- ✅ **Video Download** - Get MP4 files in your preferred quality
- ✅ **Audio Extraction** - Extract audio as MP3 format
- ✅ **Thumbnail Download** - Save high-resolution video thumbnails
- ✅ **Fast & Reliable** - Direct streaming without compression
- ✅ **No Registration** - Completely free, no signup required
- ✅ **Vercel Ready** - Deploy instantly to Vercel
- ✅ **Responsive Design** - Works on desktop, tablet, and mobile

## 🚀 Quick Start

### Prerequisites
- Node.js 18+ installed
- npm or yarn package manager

### Local Development

```bash
# Clone or setup the project
git clone https://github.com/kritikakumari0972-bit/youtube-shorts-downloader.git
cd youtube-shorts-downloader

# Install dependencies
npm install

# Run development server
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

### Production Build

```bash
# Build for production
npm run build

# Start production server
npm start
```

## 📦 Deployment to Vercel

### Method 1: Direct Vercel Deploy (Recommended)

1. Push your code to GitHub
2. Go to [Vercel](https://vercel.com/)
3. Click "New Project"
4. Import your GitHub repository
5. Click "Deploy"

### Method 2: Using Vercel CLI

```bash
# Install Vercel CLI
npm install -g vercel

# Deploy
vercel
```

## 🎯 How to Use

1. **Paste YouTube URL** - Copy and paste any YouTube video or Shorts URL
2. **Click Preview** - Instantly see video metadata and available qualities
3. **Select Quality** - Choose your preferred download resolution (144p - 4K)
4. **Download** - Select download type:
   - 🎥 **Video** - Get MP4 in your chosen quality
   - 🎵 **Audio** - Extract audio as MP3
   - 🖼️ **Thumbnail** - Save high-res thumbnail image

## 🛠️ Technology Stack

- **Frontend**: Next.js 14 + React 18
- **Styling**: CSS Modules
- **Backend**: Next.js API Routes (Node.js)
- **YouTube**: ytdl-core library
- **Hosting**: Vercel

## 📋 API Endpoints

### GET `/api/preview`
Fetch video metadata and available formats

**Parameters:**
- `url` (string, required) - YouTube video URL

**Response:**
```json
{
  "title": "Video Title",
  "author": "Channel Name",
  "thumbnail": "https://...",
  "duration": 3600,
  "durationFormatted": "1:00:00",
  "views": 1000000,
  "formats": [
    { "qualityLabel": "1080p", "mimeType": "video/mp4" }
  ]
}
```

### GET `/api/download`
Download video, audio, or thumbnail

**Parameters:**
- `url` (string, required) - YouTube video URL
- `format` (string) - 'video', 'audio' (default: 'video')
- `quality` (string) - '144', '360', '480', '720', '1080', 'best' (default: 'best')

**Response:**
- Binary file stream (MP4, MP3, etc.)

## ⚙️ Configuration

### Environment Variables
Create a `.env.local` file (if needed):

```env
# No env variables required for basic functionality
# Add your own if needed for analytics, etc.
```

## 🐛 Troubleshooting

### "Invalid YouTube URL"
- Ensure the URL is a valid YouTube video or Shorts link
- Try copying directly from your browser address bar

### "Video unavailable"
- Video may be private, deleted, or region-restricted
- Check if the video is accessible in your region

### Download hangs or times out
- Very long videos may take time
- Try selecting a lower quality option
- Check your internet connection

### Audio extraction not working
- Ensure the video has audio content
- Try a different video to test

## 📝 Supported Formats

### Video
- **Format**: MP4 (H.264 video codec)
- **Qualities**: 144p, 240p, 360p, 480p, 720p, 1080p, 1440p, 2160p (4K)
- **Codec**: H.264 (AVC)

### Audio
- **Format**: MP3
- **Bitrate**: 128kbps - 256kbps
- **Sample Rate**: 44.1 kHz

### Thumbnail
- **Format**: JPEG
- **Resolution**: High-resolution (max: 1280x720 or higher)

## ⚠️ Legal Notice

**Important:** This tool is for personal use only. Use it only for:
- Videos you own
- Videos with explicit permission to download
- Educational purposes
- Content you have rights to

**Do not use for:**
- Copyrighted content without permission
- Violating YouTube's Terms of Service
- Commercial purposes
- Redistribution of copyrighted material

Respect creators' rights. Always check video licenses and terms before downloading.

## 📄 License

MIT License - See LICENSE file for details

## 🤝 Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.

## 💬 Support

For issues, questions, or suggestions, please open an issue on GitHub or contact the maintainers.

## 🔗 Links

- [GitHub Repository](https://github.com/kritikakumari0972-bit/youtube-shorts-downloader)
- [YouTube Shorts](https://www.youtube.com/shorts/)
- [ytdl-core Documentation](https://github.com/fent/node-ytdl-core)

---

**Made with ❤️ by Kritika**
