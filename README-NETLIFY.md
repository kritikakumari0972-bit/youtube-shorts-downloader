# 🎥 YouTube Shorts & Videos Downloader - Netlify Edition

A powerful, fully-featured YouTube video and Shorts downloader built with **Next.js** and **Netlify Functions**. Download videos in multiple quality options, extract audio as MP3, and download high-resolution thumbnails.

## ✨ Features

- ✅ **Video Preview** - See metadata before downloading (title, duration, views, channel)
- ✅ **Multiple Quality Options** - Download in 144p to 4K resolution
- ✅ **Video Download** - Get MP4 files in your preferred quality
- ✅ **Audio Extraction** - Extract audio as MP3 format
- ✅ **Thumbnail Download** - Save high-resolution video thumbnails
- ✅ **Fast & Reliable** - Direct streaming without compression
- ✅ **No Registration** - Completely free, no signup required
- ✅ **Netlify Ready** - Deploy instantly to Netlify with serverless functions
- ✅ **Responsive Design** - Works on desktop, tablet, and mobile

## 🚀 Quick Start - Netlify Deployment

### Prerequisites
- Node.js 18+ installed
- Netlify account (free at [netlify.com](https://netlify.com))
- GitHub account with this repository

### Method 1: Deploy with Netlify (1-Click)

1. Fork or clone this repository
2. Go to [Netlify](https://netlify.com) and sign in
3. Click **"Add new site" → "Import an existing project"**
4. Select **GitHub** and choose this repository
5. Click **"Deploy"**

### Method 2: Deploy with Netlify CLI

```bash
# Install Netlify CLI
npm install -g netlify-cli

# Clone and setup
git clone https://github.com/kritikakumari0972-bit/youtube-shorts-downloader.git
cd youtube-shorts-downloader
npm install

# Deploy to Netlify
netlify deploy --prod
```

### Method 3: Local Development

```bash
# Install dependencies
npm install

# Run with Netlify Functions locally
npm run netlify:dev

# Or use standard Next.js dev server
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

## 📋 Project Structure

```
.
├── app/
│   ├── page.js                 # Main UI component
│   ├── layout.js               # Next.js layout
│   ├── globals.css             # Global styles
│   └── styles.module.css       # Component styles
├── netlify/
│   └── functions/
│       ├── preview.js          # API function to fetch video info
│       ├── download.js         # API function to download/extract
│       └── status.js           # Health check endpoint
├── netlify.toml                # Netlify configuration
├── next.config.mjs             # Next.js configuration
├── package.json                # Dependencies
└── README.md                   # This file
```

## 🎯 How to Use

1. **Paste YouTube URL** - Copy and paste any YouTube video or Shorts URL
2. **Click Preview** - Instantly see video metadata and available qualities
3. **Select Quality** - Choose your preferred download resolution (144p - 4K)
4. **Download** - Select download type:
   - 🎥 **Video** - Get MP4 in your chosen quality
   - 🎵 **Audio** - Extract audio as MP3
   - 🖼️ **Thumbnail** - Save high-res thumbnail image

## 🔧 API Endpoints (Netlify Functions)

### GET `/.netlify/functions/preview`
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

### GET `/.netlify/functions/download`
Download video, audio, or thumbnail

**Parameters:**
- `url` (string, required) - YouTube video URL
- `format` (string) - 'video' or 'audio' (default: 'video')
- `quality` (string) - '144', '360', '480', '720', '1080', 'best' (default: 'best')

**Response:**
- Binary file stream (MP4, MP3, etc.)

## 📦 Technology Stack

- **Frontend**: Next.js 14 + React 18
- **Backend**: Netlify Functions (Serverless)
- **Styling**: CSS Modules
- **YouTube API**: ytdl-core library
- **Hosting**: Netlify (free tier supported)

## ⚙️ Netlify Configuration

The `netlify.toml` file is pre-configured:

```toml
[build]
  command = "npm run build"
  functions = "netlify/functions"
  publish = ".next"

[functions]
  directory = "netlify/functions"
  node_bundler = "esbuild"
```

## 🌍 Environment Variables

No environment variables required! The app works out of the box.

Optional (for future enhancements):
```env
# Add custom analytics or monitoring
NEXTJS_DEBUG=false
```

## 🔒 Security & Performance

- **Request Timeout**: 30 seconds for preview, 60 seconds for download
- **CORS Enabled**: Cross-origin requests allowed
- **No Data Storage**: Videos are streamed directly, not stored
- **Input Validation**: All URLs are validated before processing
- **Error Handling**: Graceful error messages for failed requests

## 🐛 Troubleshooting

### "Functions failed to initialize"
- Ensure you have `netlify.toml` in root directory
- Run `npm install` to install dependencies
- Check Node.js version is 18+

### "Video preview fails"
- Verify the YouTube URL is valid and public
- Some videos may be region-restricted
- Try a different video to test

### "Download hangs or times out"
- Netlify Functions have a 26-second execution timeout on free tier
- For longer videos, consider upgrading to Pro
- Try lower quality options for faster downloads

### "CORS errors"
- CORS is already enabled in the functions
- Check browser console for specific error messages

## 📈 Performance Tips

1. **Lower Quality Downloads** - Faster for large videos
2. **Audio Only** - Smallest file size, fastest extraction
3. **Thumbnail Download** - Always instant
4. **Retry Failed Downloads** - Wait 30 seconds and try again

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

## 🎁 Netlify Benefits

- **Free Hosting**: Deploy for free on Netlify's global CDN
- **Serverless Functions**: No server management
- **Auto Deployments**: Push to GitHub, auto-deploy
- **SSL/HTTPS**: Free SSL certificate included
- **Custom Domain**: Connect your own domain
- **Build Preview**: Preview changes before going live

## 🔗 Links

- [GitHub Repository](https://github.com/kritikakumari0972-bit/youtube-shorts-downloader)
- [Netlify](https://netlify.com)
- [ytdl-core Docs](https://github.com/fent/node-ytdl-core)
- [Next.js Docs](https://nextjs.org/docs)

## 💬 Support

For issues, questions, or suggestions:
- Open an issue on GitHub
- Check Netlify Deploy logs
- Review browser console errors

## 📄 License

MIT License - Free to use and modify

---

**Made with ❤️ by Kritika | Powered by Netlify**

✨ **Enjoying this tool? Star us on GitHub!** ⭐
