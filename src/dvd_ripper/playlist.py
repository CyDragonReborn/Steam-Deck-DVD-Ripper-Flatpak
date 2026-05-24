import logging
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from .utils import run_cmd

logger = logging.getLogger(__name__)


def detect_playlist_file(dvd_path: str) -> Optional[Path]:
    path = Path(dvd_path)

    if path.is_file():
        return None

    search_dirs = [path]
    if (path / "VIDEO_TS").is_dir():
        search_dirs.append(path / "VIDEO_TS")
    if (path / "BDMV" / "PLAYLIST").is_dir():
        search_dirs.append(path / "BDMV" / "PLAYLIST")

    for search_dir in search_dirs:
        for pattern in ["*.mpl", "*.mpls", "*.m3u", "*.m3u8", "*.pls", "*.xspf"]:
            for f in sorted(search_dir.glob(pattern)):
                logger.info("Found playlist file: %s", f)
                return f

        if search_dir.name == "VIDEO_TS":
            ifo_files = sorted(search_dir.glob("VTS_*_0.IFO"))
            if ifo_files:
                logger.info("Found DVD IFO files (contain PGC playlist data): %d files", len(ifo_files))
                return ifo_files[0]

        if search_dir.name == "PLAYLIST" or (search_dir / "index.bdmv").exists():
            logger.info("Found Blu-ray structure at %s", search_dir.parent)
            return None

    logger.debug("No playlist file found in %s", dvd_path)
    return None


def parse_ifo_vts_order(ifo_path: Path) -> list[int]:
    try:
        with open(ifo_path, "rb") as f:
            data = f.read()

        if len(data) < 12:
            return []

        header = data[:12].decode("ascii", errors="ignore")
        if not header.startswith("DVDVIDEO-VTS"):
            return []

        pgci_srp_offset = struct.unpack(">I", data[0xCC:0xD0])[0] * 2
        if pgci_srp_offset >= len(data) - 8:
            return []

        pgc_count = struct.unpack(">H", data[pgci_srp_offset + 4:pgci_srp_offset + 6])[0]

        title_numbers = []
        offset = pgci_srp_offset + 8

        for i in range(min(pgc_count, 99)):
            if offset + 8 > len(data):
                break

            pgc_type = data[offset]
            entry_offset = struct.unpack(">I", data[offset + 4:offset + 8])[0] * 2

            if pgc_type == 0x01 and entry_offset > 0 and entry_offset < len(data) - 20:
                pgn = struct.unpack(">H", data[entry_offset + 14:entry_offset + 16])[0]
                if pgn > 0 and pgn not in title_numbers:
                    title_numbers.append(pgn)

            offset += 8

        if title_numbers:
            logger.info("Parsed IFO PGC order: %d entries from %s", len(title_numbers), ifo_path.name)
            return title_numbers

    except Exception as e:
        logger.debug("IFO parsing failed for %s: %s", ifo_path, e)

    return []


def parse_lsdvd_chronological(input_path: str) -> list[int]:
    try:
        result = run_cmd(["lsdvd", input_path])
        output = result.stdout

        titles = []
        longest_track = None

        for line in output.splitlines():
            line = line.strip()
            if line.startswith("Title:"):
                match = re.match(r"Title:\s+(\d+),\s*Length:\s+([\d:.]+)\s*Chapters:\s+(\d+)", line)
                if match:
                    num = int(match.group(1))
                    dur_str = match.group(2).strip()
                    chapters = int(match.group(3))

                    parts = dur_str.split(":")
                    if len(parts) == 3:
                        duration_sec = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                    elif len(parts) == 2:
                        duration_sec = float(parts[0]) * 60 + float(parts[1])
                    else:
                        duration_sec = 0.0

                    titles.append((num, duration_sec, chapters))

            if line.startswith("Longest track:"):
                match = re.match(r"Longest track:\s+(\d+)", line)
                if match:
                    longest_track = int(match.group(1))

        if not titles:
            return []

        episodic = [
            (num, dur, ch) for num, dur, ch in titles
            if 300 <= dur <= 7200 and ch > 0
        ]

        if not episodic:
            episodic = [(num, dur, ch) for num, dur, ch in titles if dur > 60]

        if longest_track and longest_track in [t[0] for t in episodic]:
            episodic = [t for t in episodic if t[0] != longest_track]

        return [t[0] for t in episodic]

    except Exception as e:
        logger.warning("lsdvd chronological parse failed: %s", e)
        return []


def parse_mpls_playlist(mpls_path: Path) -> list[int]:
    try:
        with open(mpls_path, "rb") as f:
            data = f.read()

        playlist_items = []
        text = data.decode("utf-16-be", errors="ignore")

        for match in re.finditer(r"(\d{5})\.m2ts", text):
            playlist_items.append(int(match.group(1)))

        if not playlist_items:
            for match in re.finditer(rb"(\d{5})\.m2ts", data):
                playlist_items.append(int(match.group(1)))

        if playlist_items:
            logger.info("Parsed MPLS playlist: %d items from %s", len(playlist_items), mpls_path)
            return playlist_items
    except Exception as e:
        logger.warning("Failed to parse MPLS playlist %s: %s", mpls_path, e)

    return []


def parse_m3u_playlist(m3u_path: Path) -> list[str]:
    entries = []
    try:
        with open(m3u_path, "r", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                entries.append(line)
        if entries:
            logger.info("Parsed M3U playlist: %d entries from %s", len(entries), m3u_path)
    except Exception as e:
        logger.warning("Failed to parse M3U playlist %s: %s", m3u_path, e)
    return entries


def parse_xspf_playlist(xspf_path: Path) -> list[str]:
    entries = []
    try:
        tree = ET.parse(xspf_path)
        root = tree.getroot()
        ns = {"xspf": "http://xspf.org/ns/0/"}
        for track in root.findall(".//xspf:track", ns):
            location = track.find("xspf:location", ns)
            if location is not None and location.text:
                entries.append(location.text)
        if entries:
            logger.info("Parsed XSPF playlist: %d entries from %s", len(entries), xspf_path)
    except Exception as e:
        logger.warning("Failed to parse XSPF playlist %s: %s", xspf_path, e)
    return entries


def parse_pls_playlist(pls_path: Path) -> list[str]:
    entries = []
    try:
        with open(pls_path, "r", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.lower().startswith("file"):
                    parts = line.split("=", 1)
                    if len(parts) == 2 and parts[1]:
                        entries.append(parts[1])
        if entries:
            logger.info("Parsed PLS playlist: %d entries from %s", len(entries), pls_path)
    except Exception as e:
        logger.warning("Failed to parse PLS playlist %s: %s", pls_path, e)
    return entries


def parse_playlist(playlist_path: Path) -> dict:
    suffix = playlist_path.suffix.lower()

    if suffix == ".ifo":
        items = parse_ifo_vts_order(playlist_path)
        return {"type": "ifo", "items": items, "path": playlist_path}
    elif suffix in (".mpl", ".mpls"):
        items = parse_mpls_playlist(playlist_path)
        return {"type": "mpls", "items": items, "path": playlist_path}
    elif suffix in (".m3u", ".m3u8"):
        items = parse_m3u_playlist(playlist_path)
        return {"type": "m3u", "items": items, "path": playlist_path}
    elif suffix == ".xspf":
        items = parse_xspf_playlist(playlist_path)
        return {"type": "xspf", "items": items, "path": playlist_path}
    elif suffix == ".pls":
        items = parse_pls_playlist(playlist_path)
        return {"type": "pls", "items": items, "path": playlist_path}

    logger.warning("Unknown playlist format: %s", playlist_path)
    return {"type": "unknown", "items": [], "path": playlist_path}


def apply_playlist_order(title_numbers: list[int], playlist_items: list[int]) -> list[int]:
    ordered = []
    for item in playlist_items:
        if item in title_numbers:
            ordered.append(item)

    for t in title_numbers:
        if t not in ordered:
            ordered.append(t)

    return ordered


def get_chronological_order(input_path: str) -> list[int]:
    playlist_path = detect_playlist_file(input_path)
    if playlist_path:
        playlist_data = parse_playlist(playlist_path)
        if playlist_data["items"]:
            all_titles = parse_lsdvd_chronological(input_path)
            if playlist_data["type"] == "ifo":
                return apply_playlist_order(all_titles, playlist_data["items"])
            elif playlist_data["type"] == "mpls":
                return apply_playlist_order(all_titles, playlist_data["items"])

    return parse_lsdvd_chronological(input_path)
