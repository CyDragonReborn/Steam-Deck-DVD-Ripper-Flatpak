import logging
import re
import tempfile
from pathlib import Path
from typing import Optional

from .config import Config
from .utils import run_cmd, format_duration

logger = logging.getLogger(__name__)


def extract_title_number(filepath: Path) -> int:
    match = re.search(r'title(\d+)', filepath.stem, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 0


def sort_chronologically(config: Config, files: list[Path]) -> list[Path]:
    return sorted(files, key=lambda f: extract_title_number(f))


def create_concat_file(files: list[Path], concat_path: Path) -> None:
    with open(concat_path, "w") as f:
        for filepath in files:
            abs_path = filepath.resolve()
            f.write(f"file '{abs_path}'\n")
    logger.debug("Concat file written to %s with %d entries", concat_path, len(files))


def merge_to_mkv(config: Config, files: list[Path], output_path: Path) -> Path:
    if len(files) == 1:
        logger.info("Only one file, copying instead of merging.")
        import shutil
        shutil.copy2(files[0], output_path)
        return output_path

    sorted_files = sort_chronologically(config, files)

    logger.info("Merging %d files in title order: %s",
                len(sorted_files), [f.name for f in sorted_files])

    total_dur = sum(
        float(run_cmd([config.ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
                       "-of", "csv=p=0", str(f)]).stdout)
        for f in sorted_files
    )
    logger.info("Total duration: %s -> %s", format_duration(total_dur), output_path)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tf:
        concat_path = Path(tf.name)
        create_concat_file(sorted_files, concat_path)

    try:
        cmd = [
            config.ffmpeg_bin,
            "-nostdin",
            "-hide_banner",
            "-v", "warning",
            "-stats",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_path),
            "-c", "copy",
            "-movflags", "+faststart",
            "-y",
            str(output_path),
        ]

        run_cmd(cmd, timeout=int(total_dur + 600))

        if not output_path.exists():
            raise RuntimeError("Merge failed: output file not created")

        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info("Merge complete: %s (%.1f MB)", output_path, size_mb)

    finally:
        if concat_path.exists():
            concat_path.unlink()

    return output_path
