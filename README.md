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
    const title = sanitizeTitle(video.title);
    const stream = ytdl.downloadFromInfo(info, {
      quality: 'highest',
      filter: 'audioandvideo',
    });

    const headers = new Headers();
    headers.set('Content-Type', 'video/mp4');
    headers.set('Content-Disposition', `attachment; filename="${title}.mp4"`);
    headers.set('Cache-Control', 'no-store');

    return new Response(stream, { headers });
  } catch (error) {
    return Response.json(
      { error: 'Unable to download this video. Check the URL or make sure the video is public.' },
      { status: 400 }
    );
  }
}
