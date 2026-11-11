# NOVA Native format and compatibility matrix

This document describes the first-party native engine. It is intentionally a subset of the wider website coverage available through the optional local yt-dlp compatibility component.

## Direct media and public pages

| Source | Native behavior | Notes |
|---|---|---|
| Direct HTTP media URL | Supported | Streams to disk with a configurable file-size limit, validated redirects and transient-request retries. |
| Public HTML5 page | Partial | Resolves directly exposed video, audio, source, Open Graph, Twitter player-stream and JSON-LD media URLs. It does not run page JavaScript or use browser cookies. |
| MP3 conversion | Supported with FFmpeg | Converts a locally downloaded audio source. |

## HLS

| Capability | Status |
|---|---|
| Static media playlists | Supported |
| Master playlist quality selection | Supported when height metadata is present |
| Separate TYPE=AUDIO rendition | Supported; default/autoselect preference is honored |
| EXT-X-MAP initialization resource | Supported |
| EXT-X-BYTERANGE media segments | Supported, including implicit offsets for repeated ranges of the same resource |
| EXT-X-MAP BYTERANGE | Supported |
| Transient GET retry | Supported |
| Live or unfinished playlists | Not supported |
| Nested master playlists | Not supported |
| AES or other encrypted playlists | Not supported; non-NONE key methods are rejected |
| Native subtitle renditions | Partial: static HLS master playlists with declared plain WebVTT renditions; language selection and timestamp-map normalization; packaged as a VTT sidecar |

Every range request must receive HTTP 206 Partial Content and an exact matching Content-Range. If the server ignores the Range header or returns a mismatched/incomplete range, NOVA fails the job instead of appending the wrong bytes.

## MPEG-DASH

| Capability | Status |
|---|---|
| Static one-Period MPD | Supported |
| Dynamic/live MPD | Not supported |
| BaseURL hierarchy | Supported at MPD, Period, AdaptationSet and Representation levels |
| SegmentTemplate | Supported |
| SegmentTimeline | Supported, with bounded expansion |
| Duration-based templates | Supported when a static Period duration is known |
| Template variables | RepresentationID, Bandwidth, Number, Time, formatted Number, and escaped dollar literals |
| SegmentList and initialization resource | Supported |
| Separate audio/video AdaptationSets | Supported; video quality and audio representation are selected independently and muxed with local FFmpeg |
| Multiple Periods | Not supported |
| SegmentBase | Not supported |
| DASH Initialization range and SegmentURL mediaRange | Not supported |
| Text/subtitle-only AdaptationSets | Not downloaded by the native media path |
| ContentProtection / DRM | Rejected |

A separate audio/video DASH source requires local FFmpeg for the final stream-copy mux. NOVA does not transcode the tracks during muxing. It chooses WebM when both selected tracks are WebM, Matroska for mixed WebM/non-WebM tracks, and MP4 for other track pairs. FFmpeg errors are surfaced instead of claiming a successful output.

## Network and resource safety

- URLs and every redirect are validated before the request is followed.
- Redirect count, manifest size, segment count and total downloaded bytes are bounded.
- Temporary connection failures and selected transient HTTP statuses are retried a small, bounded number of times. Retry-After is capped.
- HLS byte-range responses are checked against their requested offsets and lengths.
- DASH XML containing a DOCTYPE or ENTITY declaration is rejected.
- Encrypted media and access-control circumvention are out of scope.
- A failed job is not presented as a completed download.

## Website compatibility

NOVA Native only resolves public media exposed in the supported metadata and manifest formats. Native subtitles are limited to static HLS master playlists declaring plain WebVTT renditions. The selected subtitle language is normalized using each segment's X-TIMESTAMP-MAP, including MPEGTS 33-bit rollover, and returned in a ZIP containing the media and VTT sidecar. Direct-media subtitles, fragmented-MP4 subtitle tracks and DASH subtitles remain outside the native subset. A site that requires JavaScript execution, account authentication, cookies, signed private endpoints or a site-specific extraction flow may need the optional local compatibility component. That component runs in the same NOVA FETCH installation and remains third-party software under its own license.

NOVA FETCH does not bypass DRM, paywalls, private-account restrictions or other access controls. Only download media that you own or have permission to save.
