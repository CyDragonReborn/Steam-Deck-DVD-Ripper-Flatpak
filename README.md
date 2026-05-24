# DVD Ripper - Flatpak Edition

A modern DVD ripping application for Linux desktops and the Steam Deck. Rip episodic DVDs, detect duplicates, merge chronologically, and encode to MP4 with **AMD VAAPI GPU-accelerated encoding**.

## Features

- **AMD VAAPI GPU encoding** — H.264, HEVC, and AV1 via the Steam Deck's RDNA 2 iGPU
- **Automatic duplicate detection** — content hash-based deduplication
- **Chronological merging** — episodes merged in correct order
- **Deinterlacing and cleanup** — yadif/VA-API deinterlace
- **Subtitle support** — soft (selectable) or burned-in
- **Upscaling** — 720p, 1080p, 1440p, 4K
- **Play All mode** — all titles merged to a single file
- **ISO and VIDEO_TS support** — no physical disc needed
- **GTK4/Libadwaita UI** — native look on GNOME, KDE, and Steam Deck Desktop Mode

## Steam Deck Usage

### Desktop Mode

1. Switch to Desktop Mode (hold Steam button → Power → Switch to Desktop)
2. Open **Discover** (software center)
3. Search for "DVD Ripper" or install via Flatpak:

```bash
flatpak install flathub io.github.dvdripper.app
```

4. Launch from the application menu
5. Insert a DVD or select an ISO file
6. Click **Scan** to see titles
7. Choose settings and click **Start Rip**
8. Output saves to `~/Videos/DVD-Rips/`

### Gaming Mode

The app is accessible from Gaming Mode via **Non-Steam Games** → **Desktop Applications** after adding it in Desktop Mode.

## Build from Source

### Prerequisites

```bash
# Fedora / SteamOS
sudo dnf install flatpak flatpak-builder

# Ubuntu / Debian
sudo apt install flatpak flatpak-builder
```

### Add Flathub remote

```bash
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
```

### Install SDK runtime

```bash
flatpak install flathub org.gnome.Platform//46 org.gnome.Sdk//46
```

### Build

```bash
git clone https://github.com/CyDragonReborn/dvd-ripper-flatpak.git
cd dvd-ripper-flatpak

flatpak-builder --user --install --force-clean build-dir io.github.dvdripper.app.json
```

### Run

```bash
flatpak run io.github.dvdripper.app
```

## CLI Usage

```bash
# Scan a DVD
flatpak run io.github.dvdripper.app scan

# Rip with AMD VAAPI (default)
flatpak run io.github.dvdripper.app rip --name "Show S01"

# Rip with specific encoder
flatpak run io.github.dvdripper.app rip --name "Show S01" --encoder av1_vaapi --quality 22

# Play All mode
flatpak run io.github.dvdripper.app rip --play-all --name "Movie Collection"
```

## Encoder Options

| Encoder | Speed | Quality | Best For |
|---------|-------|---------|----------|
| `h264_vaapi` | Fast | Good | General use, fastest |
| `hevc_vaapi` | Medium | Better | Smaller files |
| `av1_vaapi` | Slow | Best | Maximum quality/size ratio |
| `x264` | Slow | Best | CPU fallback, no GPU |

On the Steam Deck (RDNA 2 iGPU):
- **H.264 VAAPI**: ~5-10x real-time for 480p source
- **HEVC VAAPI**: ~3-7x real-time
- **AV1 VAAPI**: ~2-5x real-time

A typical 2-hour DVD takes **15-40 minutes** to rip + encode on Steam Deck with VAAPI.

## Troubleshooting

### VAAPI not available
```bash
# Check if VAAPI is working
flatpak run --command=vainfo io.github.dvdripper.app

# Ensure /dev/dri is accessible
ls -la /dev/dri/renderD128
```

### DVD not detected
```bash
# Check device
ls -la /dev/sr0

# Test with lsdvd
flatpak run --command=lsdvd io.github.dvdripper.app /dev/sr0
```

### Permission denied on output
```bash
# The app needs filesystem access. Grant it:
flatpak override --user --filesystem=home io.github.dvdripper.app
```

### Slow encoding
- Ensure you're using a VAAPI encoder, not x264
- Check GPU utilization: `radeontop` or `nvtop`
- Lower quality (higher CRF number) for faster encoding

## Architecture

```
┌─────────────────────────────────────────────┐
│           GTK4/Libadwaita UI                │
│  (Native desktop, Steam Deck optimized)     │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│              Ripper Engine                  │
│  Scan → Rip → Dedupe → Merge → Encode       │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│              FFmpeg + VAAPI                 │
│  /dev/dri/renderD128 (AMD RDNA 2 iGPU)      │
└─────────────────────────────────────────────┘
```

## License

MIT License

## Credits

- **FFmpeg** — video processing and VAAPI encoding
- **lsdvd** — DVD title scanning
- **libdvd-pkg** — DVD decryption
- **GTK4/Libadwaita** — modern desktop UI
- **Flatpak** — sandboxed distribution
