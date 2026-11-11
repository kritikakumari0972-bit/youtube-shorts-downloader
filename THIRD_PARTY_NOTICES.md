# Third-party notices and source register

NOVA FETCH contains first-party code and may install or run separate open-source components. The MIT license in this repository applies only to NOVA FETCH's original code. It does not replace or override the licenses of third-party projects.

## Components used by the current build

### yt-dlp — local website-extractor compatibility component

- Repository: https://github.com/yt-dlp/yt-dlp
- License of the source repository and PyPI source/wheel: Unlicense.
- The yt-dlp project notes that some standalone executable bundles contain separately licensed third-party code, including GPLv3+ components; those binary bundles have different licensing consequences.
- NOVA FETCH installs the Python package from its source distribution/wheel. It does not download media through a hosted yt-dlp API.
- The optional yt-dlp-ejs package and its bundled components have their own notices; see the upstream repositories.

Upstream licensing statement: https://github.com/yt-dlp/yt-dlp/blob/master/README.md#licensing

### FFmpeg — local media processing tool

- Project: https://ffmpeg.org/
- Most FFmpeg source is LGPL v2.1-or-later. Optional components are GPL v2-or-later; build options and enabled libraries affect the resulting binary's obligations.
- NOVA FETCH invokes the local ffmpeg executable installed in the Docker image. It does not use a hosted transcoding service.
- The actual FFmpeg package/build selected by the OS package repository must be checked before redistributing a prebuilt Docker image or binary. Preserve its corresponding license and source notices as required.

Official licensing guidance: https://ffmpeg.org/legal.html

## Projects reviewed but not included as code in the current build

### VidBee

- Repository: https://github.com/nexmoe/VidBee
- Repository license: MIT.
- VidBee's UI/source has not been copied into NOVA FETCH. Its project lists yt-dlp and FFmpeg among its own upstream tools.
- If any VidBee files are copied in a future commit, preserve the original copyright and MIT license notice.

### Cobalt

- Repository: https://github.com/imputnet/cobalt
- Cobalt API server: AGPL-3.0. If Cobalt API code is copied or modified, the applicable source, attribution and same-license requirements must be followed.
- Cobalt frontend: CC-BY-NC-SA 4.0, a separate non-commercial license. It is not included or copied.
- Cobalt mascot artwork/character materials are not included.
- The current NOVA FETCH build does not call a Cobalt API and does not bundle Cobalt code.

Cobalt license notes: https://github.com/imputnet/cobalt/blob/main/api/README.md#license
Cobalt project credits: https://cobalt.tools/about/credits

## Changes and update procedure

This file records the components known to be used or reviewed by this build. Before adding, vendoring, copying, or distributing any third-party code or binaries:

1. Read the exact license and notices in the exact version being used.
2. Preserve copyright and license notices.
3. Record the upstream version/commit and local modifications.
4. Add the corresponding source and build instructions when the license requires them.
5. Re-check the licenses of transitive dependencies and binary build options.

Do not claim third-party code as original NOVA FETCH code. First-party NOVA FETCH code and the upstream components are separate works with separate ownership and licensing.
