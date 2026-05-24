#!/usr/bin/env python3
import argparse
import logging
import sys
from pathlib import Path

from .config import Config
from .utils import validate_dependencies, sanitize_filename
from .ripper import scan_dvd, rip_all_titles, select_play_all_titles
from .duplicates import deduplicate_files
from .merger import merge_to_mkv
from .encoder import encode_to_mp4, get_available_encoders

logger = logging.getLogger("dvd-rip")


def setup_logging(verbose=False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_scan(args):
    config = Config()
    device = args.device or "/dev/sr0"

    info = scan_dvd(config, device)
    print(f"\nDVD Titles on {device}:")
    print(f"{'Title':<8} {'Duration':<12} {'Chapters':<10} {'Audio':<8} {'Subs':<8}")
    print("-" * 50)
    for t in info.episodic_titles:
        mins = t.duration_sec / 60
        print(f"{t.number:<8} {mins:<12.1f} {t.chapters:<10} {t.audio_tracks:<8} {t.subtitle_tracks:<8}")
    print(f"\nTotal episodic titles: {len(info.episodic_titles)}")


def cmd_rip(args):
    config = Config()
    config.handbrake_encoder = args.encoder
    config.handbrake_quality = str(args.quality)
    config.handbrake_deinterlace = "decomb" if not args.no_deinterlace else ""
    config.handbrake_denoise = "nlmeans" if not args.no_denoise else ""
    config.subtitle_mode = args.subtitle
    config.set_upscale(args.upscale)
    if args.keep_temp:
        config.keep_temp_files = True

    device = args.device or "/dev/sr0"

    missing = validate_dependencies(config)
    if missing:
        print(f"Missing: {', '.join(missing)}")
        sys.exit(1)

    disc_name = sanitize_filename(args.name or Path(device).stem or "dvd")

    print(f"\nRipping: {device}")
    print(f"Name: {disc_name}")
    print(f"Encoder: {config.get_encoder_label()}")
    print(f"Quality: CRF {config.handbrake_quality}")
    print()

    info = scan_dvd(config, device)

    if args.play_all:
        titles = select_play_all_titles(config, device)
        print(f"Play All mode: {len(titles)} titles")
    else:
        titles = info.episodic_titles
        print(f"Found {len(titles)} episodic titles")

    rip_dir = config.temp_dir / f"{disc_name}" / "rips"
    ripped = rip_all_titles(config, device, rip_dir, disc_name, titles)
    if not ripped:
        print("No files ripped!")
        sys.exit(1)

    print(f"\nRipped {len(ripped)} files")

    print("Checking duplicates...")
    unique = deduplicate_files(config, ripped)
    print(f"Unique files: {len(unique)}")

    merged = config.temp_dir / f"{disc_name}_merged.mkv"
    print("Merging...")
    if len(unique) == 1:
        import shutil
        shutil.copy2(unique[0], merged)
    else:
        merge_to_mkv(config, unique, merged)

    output = config.output_dir / f"{disc_name}.mp4"
    print(f"Encoding to {output}...")
    encode_to_mp4(config, merged, output)

    if not config.keep_temp_files:
        import shutil
        work_dir = rip_dir.parent
        if work_dir.exists():
            shutil.rmtree(work_dir)
        if merged.exists():
            merged.unlink(missing_ok=True)

    print(f"\nDone! Output: {output}")
    print(f"Size: {output.stat().st_size / (1024*1024):.1f} MB")


def main():
    parser = argparse.ArgumentParser(description="DVD Ripper")
    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser("scan", help="Scan DVD for titles")
    scan_parser.add_argument("--device", "-d", default="/dev/sr0")

    rip_parser = subparsers.add_parser("rip", help="Rip a DVD")
    rip_parser.add_argument("--device", "-d", default="/dev/sr0")
    rip_parser.add_argument("--name", "-n")
    rip_parser.add_argument("--encoder", choices=["x264", "h264_vaapi", "hevc_vaapi", "av1_vaapi"], default="h264_vaapi")
    rip_parser.add_argument("--quality", type=int, default=20)
    rip_parser.add_argument("--upscale", choices=["none", "720p", "1080p"], default="none")
    rip_parser.add_argument("--subtitle", choices=["none", "soft", "burn"], default="none")
    rip_parser.add_argument("--play-all", action="store_true")
    rip_parser.add_argument("--no-deinterlace", action="store_true")
    rip_parser.add_argument("--no-denoise", action="store_true")
    rip_parser.add_argument("--keep-temp", action="store_true")

    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if not args.command:
        try:
            from .gtk_app import main as gtk_main
            gtk_main()
        except ImportError:
            parser.print_help()
            sys.exit(1)
        return

    setup_logging(args.verbose)

    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "rip":
        cmd_rip(args)


if __name__ == "__main__":
    main()
