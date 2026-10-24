# Local Watermark Studio (Docker)

A lightweight self-hosted web app for applying text and/or logo watermarks to batches of images. Image processing runs inside the container; there is no third-party image API, database, telemetry, or persistent upload directory. Files are processed in memory and returned as a download or ZIP.

## Features

- Custom text watermark, transparent logo, or both
- Tiled/repeating diagonal watermark with adjustable opacity, size, angle and spacing
- RGB noise on watermark pixels only
- Dual-layer “honey-pot” treatment: a conspicuous corner mark plus a separate soft-edged, low-opacity central mark
- Adjustable watermark color, size, placement, and tiling spacing
- Optional whole-image grain
- WebP, JPEG or PNG output, adjustable lossy quality and maximum image dimension
- Batch processing with ZIP download
- No external network call is needed to process images

## Quick start

Install Docker Desktop, open a terminal in this directory, then run:

```bash
docker compose up --build -d
```

Open <http://localhost:8080>. Stop the app with:

```bash
docker compose down
```

The compose file binds to `127.0.0.1:8080`, so the service is only reachable from the host by default.

## Default settings

- Main watermark text: `OVEN FRESH`
- Tiled/repeated diagonal watermark: on
- Main watermark opacity: 30%
- RGB noise on watermark pixels: 5%
- Dual-layer honey-pot treatment: on
- Corner mark: `© OVEN FRESH`, 95% opacity, bottom-right
- Central soft mark: 14% opacity, 5% text size, 1.5 px blur
- Output: WebP quality 88
- Maximum image dimension: 2400 px
- Whole-image noise: off

These are starting values. Review the export at full size and at typical website display size; the app does not currently render a live image preview before processing.

## Important limitations

- **No watermark is AI-removal-proof.** Tiling, RGB noise, a decoy and a second subject mark may increase removal effort, but an AI editor can still remove or regenerate them. The “honey-pot” approach is a heuristic, not a reliable way to trick an AI system into overlooking another mark.
- RGB noise modifies watermark RGB pixels, not the whole photo. It is not an exact reproduction of GIMP's RGB Noise filter.
- Whole-image noise and low compression quality can degrade product photographs and do not reliably improve protection. Whole-image noise is off by default.
- This app does not embed a robust invisible watermark or create cryptographically signed ownership records. Keep original files, creator information, and dated records separately if ownership evidence matters.
- If you want to make the app reachable from other computers, update the port binding intentionally and configure authentication, TLS, upload limits and a firewall. Do not expose it directly to the public internet.

## Resource use and input limits

- Container resource limits: 768 MB RAM and 2 CPU cores
- Maximum request size: 100 MB total
- Supported extensions: PNG, JPG/JPEG, WebP, BMP and TIFF
- Very large batches may need to be processed in smaller groups.

This is a lightweight starter app, not a security-reviewed upload service.
