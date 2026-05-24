import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, GObject

import os
import sys
import json
import logging
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

from .config import Config, ENCODER_PRESETS, SUBTITLE_MODES, SHARPEN_PRESETS, UPSCALE_PRESETS
from .utils import validate_dependencies, sanitize_filename, is_iso_file
from .ripper import (
    select_episodic_titles, rip_all_titles, DVDTitle, DVDInfo,
    scan_dvd, select_play_all, select_play_all_titles, rip_title
)
from .duplicates import deduplicate_files
from .merger import merge_to_mkv
from .encoder import encode_to_mp4, get_available_encoders, is_vaapi_encoder
from .playlist import detect_playlist_file, parse_playlist

logger = logging.getLogger("dvd-ripper-gtk")

SETTINGS_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "dvd-ripper" / "settings.json"

DEFAULT_SETTINGS = {
    "input_path": "/dev/sr0",
    "output_dir": str(Path.home() / "Videos" / "DVD-Rips"),
    "temp_dir": str(Path.home() / ".cache" / "dvd-ripper"),
    "encoder": "h264_vaapi",
    "quality": "20",
    "subtitle_mode": "none",
    "upscale": "none",
    "sharpen": "none",
    "brightness": 0,
    "contrast": 1.0,
    "saturation": 1.0,
    "gamma": 1.0,
    "deinterlace": True,
    "denoise": True,
    "keep_temp": False,
    "use_playlist": True,
    "rip_mode": "episodic",
    "gpu_optimized": True,
}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r") as f:
                saved = json.load(f)
            settings = DEFAULT_SETTINGS.copy()
            settings.update(saved)
            return settings
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict):
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        logger.warning("Failed to save settings: %s", e)


class RipState(GObject.GObject):
    __gtype_name__ = "RipState"
    __gsignals__ = {
        "progress-changed": (GObject.SignalFlags.RUN_FIRST, None, (float, str, float)),
        "step-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "error": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "complete": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__()
        self.running = False
        self.cancelled = False
        self.step = ""
        self.progress = 0.0
        self.message = ""
        self.speed = 1.0


class DVDWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(800, 900)
        self.set_title("DVD Ripper")

        self.settings = load_settings()
        self.config = Config()
        self.dvd_info: Optional[DVDInfo] = None
        self.rip_state = RipState()
        self.rip_thread: Optional[threading.Thread] = None

        self._build_ui()
        self._setup_encoders()
        self._connect_signals()

    def _build_ui(self):
        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)

        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        title = Gtk.Label(label="DVD Ripper", css_classes=["title-2"])
        header.set_title_widget(title)

        scroll = Gtk.ScrolledWindow()
        toolbar.set_content(scroll)

        clamp = Adw.Clamp()
        scroll.set_child(clamp)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        clamp.set_child(box)

        box.append(self._build_source_row())
        box.append(self._build_mode_row())
        box.append(self._build_options_row())
        box.append(self._build_enhance_row())
        box.append(self._build_titles_section())
        box.append(self._build_progress_section())
        box.append(self._build_action_buttons())

    def _build_source_row(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        group.set_title("Source")

        row = Adw.ActionRow()
        row.set_title("DVD Source")
        row.set_subtitle("Device path, VIDEO_TS folder, or ISO file")

        self.source_entry = Gtk.Entry()
        self.source_entry.set_text(self.settings.get("input_path", "/dev/sr0"))
        self.source_entry.set_hexpand(True)
        self.source_entry.set_width_chars(30)
        row.add_suffix(self.source_entry)

        scan_btn = Gtk.Button(label="Scan")
        scan_btn.set_css_classes(["suggested-action"])
        scan_btn.connect("clicked", self._on_scan)
        row.add_suffix(scan_btn)

        browse_btn = Gtk.Button(label="Browse")
        browse_btn.connect("clicked", self._on_browse_source)
        row.add_suffix(browse_btn)

        group.add(row)
        return group

    def _build_mode_row(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        group.set_title("Rip Mode")

        self.play_all_row = Adw.ActionRow()
        self.play_all_row.set_title("Play All")
        self.play_all_row.set_subtitle("Rip all titles in order, merge to single file")
        self.play_all_check = Gtk.CheckButton()
        self.play_all_row.add_prefix(self.play_all_check)
        group.add(self.play_all_row)

        self.episodic_row = Adw.ActionRow()
        self.episodic_row.set_title("Episodes")
        self.episodic_row.set_subtitle("Rip, deduplicate, merge chronologically")
        self.episodic_check = Gtk.CheckButton(active=True, group=self.play_all_check)
        self.episodic_row.add_prefix(self.episodic_check)
        group.add(self.episodic_row)

        return group

    def _build_options_row(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        group.set_title("Options")

        name_row = Adw.ActionRow()
        name_row.set_title("Output Name")
        self.name_entry = Gtk.Entry()
        self.name_entry.set_width_chars(25)
        name_row.add_suffix(self.name_entry)
        group.add(name_row)

        output_row = Adw.ActionRow()
        output_row.set_title("Output Directory")
        output_row.set_subtitle(self.settings.get("output_dir", str(self.config.output_dir)))
        self.output_entry = Gtk.Entry()
        self.output_entry.set_text(self.settings.get("output_dir", str(self.config.output_dir)))
        self.output_entry.set_hexpand(True)
        output_row.add_suffix(self.output_entry)
        output_browse = Gtk.Button(label="Browse")
        output_browse.connect("clicked", self._on_browse_output)
        output_row.add_suffix(output_browse)
        group.add(output_row)

        encoder_row = Adw.ComboRow()
        encoder_row.set_title("Encoder")
        self.encoder_store = Gtk.StringList()
        encoder_row.set_model(self.encoder_store)
        group.add(encoder_row)
        self.encoder_row = encoder_row

        quality_row = Adw.ActionRow()
        quality_row.set_title("Quality (CRF)")
        quality_row.set_subtitle("Lower = better quality (16-28)")
        self.quality_spin = Gtk.SpinButton.new_with_range(16, 28, 1)
        self.quality_spin.set_value(int(self.settings.get("quality", "20")))
        quality_row.add_suffix(self.quality_spin)
        group.add(quality_row)

        subtitle_row = Adw.ComboRow()
        subtitle_row.set_title("Subtitles")
        sub_store = Gtk.StringList()
        for key, label in SUBTITLE_MODES.items():
            sub_store.append(label)
        subtitle_row.set_model(sub_store)
        saved_sub = self.settings.get("subtitle_mode", "none")
        for i, key in enumerate(SUBTITLE_MODES.keys()):
            if key == saved_sub:
                subtitle_row.set_selected(i)
                break
        group.add(subtitle_row)
        self.subtitle_row = subtitle_row

        deinterlace_row = Adw.ActionRow()
        deinterlace_row.set_title("Deinterlace")
        self.deinterlace_switch = Gtk.Switch(active=self.settings.get("deinterlace", True))
        deinterlace_row.add_suffix(self.deinterlace_switch)
        group.add(deinterlace_row)

        playlist_row = Adw.ActionRow()
        playlist_row.set_title("Use Playlist Order")
        self.playlist_switch = Gtk.Switch(active=self.settings.get("use_playlist", True))
        playlist_row.add_suffix(self.playlist_switch)
        group.add(playlist_row)

        return group

    def _build_enhance_row(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        group.set_title("Video Enhancements")

        upscale_row = Adw.ComboRow()
        upscale_row.set_title("Upscale")
        upscale_store = Gtk.StringList()
        for key in UPSCALE_PRESETS.keys():
            upscale_store.append(key)
        upscale_row.set_model(upscale_store)
        saved_up = self.settings.get("upscale", "none")
        for i, key in enumerate(UPSCALE_PRESETS.keys()):
            if key == saved_up:
                upscale_row.set_selected(i)
                break
        group.add(upscale_row)
        self.upscale_row = upscale_row

        sharpen_row = Adw.ComboRow()
        sharpen_row.set_title("Sharpen")
        sharpen_store = Gtk.StringList()
        for key in SHARPEN_PRESETS.keys():
            sharpen_store.append(key)
        sharpen_row.set_model(sharpen_store)
        saved_sh = self.settings.get("sharpen", "none")
        for i, key in enumerate(SHARPEN_PRESETS.keys()):
            if key == saved_sh:
                sharpen_row.set_selected(i)
                break
        group.add(sharpen_row)
        self.sharpen_row = sharpen_row

        return group

    def _build_titles_section(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        group.set_title("Disc Titles")

        self.titles_label = Gtk.Label(label="Scan a DVD to see titles")
        self.titles_label.set_css_classes(["dim-label"])
        group.add(self.titles_label)

        self.titles_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        group.add(self.titles_box)
        self.titles_box.set_visible(False)

        return group

    def _build_progress_section(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        group.set_title("Progress")

        progress_row = Adw.ActionRow()
        progress_row.set_title("Status")

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.progress_bar.set_hexpand(True)
        progress_row.add_suffix(self.progress_bar)

        self.status_label = Gtk.Label(label="Ready")
        self.status_label.set_css_classes(["dim-label"])
        progress_row.add_suffix(self.status_label)

        group.add(progress_row)
        self.progress_group = group

        return group

    def _build_action_buttons(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_halign(Gtk.Align.END)

        self.start_btn = Gtk.Button(label="Start Rip")
        self.start_btn.set_css_classes(["suggested-action", "pill"])
        self.start_btn.set_halign(Gtk.Align.END)
        self.start_btn.connect("clicked", self._on_start_rip)
        box.append(self.start_btn)

        self.cancel_btn = Gtk.Button(label="Cancel")
        self.cancel_btn.set_css_classes(["destructive-action", "pill"])
        self.cancel_btn.set_halign(Gtk.Align.END)
        self.cancel_btn.set_visible(False)
        self.cancel_btn.connect("clicked", self._on_cancel_rip)
        box.append(self.cancel_btn)

        return box

    def _setup_encoders(self):
        available = get_available_encoders()
        for key, preset in ENCODER_PRESETS.items():
            if key in available:
                self.encoder_store.append(f"{preset['label']}")

        saved_encoder = self.settings.get("encoder", "h264_vaapi")
        for i, key in enumerate(ENCODER_PRESETS.keys()):
            if key in available and key == saved_encoder:
                self.encoder_row.set_selected(i)
                break
            if key in available and key == "h264_vaapi":
                self.encoder_row.set_selected(i)

    def _connect_signals(self):
        self.rip_state.connect("progress-changed", self._on_progress)
        self.rip_state.connect("step-changed", self._on_step)
        self.rip_state.connect("error", self._on_error)
        self.rip_state.connect("complete", self._on_complete)

    def _on_browse_source(self, btn):
        dialog = Gtk.FileDialog()
        dialog.set_title("Select DVD ISO or Folder")
        dialog.open(self, None, self._on_source_selected)

    def _on_source_selected(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                self.source_entry.set_text(file.get_path())
        except GLib.Error:
            pass

    def _on_browse_output(self, btn):
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Output Directory")
        dialog.select_folder(self, None, self._on_output_selected)

    def _on_output_selected(self, dialog, result):
        try:
            file = dialog.select_folder_finish(result)
            if file:
                path = file.get_path()
                self.output_entry.set_text(path)
        except GLib.Error:
            pass

    def _on_scan(self, btn):
        input_path = self.source_entry.get_text().strip()
        if not input_path:
            self._show_toast("Please specify a DVD source")
            return

        btn.set_sensitive(False)
        self.status_label.set_text("Scanning...")

        def do_scan():
            try:
                self.dvd_info = scan_dvd(self.config, input_path, min_duration_sec=60.0)
                GLib.idle_add(self._on_scan_complete, input_path)
            except Exception as e:
                GLib.idle_add(self._on_scan_error, str(e))
            finally:
                GLib.idle_add(btn.set_sensitive, True)

        threading.Thread(target=do_scan, daemon=True).start()

    def _on_scan_complete(self, input_path):
        if not self.name_entry.get_text():
            self.name_entry.set_text(Path(input_path).stem or "DVD")

        self.titles_label.set_visible(False)
        self.titles_box.set_visible(True)

        for child in self.titles_box.observe_children():
            self.titles_box.remove(child)

        titles = self.dvd_info.episodic_titles
        for t in titles:
            mins = t.duration_sec / 60
            row = Adw.ActionRow()
            row.set_title(f"Title {t.number}")
            row.set_subtitle(f"{mins:.1f} min | {t.chapters} chapters | {t.audio_tracks} audio | {t.subtitle_tracks} subs")
            self.titles_box.append(row)

        self.status_label.set_text(f"Found {len(titles)} titles")
        self._show_toast(f"Scan complete: {len(titles)} titles")

    def _on_scan_error(self, error_msg):
        self.status_label.set_text("Scan failed")
        self._show_toast(f"Scan failed: {error_msg}")

    def _get_encoder_key(self) -> str:
        selected = self.encoder_row.get_selected()
        keys = [k for k, v in ENCODER_PRESETS.items() if f"{v['label']}" == self.encoder_store.get_string(selected)]
        return keys[0] if keys else "x264"

    def _on_start_rip(self, btn):
        input_path = self.source_entry.get_text().strip()
        if not input_path:
            self._show_toast("Please specify a DVD source")
            return

        if self.rip_state.running:
            return

        self.rip_state.running = True
        self.rip_state.cancelled = False
        self.start_btn.set_visible(False)
        self.cancel_btn.set_visible(True)
        self.progress_bar.set_fraction(0)
        self.progress_bar.set_text("")

        thread = threading.Thread(target=self._run_pipeline, args=(input_path,), daemon=True)
        self.rip_thread = thread
        thread.start()

    def _on_cancel_rip(self, btn):
        self.rip_state.cancelled = True
        self.status_label.set_text("Cancelling...")
        self.cancel_btn.set_sensitive(False)

    def _run_pipeline(self, input_path: str):
        try:
            disc_name = sanitize_filename(self.name_entry.get_text() or "dvd")
            job_id = datetime.now().strftime("%Y%m%d_%H%M%S")

            self.config.output_dir = Path(self.output_entry.get_text())
            self.config.output_dir.mkdir(parents=True, exist_ok=True)
            self.config.temp_dir = Path(self.settings.get("temp_dir", str(self.config.temp_dir)))
            self.config.temp_dir.mkdir(parents=True, exist_ok=True)

            self.config.handbrake_encoder = self._get_encoder_key()
            self.config.handbrake_quality = str(int(self.quality_spin.get_value()))
            self.config.handbrake_deinterlace = "decomb" if self.deinterlace_switch.get_active() else ""
            self.config.handbrake_denoise = "nlmeans" if self.deinterlace_switch.get_active() else ""
            self.config.gpu_optimized = True

            sub_idx = self.subtitle_row.get_selected()
            self.config.subtitle_mode = list(SUBTITLE_MODES.keys())[sub_idx]

            upscale_idx = self.upscale_row.get_selected()
            self.config.set_upscale(list(UPSCALE_PRESETS.keys())[upscale_idx])

            sharpen_idx = self.sharpen_row.get_selected()
            self.config.video_sharpen = list(SHARPEN_PRESETS.keys())[sharpen_idx]

            play_all = self.play_all_check.get_active()

            work_dir = self.config.temp_dir / f"{disc_name}_{job_id}"
            work_dir.mkdir(parents=True, exist_ok=True)
            rip_dir = work_dir / "rips"
            rip_dir.mkdir(parents=True, exist_ok=True)

            self._emit_step("Ripping titles...")
            self._emit_progress(10, "Scanning DVD...")

            if play_all:
                titles_to_rip = select_play_all_titles(self.config, input_path)
            else:
                if not self.dvd_info:
                    self.dvd_info = scan_dvd(self.config, input_path, min_duration_sec=60.0)
                titles_to_rip = self.dvd_info.episodic_titles

            if self.rip_state.cancelled:
                return

            ripped_files = []
            total = len(titles_to_rip)
            for i, title in enumerate(titles_to_rip):
                if self.rip_state.cancelled:
                    self._emit_step("Cancelled")
                    return

                pct = 10 + (i / max(total, 1)) * 40
                self._emit_progress(pct, f"Ripping title {title.number} ({i+1}/{total})...")

                try:
                    path = rip_title(self.config, input_path, title, rip_dir, disc_name)
                    ripped_files.append(path)
                except Exception as e:
                    logger.error("Failed to rip title %d: %s", title.number, e)

            if not ripped_files:
                self._emit_error("No files were ripped")
                return

            if self.rip_state.cancelled:
                return

            self._emit_step("Deduplicating...")
            self._emit_progress(55, "Checking for duplicates...")
            unique_files = deduplicate_files(self.config, ripped_files)

            if not unique_files:
                self._emit_error("No files remaining after deduplication")
                return

            if self.rip_state.cancelled:
                return

            self._emit_step("Merging...")
            self._emit_progress(65, "Merging episodes...")
            merged_mkv = self.config.temp_dir / f"{disc_name}_merged.mkv"

            if len(unique_files) == 1:
                import shutil
                shutil.copy2(unique_files[0], merged_mkv)
            else:
                merge_to_mkv(self.config, unique_files, merged_mkv)

            if self.rip_state.cancelled:
                return

            self._emit_step("Encoding...")
            self._emit_progress(80, "Encoding to MP4...")

            final_mp4 = self.config.output_dir / f"{disc_name}.mp4"

            def encode_progress(pct, speed=1.0):
                self._emit_progress(80 + pct * 0.15, f"Encoding... {int(pct)}% ({speed:.1f}x)")

            encode_to_mp4(self.config, merged_mkv, final_mp4, progress_callback=encode_progress)

            if not self.config.keep_temp_files:
                import shutil
                if work_dir.exists():
                    shutil.rmtree(work_dir)
                if merged_mkv.exists():
                    merged_mkv.unlink(missing_ok=True)

            size_mb = final_mp4.stat().st_size / (1024 * 1024)
            self._emit_progress(100, f"Done! {size_mb:.0f} MB")
            self._emit_complete(str(final_mp4))

        except Exception as e:
            logger.exception("Pipeline failed")
            self._emit_error(str(e))
        finally:
            self.rip_state.running = False
            GLib.idle_add(self._on_pipeline_done)

    def _emit_step(self, step: str):
        GLib.idle_add(self.rip_state.emit, "step-changed", step)

    def _emit_progress(self, pct: float, msg: str, speed: float = 1.0):
        GLib.idle_add(self.rip_state.emit, "progress-changed", pct, msg, speed)

    def _emit_error(self, msg: str):
        GLib.idle_add(self.rip_state.emit, "error", msg)

    def _emit_complete(self, path: str):
        GLib.idle_add(self.rip_state.emit, "complete", path)

    def _on_progress(self, state, pct, msg, speed):
        self.progress_bar.set_fraction(pct / 100)
        self.progress_bar.set_text(f"{int(pct)}%")
        self.status_label.set_text(msg)

    def _on_step(self, state, step):
        self.status_label.set_text(step)

    def _on_error(self, state, msg):
        self.status_label.set_text(f"Error: {msg}")
        self._show_toast(f"Error: {msg}")

    def _on_complete(self, state, path):
        self.status_label.set_text("Complete!")
        self._show_toast(f"Rip complete: {path}")

    def _on_pipeline_done(self):
        self.start_btn.set_visible(True)
        self.cancel_btn.set_visible(False)
        self.cancel_btn.set_sensitive(True)

    def _show_toast(self, msg: str):
        toast = Adw.Toast.new(msg)
        toast.set_timeout(3)
        if hasattr(self, "get_toast_overlay"):
            self.get_toast_overlay().add_toast(toast)


class DVDApplication(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(application_id="io.github.dvdripper.app", **kwargs)

    def do_activate(self):
        win = DVDWindow(application=self)
        win.present()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    app = DVDApplication()
    app.run(sys.argv)


if __name__ == "__main__":
    main()
