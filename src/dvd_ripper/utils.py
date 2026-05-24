import subprocess
import logging
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def run_cmd(cmd: list[str], check: bool = True, timeout: Optional[int] = None, binary: bool = False) -> subprocess.CompletedProcess:
    logger.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=not binary, timeout=timeout)
    if check and result.returncode != 0:
        logger.error("Command failed (rc=%d): %s", result.returncode, result.stderr)
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result


def find_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Required binary '{name}' not found. Install it first.")
    return path


def validate_dependencies(config) -> None:
    for bin_name in [config.ffmpeg_bin, config.ffprobe_bin, config.lsdvd_bin]:
        find_binary(bin_name)
    try:
        find_binary(config.handbrake_bin)
    except RuntimeError:
        logger.warning("HandBrakeCLI not found. Final encoding step will be skipped.")


def sanitize_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip()


def format_duration(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def parse_duration_to_seconds(duration_str: str) -> float:
    parts = duration_str.strip().split(":")
    if len(parts) == 3:
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def get_file_hash(path: Path, sample_size: int = 8192) -> str:
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(sample_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def get_file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


def is_iso_file(path: str) -> bool:
    return Path(path).suffix.lower() == ".iso"


def mount_iso(iso_path: str, mount_dir: Path) -> str:
    if not Path(iso_path).exists():
        raise FileNotFoundError(f"ISO file not found: {iso_path}")

    if not shutil.which("mount"):
        raise RuntimeError("mount command not found. Install it to use ISO files.")

    mount_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Mounting ISO %s to %s", iso_path, mount_dir)

    try:
        run_cmd(["sudo", "mount", "-o", "loop,ro", iso_path, str(mount_dir)])
        logger.info("ISO mounted successfully")
        return str(mount_dir)
    except Exception as e:
        raise RuntimeError(f"Failed to mount ISO: {e}")


def unmount_iso(mount_dir: Path) -> bool:
    try:
        if not shutil.which("umount"):
            logger.warning("umount command not found")
            return False

        run_cmd(["sudo", "umount", str(mount_dir)])
        logger.info("ISO unmounted from %s", mount_dir)
        return True
    except Exception as e:
        logger.warning("Failed to unmount ISO: %s", e)
        return False


def resolve_input_path(input_path: str, config) -> tuple[str, Optional[Path]]:
    if is_iso_file(input_path):
        mounted_path = mount_iso(input_path, config.iso_mount_dir)
        return mounted_path, config.iso_mount_dir

    return input_path, None
