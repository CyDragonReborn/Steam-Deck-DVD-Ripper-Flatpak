from dataclasses import dataclass, field
from pathlib import Path
import os


ENCODER_PRESETS = {
    "x264": {
        "handbrake_encoder": "x264",
        "label": "x264 (CPU)",
        "supports_preset": True,
    },
    "nvenc_h264": {
        "handbrake_encoder": "nvenc_h264",
        "label": "NVIDIA H.264 (NVENC)",
        "supports_preset": False,
    },
    "nvenc_h265": {
        "handbrake_encoder": "nvenc_h265",
        "label": "NVIDIA H.265/HEVC (NVENC)",
        "supports_preset": False,
    },
    "nvenc_av1": {
        "handbrake_encoder": "nvenc_av1",
        "label": "NVIDIA AV1 (NVENC)",
        "supports_preset": False,
    },
    "svt_av1": {
        "handbrake_encoder": "svt_av1",
        "label": "SVT-AV1 (CPU)",
        "supports_preset": False,
    },
    "h264_vaapi": {
        "handbrake_encoder": "h264_vaapi",
        "label": "AMD H.264 (VAAPI)",
        "supports_preset": False,
    },
    "hevc_vaapi": {
        "handbrake_encoder": "hevc_vaapi",
        "label": "AMD HEVC/H.265 (VAAPI)",
        "supports_preset": False,
    },
    "av1_vaapi": {
        "handbrake_encoder": "av1_vaapi",
        "label": "AMD AV1 (VAAPI)",
        "supports_preset": False,
    },
}

SUBTITLE_MODES = {
    "none": "No subtitles",
    "soft": "Soft subtitles (selectable)",
    "burn": "Burn subtitles into video",
}

SHARPEN_PRESETS = {
    "none": "No sharpening",
    "weak": "Weak sharpening",
    "medium": "Medium sharpening",
    "strong": "Strong sharpening",
    "verystrong": "Very strong sharpening",
}

UPSCALE_PRESETS = {
    "none": "Original resolution",
    "720p": "Upscale to 1280x720",
    "1080p": "Upscale to 1920x1080",
    "1440p": "Upscale to 2560x1440",
    "4k": "Upscale to 3840x2160",
}

UPSCALE_RESOLUTIONS = {
    "none": None,
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "4k": (3840, 2160),
}


@dataclass
class Config:
    temp_dir: Path = field(default_factory=lambda: Path.home() / "rips" / "temp")
    output_dir: Path = field(default_factory=lambda: Path.home() / "rips" / "output")
    keep_temp_files: bool = False

    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    handbrake_bin: str = "HandBrakeCLI"
    lsdvd_bin: str = "lsdvd"
    mediainfo_bin: str = "mediainfo"

    handbrake_preset: str = "HQ 480p30 Stereo"
    handbrake_encoder: str = "nvenc_h264"
    handbrake_quality: str = "20"
    handbrake_audio_copy_first: bool = True
    handbrake_deinterlace: str = "decomb"
    handbrake_denoise: str = "medium"

    subtitle_mode: str = "none"
    video_sharpen: str = "none"
    video_upscale: str = "none"
    video_scale: tuple[int, int] | None = None
    video_brightness: int = 0
    video_contrast: float = 1.0
    video_saturation: float = 1.0
    video_gamma: float = 1.0

    duplicate_hash_sample_seconds: float = 5.0
    duplicate_hash_threshold: float = 0.85
    duplicate_duration_tolerance_sec: float = 30.0
    duplicate_phash_threshold: int = 10

    gpu_optimized: bool = True

    iso_mount_dir: Path = field(default_factory=lambda: Path("/tmp/dvd-ripper-iso"))

    def __post_init__(self):
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.iso_mount_dir.mkdir(parents=True, exist_ok=True)

    def get_encoder_label(self) -> str:
        return ENCODER_PRESETS.get(self.handbrake_encoder, {}).get("label", self.handbrake_encoder)

    def encoder_supports_preset(self) -> bool:
        return ENCODER_PRESETS.get(self.handbrake_encoder, {}).get("supports_preset", False)

    def set_upscale(self, preset: str):
        self.video_upscale = preset
        self.video_scale = UPSCALE_RESOLUTIONS.get(preset)
