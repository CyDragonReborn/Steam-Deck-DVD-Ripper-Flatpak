import logging
import subprocess
import shutil
import re
from pathlib import Path
from typing import Optional

from .config import Config, ENCODER_PRESETS
from .utils import find_binary

logger = logging.getLogger(__name__)


def check_handbrake(config: Config) -> bool:
    try:
        find_binary(config.handbrake_bin)
        return True
    except RuntimeError:
        return False


def check_vaapi_available() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10
        )
        return "h264_vaapi" in result.stdout
    except Exception:
        return False


def check_amf_available() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10
        )
        return "h264_amf" in result.stdout
    except Exception:
        return False


def check_nvenc_available() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10
        )
        return "nvenc" in result.stdout.lower()
    except Exception:
        return False


def check_svt_av1_available() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10
        )
        return "svt_av1" in result.stdout.lower()
    except Exception:
        return False


def get_available_encoders() -> list[str]:
    available = ["x264"]
    if check_vaapi_available():
        available.extend(["h264_vaapi", "hevc_vaapi", "av1_vaapi"])
    if check_amf_available():
        available.extend(["h264_amf", "hevc_amf"])
    if check_nvenc_available():
        available.extend(["nvenc_h264", "nvenc_h265", "nvenc_av1"])
    if check_svt_av1_available():
        available.append("svt_av1")
    return available


def is_vaapi_encoder(encoder: str) -> bool:
    return encoder.endswith("_vaapi")


def is_amf_encoder(encoder: str) -> bool:
    return encoder.endswith("_amf")


def is_nvenc_encoder(encoder: str) -> bool:
    return encoder.startswith("nvenc_")


def is_gpu_encoder(encoder: str) -> bool:
    return is_vaapi_encoder(encoder) or is_amf_encoder(encoder) or is_nvenc_encoder(encoder)


def build_ffmpeg_encode_cmd(config: Config, input_path: Path, output_path: Path) -> list[str]:
    encoder = config.handbrake_encoder

    cmd = [
        config.ffmpeg_bin,
        "-nostdin",
        "-hide_banner",
    ]

    if is_vaapi_encoder(encoder):
        cmd.extend(["-vaapi_device", "/dev/dri/renderD128"])

    cmd.extend(["-i", str(input_path)])

    if is_gpu_encoder(encoder):
        cmd.extend(["-vf", "format=nv12,hwupload"])

    cmd.extend(["-c:v", encoder])

    quality = config.handbrake_quality

    if is_vaapi_encoder(encoder):
        cmd.extend(["-qp", quality])
        if encoder == "av1_vaapi":
            cmd.extend(["-b:v", "0"])
        if config.handbrake_deinterlace:
            vf_parts = ["format=nv12,hwupload", "deinterlace_vaapi"]
            if config.video_scale:
                vf_parts.append(
                    f"scale_vaapi=w={config.video_scale[0]}:h={config.video_scale[1]}"
                )
            cmd[cmd.index("-vf")] = "-vf"
            cmd[cmd.index("-vf") + 1] = ",".join(vf_parts)
    elif is_amf_encoder(encoder):
        quality_map = {"16": "28", "18": "24", "20": "20", "22": "16", "24": "12", "26": "8", "28": "4"}
        amf_quality = quality_map.get(quality, "20")
        cmd.extend(["-quality", "speed", "-qp_i", amf_quality, "-qp_p", amf_quality])
    elif is_nvenc_encoder(encoder):
        cmd.extend(["-preset", "p4", "-cq", quality])
    elif encoder == "x264":
        preset = "medium" if not getattr(config, "gpu_optimized", True) else "fast"
        cmd.extend(["-preset", preset, "-crf", quality])
    elif encoder == "svt_av1":
        cmd.extend(["-preset", "6", "-crf", quality])

    cmd.extend([
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-y",
        str(output_path),
    ])

    return cmd


def build_handbrake_encode_cmd(config: Config, input_path: Path, output_path: Path) -> list[str]:
    encoder = config.handbrake_encoder
    is_gpu = is_gpu_encoder(encoder)
    gpu_opt = getattr(config, "gpu_optimized", True)

    cmd = [
        config.handbrake_bin,
        "-i", str(input_path),
        "-o", str(output_path),
        "-e", encoder,
        "-q", config.handbrake_quality,
        "--crop", "0:0:0:0",
        "-O",
        "--no-markers",
    ]

    if is_gpu:
        cmd.extend(["--encoder-preset", "fast"])
    elif encoder == "x264":
        cmd.extend(["-x", "ref=4:me=umh:subme=8:trellis=2"])
    elif encoder == "svt_av1":
        cmd.extend(["--encoder-preset", "6"])

    if config.handbrake_deinterlace:
        if is_gpu and gpu_opt:
            cmd.append("--deinterlace=bob")
        else:
            cmd.append("--decomb")

    if config.handbrake_denoise and not (is_gpu and gpu_opt):
        cmd.append("--nlmeans=medium")

    if not (is_gpu and gpu_opt):
        sharpen_map = {
            "weak": "ultralight",
            "medium": "light",
            "strong": "medium",
            "verystrong": "strong",
        }
        if config.video_sharpen and config.video_sharpen != "none":
            hb_sharpen = sharpen_map.get(config.video_sharpen, "light")
            cmd.append(f"--unsharp={hb_sharpen}")

        if config.video_scale:
            cmd.extend([
                "--width", str(config.video_scale[0]),
                "--height", str(config.video_scale[1]),
                "--keep-display-aspect",
            ])

    if config.subtitle_mode == "burn":
        cmd.extend(["--subtitle", "1", "--subtitle-burned"])
    elif config.subtitle_mode == "soft":
        cmd.extend(["--subtitle", "1", "--subtitle-default"])

    cmd.extend(["-a", "1", "-E", "copy"])

    return cmd


def encode_to_mp4(
    config: Config,
    input_path: Path,
    output_path: Path,
    progress_callback=None,
) -> Path:
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    encoder = config.handbrake_encoder
    use_ffmpeg = is_gpu_encoder(encoder) or encoder in ("x264", "svt_av1")

    if use_ffmpeg:
        return _encode_ffmpeg(config, input_path, output_path, progress_callback)
    else:
        return _encode_handbrake(config, input_path, output_path)


def _encode_ffmpeg(
    config: Config,
    input_path: Path,
    output_path: Path,
    progress_callback=None,
) -> Path:
    encoder_label = config.get_encoder_label()
    logger.info("Encoding with %s (FFmpeg): %s -> %s", encoder_label, input_path, output_path)

    cmd = build_ffmpeg_encode_cmd(config, input_path, output_path)
    logger.info("FFmpeg command: %s", " ".join(cmd))

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    duration = None
    current_time = 0.0
    speed = 1.0

    for line in process.stderr:
        if "Duration:" in line and duration is None:
            match = re.search(r"Duration: (\d{2}):(\d{2}):(\d{2}\.\d{2})", line)
            if match:
                h, m, s = match.groups()
                duration = int(h) * 3600 + int(m) * 60 + float(s)
        if "time=" in line:
            match = re.search(r"time=(\d{2}):(\d{2}):(\d{2}\.\d{2})", line)
            if match:
                h, m, s = match.groups()
                current_time = int(h) * 3600 + int(m) * 60 + float(s)
            match = re.search(r"speed=([\d.]+)x", line)
            if match:
                speed = float(match.group(1))
            if duration and progress_callback:
                progress = min(current_time / duration * 100, 99)
                progress_callback(progress, speed)

    process.wait()

    if process.returncode != 0:
        stderr = process.stderr.read() if process.stderr else "no output"
        raise RuntimeError(f"FFmpeg encoding failed (rc={process.returncode}):\n{stderr[-2000:]}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg encoding failed: output file not created or empty")

    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info("Encoding complete: %s (%.1f MB)", output_path, size_mb)
    return output_path


def _encode_handbrake(
    config: Config,
    input_path: Path,
    output_path: Path,
) -> Path:
    if not check_handbrake(config):
        raise RuntimeError("HandBrakeCLI not found")

    encoder_label = config.get_encoder_label()
    logger.info("Encoding with %s (HandBrake): %s -> %s", encoder_label, input_path, output_path)

    cmd = build_handbrake_encode_cmd(config, input_path, output_path)
    logger.info("HandBrake command: %s", " ".join(cmd))

    process = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=3600 * 12,
    )

    if process.stdout:
        for line in process.stdout.splitlines():
            if any(kw in line for kw in ["Encoding:", "Done", "ERROR", "Encodes"]):
                logger.info("HB: %s", line.strip())

    if process.returncode != 0:
        error_detail = process.stderr if process.stderr else "no stderr output"
        raise RuntimeError(f"HandBrake encoding failed (rc={process.returncode}):\n{error_detail[-3000:]}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("HandBrake encoding failed: output file not created or empty")

    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info("Encoding complete: %s (%.1f MB)", output_path, size_mb)
    return output_path
