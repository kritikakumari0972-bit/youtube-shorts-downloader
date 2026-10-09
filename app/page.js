'use client';

import { useState } from 'react';
import styles from './styles.module.css';

const defaultThumbnail = 'https://images.unsplash.com/photo-1492691527719-9d1e07e534b4?auto=format&fit=crop&w=900&q=80';

export default function HomePage() {
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState('');
  const [downloadType, setDownloadType] = useState('video');
  const [selectedQuality, setSelectedQuality] = useState('best');
  const [downloadingFormat, setDownloadingFormat] = useState(null);

  const fetchPreview = async () => {
    if (!url.trim()) {
      setError('Please paste a YouTube URL first.');
      return;
    }

    setLoading(true);
    setError('');
    setPreview(null);

    try {
      const res = await fetch(`/api/preview?url=${encodeURIComponent(url)}`);
      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.error || 'Could not load preview.');
      }

      setPreview(data);
      setSelectedQuality('best');
    } catch (err) {
      setError(err.message || 'Something went wrong while fetching the video.');
      setPreview(null);
    } finally {
      setLoading(false);
    }
  };

  const handleDownload = async (format) => {
    if (!url.trim() || !preview) {
      setError('Please load a video first.');
      return;
    }

    setDownloadingFormat(format);
    try {
      const params = new URLSearchParams({
        url,
        format,
        quality: format === 'video' ? selectedQuality : 'best',
      });

      window.open(`/api/download?${params}`, '_blank');
      
      setTimeout(() => setDownloadingFormat(null), 2000);
    } catch (err) {
      setError('Download failed. Please try again.');
      setDownloadingFormat(null);
    }
  };

  const downloadThumbnail = () => {
    if (!preview?.thumbnail) {
      setError('No thumbnail available.');
      return;
    }

    const link = document.createElement('a');
    link.href = preview.thumbnail;
    link.download = `${preview.title || 'thumbnail'}.jpg`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <main className={styles.page}>
      <div className={styles.container}>
        <div className={styles.card}>
          {/* Header */}
          <header className={styles.header}>
            <div className={styles.titleSection}>
              <h1 className={styles.title}>🎥 YouTube Downloader</h1>
              <p className={styles.subtitle}>Download Shorts & Videos with preview, multiple qualities & audio extraction</p>
            </div>
          </header>

          {/* Main Content */}
          <div className={styles.body}>
            {/* Left Panel - Upload & Preview */}
            <section className={styles.leftPanel}>
              {/* URL Input */}
              <div className={styles.inputSection}>
                <label className={styles.label}>Paste YouTube URL</label>
                <div className={styles.inputRow}>
                  <input
                    className={styles.input}
                    type="text"
                    placeholder="https://www.youtube.com/shorts/... or /watch?v=..."
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    onKeyPress={(e) => e.key === 'Enter' && fetchPreview()}
                  />
                  <button className={styles.primaryButton} onClick={fetchPreview} disabled={loading}>
                    {loading ? '⏳ Loading...' : '🔍 Preview'}
                  </button>
                </div>
              </div>

              {/* Error Message */}
              {error && (
                <div className={styles.errorBox}>
                  <span>⚠️</span>
                  <div>{error}</div>
                  <button className={styles.closeBtn} onClick={() => setError('')}>✕</button>
                </div>
              )}

              {/* Preview Box */}
              {preview ? (
                <div className={styles.previewBox}>
                  <div className={styles.thumbnailWrapper}>
                    <img
                      src={preview.thumbnail || defaultThumbnail}
                      alt={preview.title || 'Video thumbnail'}
                      className={styles.thumbnail}
                    />
                    <div className={styles.badgeContainer}>
                      <span className={styles.badge}>{preview.durationFormatted || 'N/A'}</span>
                    </div>
                  </div>

                  <div className={styles.previewInfo}>
                    <h3 className={styles.videoTitle}>{preview.title}</h3>
                    <p className={styles.videoMeta}>📺 {preview.author || 'Unknown Channel'}</p>
                    <p className={styles.videoMeta}>👁️ {preview.views?.toLocaleString() || 'N/A'} views</p>
                    <p className={styles.videoMeta}>⏱️ {preview.durationFormatted || 'N/A'}</p>
                    <p className={styles.videoMeta}>📊 {preview.formats?.length || 0} quality options available</p>

                    {/* Quality Selector */}
                    <div className={styles.qualitySection}>
                      <label className={styles.label}>Video Quality</label>
                      <select
                        className={styles.select}
                        value={selectedQuality}
                        onChange={(e) => setSelectedQuality(e.target.value)}
                      >
                        <option value="best">Best Available</option>
                        <option value="4k">4K (2160p)</option>
                        <option value="1080">1080p</option>
                        <option value="720">720p</option>
                        <option value="480">480p</option>
                        <option value="360">360p</option>
                        <option value="240">240p</option>
                        <option value="144">144p</option>
                      </select>
                    </div>
                  </div>
                </div>
              ) : (
                <div className={styles.placeholderBox}>
                  <div className={styles.placeholderContent}>
                    <div style={{ fontSize: '3rem', marginBottom: '1rem' }}>🎬</div>
                    <div style={{ fontWeight: 700, marginBottom: '0.5rem' }}>No video loaded yet</div>
                    <div style={{ color: '#cbd5e1' }}>Paste a YouTube URL above to get started</div>
                  </div>
                </div>
              )}
            </section>

            {/* Right Panel - Download Options */}
            <aside className={styles.rightPanel}>
              {/* Download Options */}
              <div className={styles.optionsCard}>
                <h2 className={styles.optionsTitle}>📥 Download Options</h2>

                {/* Download Type Tabs */}
                <div className={styles.tabs}>
                  <button
                    className={`${styles.tab} ${downloadType === 'video' ? styles.tabActive : ''}`}
                    onClick={() => setDownloadType('video')}
                  >
                    🎥 Video
                  </button>
                  <button
                    className={`${styles.tab} ${downloadType === 'audio' ? styles.tabActive : ''}`}
                    onClick={() => setDownloadType('audio')}
                  >
                    🎵 Audio
                  </button>
                  <button
                    className={`${styles.tab} ${downloadType === 'thumbnail' ? styles.tabActive : ''}`}
                    onClick={() => setDownloadType('thumbnail')}
                  >
                    🖼️ Thumbnail
                  </button>
                </div>

                {/* Video Download */}
                {downloadType === 'video' && (
                  <div className={styles.downloadSection}>
                    <p className={styles.sectionHint}>Download video in MP4 format with your preferred quality</p>
                    <button
                      className={styles.downloadButton}
                      onClick={() => handleDownload('video')}
                      disabled={!preview || downloadingFormat === 'video'}
                    >
                      {downloadingFormat === 'video' ? '⏳ Downloading...' : '⬇️ Download Video (MP4)'}
                    </button>
                  </div>
                )}

                {/* Audio Download */}
                {downloadType === 'audio' && (
                  <div className={styles.downloadSection}>
                    <p className={styles.sectionHint}>Extract audio as MP3 (best quality available)</p>
                    <button
                      className={styles.downloadButton}
                      onClick={() => handleDownload('audio')}
                      disabled={!preview || downloadingFormat === 'audio'}
                    >
                      {downloadingFormat === 'audio' ? '⏳ Extracting...' : '🎧 Download Audio (MP3)'}
                    </button>
                  </div>
                )}

                {/* Thumbnail Download */}
                {downloadType === 'thumbnail' && (
                  <div className={styles.downloadSection}>
                    <p className={styles.sectionHint}>Download the video thumbnail in high resolution</p>
                    <button
                      className={styles.downloadButton}
                      onClick={downloadThumbnail}
                      disabled={!preview}
                    >
                      📸 Download Thumbnail (JPG)
                    </button>
                  </div>
                )}
              </div>

              {/* Format Information */}
              {preview?.formats && preview.formats.length > 0 && (
                <div className={styles.infoCard}>
                  <h3 className={styles.infoTitle}>📋 Available Formats</h3>
                  <div className={styles.formatList}>
                    {preview.formats.slice(0, 5).map((fmt, idx) => (
                      <div key={idx} className={styles.formatItem}>
                        <span className={styles.formatLabel}>{fmt.qualityLabel}</span>
                        <span className={styles.formatSize}>{fmt.contentLength ? `${(fmt.contentLength / (1024 * 1024)).toFixed(1)}MB` : 'N/A'}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Features */}
              <div className={styles.featureCard}>
                <h3 className={styles.featureTitle}>✨ Features</h3>
                <ul className={styles.featureList}>
                  <li>✅ Video preview with metadata</li>
                  <li>✅ Multiple quality options (144p-4K)</li>
                  <li>✅ MP4 & MP3 format support</li>
                  <li>✅ Audio extraction</li>
                  <li>✅ High-res thumbnail download</li>
                  <li>✅ Fast & reliable streaming</li>
                  <li>✅ No registration required</li>
                </ul>
              </div>

              {/* Notes */}
              <div className={styles.notesCard}>
                <strong>⚖️ Legal Notice:</strong>
                <p>Use this tool only for videos you own or have permission to download. Respect copyright and YouTube's Terms of Service.</p>
              </div>
            </aside>
          </div>
        </div>
      </div>
    </main>
  );
}
