import ytdl from 'ytdl-core';

export const runtime = 'nodejs';
export const maxDuration = 30;

function formatDuration(seconds) {
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  
  if (hrs > 0) return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const url = searchParams.get('url');

  if (!url) {
    return Response.json({ error: 'Missing YouTube URL.' }, { status: 400 });
  }

  // Validate URL
  if (!ytdl.validateURL(url)) {
    return Response.json({ error: 'Invalid YouTube URL. Please provide a valid YouTube link.' }, { status: 400 });
  }

  try {
    const info = await ytdl.getBasicInfo(url);
    const videoDetails = info.videoDetails;
    
    // Get all available formats
    const allFormats = info.formats
      .filter(f => f.hasVideo && f.hasAudio)
      .sort((a, b) => {
        const qualityA = parseInt(b.qualityLabel?.replace(/[^0-9]/g, '') || 0);
        const qualityB = parseInt(a.qualityLabel?.replace(/[^0-9]/g, '') || 0);
        return qualityA - qualityB;
      });

    // Get thumbnail
    const thumbnails = videoDetails.thumbnails || [];
    const thumbnail = thumbnails[thumbnails.length - 1]?.url || null;

    return Response.json({
      title: videoDetails.title,
      author: videoDetails.author?.name || videoDetails.ownerChannelName || 'Unknown',
      thumbnail: thumbnail,
      duration: Number(videoDetails.lengthSeconds || 0),
      durationFormatted: formatDuration(Number(videoDetails.lengthSeconds || 0)),
      views: Number(videoDetails.viewCount || 0),
      channelId: videoDetails.channelId,
      videoId: videoDetails.videoId,
      isLive: videoDetails.isLiveContent || false,
      formats: allFormats.slice(0, 10).map(f => ({
        qualityLabel: f.qualityLabel,
        mimeType: f.mimeType,
        contentLength: f.contentLength,
      })),
      url,
    });
  } catch (error) {
    console.error('[Preview Error]:', error);
    return Response.json(
      { error: 'Unable to load the video. Make sure the URL is public and valid.' },
      { status: 400 }
    );
  }
}
