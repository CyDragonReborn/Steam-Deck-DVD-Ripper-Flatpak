# DVD Ripper - Flatpak Edition

from .config import Config, ENCODER_PRESETS, UPSCALE_PRESETS, SUBTITLE_MODES, SHARPEN_PRESETS
from .ripper import scan_dvd, rip_all_titles, select_play_all_titles, DVDTitle, DVDInfo
from .duplicates import deduplicate_files
from .merger import merge_to_mkv
from .encoder import encode_to_mp4, get_available_encoders

__version__ = "1.0.0"
