import ytdl from 'ytdl-core';

export const runtime = 'nodejs';

function sanitizeTitle(value) {
  return (value || 'youtube-short')
    .replace(/[<>:"/\\|?*]+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 90) || 'youtube-short';
}

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const url = searchParams.get('url');

  if (!url) {
    return Response.json({ error: 'Missing YouTube URL.' }, { status: 400 });
  }

  try {
    const info = await ytdl.getBasicInfo(url);
    const video = info.videoDetails;
    const formats = info.formats.filter((f) => f.hasVideo && f.hasAudio && f.container === 'mp4');
    const bestFormat = formats.sort((a, b) => (Number(b.qualityLabel?.replace(/[^0-9]/g, '')) || 0) - (Number(a.qualityLabel?.replace(/[^0-9]/g, '')) || 0))[0] || info.formats.find((f) => f.hasVideo && f.hasAudio);

    return Response.json({
      title: video.title,
      author: video.ownerChannelName,
      thumbnail: video.thumbnails?.at(-1)?.url || video.thumbnail?.thumbnails?.at(-1)?.url || '',
      duration: Number(video.lengthSeconds || 0),
      quality: bestFormat?.qualityLabel || 'Best available',
      url,
    });
  } catch (error) {
    return Response.json(
      { error: 'Unable to load the video. Please make sure the URL is public and valid.' },
      { status: 400 }
    );
  }
}
