import ytdl from 'ytdl-core';
import { Readable } from 'stream';

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
    let videoFormats = formats.filter(f => f.hasVideo && f.hasAudio && f.container === 'mp4');

    if (videoFormats.length === 0) {
      videoFormats = formats.filter(f => f.hasVideo && f.hasAudio);
    }

    if (quality === 'best') {
      return videoFormats[0] || formats.find(f => f.hasVideo && f.hasAudio);
    }

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
    let audioFormats = formats.filter(f => f.hasAudio && !f.hasVideo);
    if (audioFormats.length === 0) {
      audioFormats = formats.filter(f => f.hasAudio);
    }
    return audioFormats.sort((a, b) => (Number(b.bitrate) || 0) - (Number(a.bitrate) || 0))[0];
  }

  return formats.find(f => f.hasVideo && f.hasAudio);
}

export const handler = async (event) => {
  if (event.httpMethod !== 'GET') {
    return {
      statusCode: 405,
      body: JSON.stringify({ error: 'Method not allowed' }),
    };
  }

  const url = event.queryStringParameters?.url;
  const format = event.queryStringParameters?.format || 'video';
  const quality = event.queryStringParameters?.quality || 'best';

  if (!url) {
    return {
      statusCode: 400,
      body: JSON.stringify({ error: 'Missing YouTube URL.' }),
    };
  }

  if (!ytdl.validateURL(url)) {
    return {
      statusCode: 400,
      body: JSON.stringify({ error: 'Invalid YouTube URL.' }),
    };
  }

  try {
    const info = await getVideoInfo(url);
    const videoDetails = info.videoDetails;
    const title = sanitizeFilename(videoDetails.title);

    const selectedFormat = selectFormat(info, format, quality);

    if (!selectedFormat) {
      return {
        statusCode: 400,
        body: JSON.stringify({ error: 'No suitable format found for download.' }),
      };
    }

    // Get stream from ytdl
    const stream = ytdl.downloadFromInfo(info, { format: selectedFormat });

    // Convert stream to buffer
    const chunks = [];
    
    return new Promise((resolve) => {
      stream.on('data', (chunk) => {
        chunks.push(chunk);
      });

      stream.on('end', () => {
        const buffer = Buffer.concat(chunks);
        const base64 = buffer.toString('base64');

        const fileExtension = format === 'audio' ? 'mp3' : 'mp4';
        const contentType = format === 'audio' ? 'audio/mpeg' : 'video/mp4';

        resolve({
          statusCode: 200,
          headers: {
            'Content-Type': contentType,
            'Content-Disposition': `attachment; filename="${title}.${fileExtension}"`,
            'Cache-Control': 'no-store',
            'Content-Length': buffer.length.toString(),
          },
          body: base64,
          isBase64Encoded: true,
        });
      });

      stream.on('error', (error) => {
        console.error('[Download Stream Error]:', error.message);
        resolve({
          statusCode: 500,
          body: JSON.stringify({ error: 'Failed to process download stream.' }),
        });
      });
    });
  } catch (error) {
    console.error('[Download Error]:', error.message);
    return {
      statusCode: 500,
      body: JSON.stringify({
        error: 'Failed to download. The video may be unavailable, private, or region-restricted.',
      }),
    };
  }
};
