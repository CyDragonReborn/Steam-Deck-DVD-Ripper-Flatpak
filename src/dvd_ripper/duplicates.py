import logging
import json
import struct
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from .config import Config
from .utils import run_cmd, parse_duration_to_seconds, get_file_size_mb

logger = logging.getLogger(__name__)


@dataclass
class VideoInfo:
    path: Path
    duration_sec: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str
    file_size_mb: float
    md5hash: str = ""
    md5hash_mid: str = ""
    md5hash_end: str = ""
    phash: str = ""


def get_video_info(config: Config, path: Path) -> Optional[VideoInfo]:
    try:
        cmd = [
            config.ffprobe_bin,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        result = run_cmd(cmd)
        data = json.loads(result.stdout)

        video_stream = None
        audio_stream = None
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and not video_stream:
                video_stream = s
            elif s.get("codec_type") == "audio" and not audio_stream:
                audio_stream = s

        if not video_stream:
            logger.warning("No video stream found in: %s", path)
            return None

        duration = float(data.get("format", {}).get("duration", 0))
        r_frame_rate = video_stream.get("r_frame_rate", "0/1")
        num, den = r_frame_rate.split("/")
        fps = float(num) / float(den) if float(den) else 0

        return VideoInfo(
            path=path,
            duration_sec=duration,
            width=int(video_stream.get("width", 0)),
            height=int(video_stream.get("height", 0)),
            fps=fps,
            video_codec=video_stream.get("codec_name", "unknown"),
            audio_codec=audio_stream.get("codec_name", "unknown") if audio_stream else "none",
            file_size_mb=get_file_size_mb(path),
        )
    except Exception as e:
        logger.error("Failed to probe %s: %s", path, e)
        return None


def sample_frames(config: Config, path: Path, ts: float, duration: float = 3.0) -> bytes:
    cmd = [
        config.ffmpeg_bin,
        "-nostdin",
        "-hide_banner",
        "-ss", str(ts),
        "-i", str(path),
        "-t", str(duration),
        "-vf", "fps=1,scale=160:120,format=gray",
        "-f", "rawvideo",
        "-pix_fmt", "gray",
        "-",
    ]
    result = run_cmd(cmd, timeout=60, binary=True)
    return result.stdout


def compute_content_hash_3point(config: Config, path: Path, duration_sec: float) -> tuple[str, str, str]:
    import hashlib

    def hash_bytes(data: bytes) -> str:
        return hashlib.md5(data).hexdigest() if data else ""

    h_start = ""
    h_mid = ""
    h_end = ""

    try:
        h_start = hash_bytes(sample_frames(config, path, 10, 3.0))
    except Exception as e:
        logger.warning("Start hash failed for %s: %s", path.name, e)

    mid_ts = max(duration_sec / 2 - 1.5, 15)
    try:
        h_mid = hash_bytes(sample_frames(config, path, mid_ts, 3.0))
    except Exception as e:
        logger.warning("Mid hash failed for %s: %s", path.name, e)

    end_ts = max(duration_sec - 20, 30)
    try:
        h_end = hash_bytes(sample_frames(config, path, end_ts, 3.0))
    except Exception as e:
        logger.warning("End hash failed for %s: %s", path.name, e)

    return h_start, h_mid, h_end


def compute_phash(config: Config, path: Path) -> str:
    cmd = [
        config.ffmpeg_bin,
        "-nostdin",
        "-hide_banner",
        "-ss", "10",
        "-i", str(path),
        "-t", "1",
        "-vf", "fps=1,scale=32:32,format=gray",
        "-f", "image2pipe",
        "-vcodec", "rawvideo",
        "-",
    ]
    try:
        result = run_cmd(cmd, timeout=60, binary=True)
        raw = result.stdout
        if not raw:
            return ""

        pixels = list(raw)
        size = 32 * 32
        if len(pixels) < size:
            return ""

        avg = sum(pixels[:size]) / size
        bits = "".join("1" if pixels[i] > avg else "0" for i in range(size))

        phash_hex = ""
        for i in range(0, len(bits), 4):
            nibble = bits[i:i+4]
            phash_hex += format(int(nibble, 2), "x")

        return phash_hex
    except Exception as e:
        logger.warning("Perceptual hash failed for %s: %s", path, e)
        return ""


def hamming_distance(h1: str, h2: str) -> int:
    if len(h1) != len(h2):
        return 999
    return sum(c1 != c2 for c1, c2 in zip(h1, h2))


def compute_duplicates(config: Config, files: list[Path]) -> list[list[Path]]:
    logger.info("Analyzing %d files for duplicates...", len(files))

    infos = []
    for f in files:
        info = get_video_info(config, f)
        if info:
            infos.append(info)

    if len(infos) < 2:
        logger.info("Only %d file(s), skipping deduplication.", len(infos))
        return []

    logger.info("Computing 3-point content hashes (start/mid/end)...")
    for info in infos:
        info.md5hash, info.md5hash_mid, info.md5hash_end = compute_content_hash_3point(
            config, info.path, info.duration_sec
        )
        logger.info("  %s: start=%s mid=%s end=%s",
                     info.path.name,
                     info.md5hash[:12] if info.md5hash else "???",
                     info.md5hash_mid[:12] if info.md5hash_mid else "???",
                     info.md5hash_end[:12] if info.md5hash_end else "???")

    groups = []
    used = set()

    for i in range(len(infos)):
        if i in used:
            continue
        group = [infos[i]]
        used.add(i)

        for j in range(i + 1, len(infos)):
            if j in used:
                continue

            is_dup = False

            all_start_match = (
                infos[i].md5hash and infos[j].md5hash and
                infos[i].md5hash == infos[j].md5hash
            )
            all_mid_match = (
                infos[i].md5hash_mid and infos[j].md5hash_mid and
                infos[i].md5hash_mid == infos[j].md5hash_mid
            )
            all_end_match = (
                infos[i].md5hash_end and infos[j].md5hash_end and
                infos[i].md5hash_end == infos[j].md5hash_end
            )

            if all_start_match and all_mid_match and all_end_match:
                logger.info("  MATCH (all 3 points identical): %s <-> %s",
                            infos[i].path.name, infos[j].path.name)
                is_dup = True

            if not is_dup:
                dur_diff = abs(infos[i].duration_sec - infos[j].duration_sec)
                size_diff_pct = abs(infos[i].file_size_mb - infos[j].file_size_mb) / infos[i].file_size_mb
                logger.debug("  %s vs %s: dur_diff=%.1fs size_diff=%.1f%% start_match=%s mid_match=%s end_match=%s",
                             infos[i].path.name, infos[j].path.name,
                             dur_diff, size_diff_pct * 100,
                             all_start_match, all_mid_match, all_end_match)

            if is_dup:
                group.append(infos[j])
                used.add(j)

        if len(group) > 1:
            groups.append([g.path for g in group])

    if groups:
        logger.info("Found %d duplicate groups:", len(groups))
        for idx, group in enumerate(groups):
            logger.info("  Group %d:", idx + 1)
            for g in group:
                logger.info("    - %s", g)
    else:
        logger.info("No duplicates found.")

    return groups


def deduplicate_files(config: Config, files: list[Path]) -> list[Path]:
    dup_groups = compute_duplicates(config, files)

    used = set()
    for group in dup_groups:
        used.update(group)

    unique = [f for f in files if f not in used]

    for group in dup_groups:
        best = max(group, key=lambda p: p.stat().st_size)
        unique.append(best)
        for f in group:
            if f != best:
                logger.info("Removing duplicate: %s (keeping %s)", f, best)
                f.unlink()

    unique.sort()
    logger.info("Deduplicated: %d unique files remaining", len(unique))
    return unique
