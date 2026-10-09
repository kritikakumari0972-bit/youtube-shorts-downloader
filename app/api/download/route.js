import ytdl from 'ytdl-core';
import { PassThrough } from 'stream';

export const runtime = 'nodejs';
export const maxDuration = 60;

function sanitizeFilename(filename) {
  return (filename || 'download')
    .replace(/[<>:"/\\|?*]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 100) || 'download';
}

async function getVideoInfo(url) {
  try {
    return await ytdl.getInfo(url);
  } catch (error) {
    throw new Error('Failed to fetch video information');
  }
}

function selectFormat(info, type, quality) {
  const formats = info.formats;

  if (type === 'video') {
    // Filter for video + audio combined or select best
    let videoFormats = formats.filter(f => f.hasVideo && f.hasAudio && f.container === 'mp4');

    if (videoFormats.length === 0) {
      videoFormats = formats.filter(f => f.hasVideo && f.hasAudio);
    }

    if (quality === 'best') {
      return videoFormats[0] || formats.find(f => f.hasVideo && f.hasAudio);
    }

    // Convert quality string to number
    const qualityNum = parseInt(quality);
    if (!isNaN(qualityNum)) {
      const best = videoFormats.find(f => {
        const fmt = parseInt(f.qualityLabel?.replace(/[^0-9]/g, '') || 0);
        return fmt <= qualityNum;
      });
      if (best) return best;
    }

    return videoFormats[0] || formats.find(f => f.hasVideo && f.hasAudio);
  } else if (type === 'audio') {
    // Get audio only formats
    let audioFormats = formats.filter(f => f.hasAudio && !f.hasVideo);
    if (audioFormats.length === 0) {
      audioFormats = formats.filter(f => f.hasAudio);
    }
    return audioFormats.sort((a, b) => (Number(b.bitrate) || 0) - (Number(a.bitrate) || 0))[0];
  }

  return formats.find(f => f.hasVideo && f.hasAudio);
}

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const url = searchParams.get('url');
  const format = searchParams.get('format') || 'video';
  const quality = searchParams.get('quality') || 'best';

  if (!url) {
    return Response.json({ error: 'Missing YouTube URL.' }, { status: 400 });
  }

  if (!ytdl.validateURL(url)) {
    return Response.json({ error: 'Invalid YouTube URL.' }, { status: 400 });
  }

  try {
    const info = await getVideoInfo(url);
    const videoDetails = info.videoDetails;
    const title = sanitizeFilename(videoDetails.title);

    // Select appropriate format
    const selectedFormat = selectFormat(info, format, quality);

    if (!selectedFormat) {
      return Response.json({ error: 'No suitable format found for download.' }, { status: 400 });
    }

    // Set response headers
    const headers = new Headers();

    if (format === 'audio') {
      headers.set('Content-Type', 'audio/mpeg');
      headers.set('Content-Disposition', `attachment; filename="${title}.mp3"`);
    } else {
      headers.set('Content-Type', 'video/mp4');
      headers.set('Content-Disposition', `attachment; filename="${title}.mp4"`);
    }

    headers.set('Cache-Control', 'no-store');
    headers.set('X-Content-Type-Options', 'nosniff');

    // Create a readable stream from the format
    const stream = ytdl.downloadFromInfo(info, { format: selectedFormat });

    return new Response(stream, { headers });
  } catch (error) {
    console.error('[Download Error]:', error.message);
    return Response.json(
      { error: 'Failed to download. The video may be unavailable, private, or region-restricted.' },
      { status: 500 }
    );
  }
}
