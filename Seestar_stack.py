from __future__ import annotations

# Seestar Debayer & Stack
# Copyright (c) 2026 Aquarius58
# SPDX-License-Identifier: MIT

import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import sirilpy as s

if not s.utility.check_module_version(">=1.0.13"):
    print("Error: sirilpy module is too old and does not support this script.")
    sys.exit(1)

s.ensure_installed("PyQt6")
s.ensure_installed("astropy")
s.ensure_installed("numpy")

from astropy.io import fits
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# User settings
SCRIPT_VERSION = "0.2.2"
SIRIL_REQUIRES = "1.3.0"
OUTPUT_BITS_COMMAND = "set16bits"

DEFAULT_PLAN_MODE = "time"
DEFAULT_DURATION_PLANS = (100, 1000)
DEFAULT_FRAME_PLANS = (10, 100)
ALL_PLAN_NAME = "ALL"

STACK_METHOD = "rej"
REJECTION_LOW = 3.0
REJECTION_HIGH = 3.0
USE_REJECTION_MAPS = True
NONORM = True
MIN_FRAMES_PER_STACK = 3

REGISTRATION_TRANSFORM = "similarity"
REGISTRATION_INTERPOLATION = "lanczos4"
REGISTRATION_TWO_PASS = True
REGISTRATION_MAXSTARS = 1000
REGISTRATION_MINPAIRS = 10

TEMP_BASENAME = "tmp"
DEFAULT_SUBFRAME_EXPOSURE = 10.0
DEBAYER_CFA_TO_LUMA = True
KEEP_LUMA_IMAGES = True
OVERWRITE_RESULTS = True
KEEP_TEMP_ON_ERROR = True
CLEANUP_RETRIES = 6
CLEANUP_RETRY_DELAY_SECONDS = 0.5
VERBOSE_COMMAND_LOG = False
VERBOSE_FRAME_LOG = False
PROGRESS_LOG_INTERVAL = 50

WINDOW_TITLE = f"Seestar Debayer & Stack {SCRIPT_VERSION}"
WINDOW_WIDTH = 760
WINDOW_HEIGHT = 420

CFA_CHANNELS = {
    "L": {
        "suffix": "_l",
        "filter": "L",
        "chanmode": "LUMA601",
        "label": "L",
        "comment": "Derived luminance channel",
        "mode_comment": "RGB luma weights: 0.299 R, 0.587 G, 0.114 B",
    },
    "R": {
        "suffix": "_r",
        "filter": "R",
        "chanmode": "CFA_R",
        "label": "R",
        "comment": "Derived red channel",
        "mode_comment": "Debayered CFA red channel",
    },
    "G": {
        "suffix": "_g",
        "filter": "G",
        "chanmode": "CFA_G",
        "label": "G",
        "comment": "Derived green channel",
        "mode_comment": "Debayered CFA green channel",
    },
    "B": {
        "suffix": "_b",
        "filter": "B",
        "chanmode": "CFA_B",
        "label": "B",
        "comment": "Derived blue channel",
        "mode_comment": "Debayered CFA blue channel",
    },
}
DEFAULT_CFA_CHANNELS = ("L",)
BAYER_HEADER_KEYS = ("BAYERPAT", "XBAYROFF", "YBAYROFF", "ROWORDER")

@dataclass(frozen=True)
class StackPlan:
    name: str
    suffix: str
    block_size: int | None
    duration_seconds: int | None


@dataclass(frozen=True)
class FitsFrame:
    path: Path
    date_obs: datetime
    exptime: float


def is_hidden_fits(path: Path) -> bool:
    return path.name.startswith(".") or path.name.startswith("._")


def is_fits_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in {".fit", ".fits"} and not is_hidden_fits(path)


def parse_date_obs(value: object, path: Path) -> datetime:
    if value is None:
        raise ValueError(f"{path.name} lacks DATE-OBS")

    date_obs = str(value).strip()
    if not date_obs:
        raise ValueError(f"{path.name} has empty DATE-OBS")
    if date_obs.endswith("Z"):
        date_obs = date_obs[:-1] + "+00:00"

    dt = datetime.fromisoformat(date_obs)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_exptime(header: fits.Header, path: Path, log_callback) -> float:
    raw_value = header.get("EXPTIME", header.get("EXPOSURE"))
    if raw_value is None:
        log_callback(
            f"[WARN] {path.name}: EXPTIME fehlt, verwende {DEFAULT_SUBFRAME_EXPOSURE:g}s."
        )
        return DEFAULT_SUBFRAME_EXPOSURE

    try:
        exptime = float(raw_value)
    except (TypeError, ValueError):
        log_callback(
            f"[WARN] {path.name}: EXPTIME ist unbrauchbar, verwende {DEFAULT_SUBFRAME_EXPOSURE:g}s."
        )
        return DEFAULT_SUBFRAME_EXPOSURE

    if exptime <= 0:
        log_callback(
            f"[WARN] {path.name}: EXPTIME <= 0, verwende {DEFAULT_SUBFRAME_EXPOSURE:g}s."
        )
        return DEFAULT_SUBFRAME_EXPOSURE

    return exptime


def read_frame(path: Path, log_callback) -> FitsFrame:
    with fits.open(path) as hdul:
        header = hdul[0].header
        date_obs = parse_date_obs(header.get("DATE-OBS"), path)
        exptime = parse_exptime(header, path, log_callback)
    return FitsFrame(path=path, date_obs=date_obs, exptime=exptime)


def image_has_bayer_header(path: Path) -> bool:
    with fits.open(path) as hdul:
        header = hdul[0].header
        return any(key in header for key in BAYER_HEADER_KEYS)


def frames_need_debayer(frames: list[FitsFrame]) -> bool:
    if not DEBAYER_CFA_TO_LUMA or not frames:
        return False
    try:
        return image_has_bayer_header(frames[0].path)
    except Exception:
        return False


def collect_valid_fits(source_dir: Path, log_callback) -> list[FitsFrame]:
    frames: list[FitsFrame] = []
    for path in sorted(source_dir.iterdir()):
        if not is_fits_file(path):
            continue
        try:
            frames.append(read_frame(path, log_callback))
        except Exception as exc:
            log_callback(f"[WARN] Ueberspringe {path.name}: {exc}")
    return sorted(frames, key=lambda frame: frame.date_obs)


def build_stack_plans(mode: str, values: tuple[int, int]) -> tuple[StackPlan, StackPlan, StackPlan]:
    if mode == "frames":
        return (
            StackPlan(str(values[0]), f"{values[0]}img", values[0], None),
            StackPlan(str(values[1]), f"{values[1]}img", values[1], None),
            StackPlan(ALL_PLAN_NAME, "all", None, None),
        )
    if mode == "time":
        return (
            StackPlan(f"{values[0]}s", f"{values[0]}sec", None, values[0]),
            StackPlan(f"{values[1]}s", f"{values[1]}sec", None, values[1]),
            StackPlan(ALL_PLAN_NAME, "all", None, None),
        )
    raise ValueError(f"Unsupported plan mode: {mode}")


def split_into_blocks(frames: list[FitsFrame], plan: StackPlan) -> list[tuple[int, list[FitsFrame]]]:
    if plan.block_size is None and plan.duration_seconds is None:
        return [(0, frames)]

    if plan.block_size is not None:
        return [
            (index, frames[index:index + plan.block_size])
            for index in range(0, len(frames), plan.block_size)
        ]

    blocks: list[tuple[int, list[FitsFrame]]] = []
    start_index = 0
    while start_index < len(frames):
        first_frame = frames[start_index]
        block_start = first_frame.date_obs - timedelta(seconds=first_frame.exptime)
        end_index = start_index + 1
        while end_index < len(frames):
            covered_seconds = (frames[end_index].date_obs - block_start).total_seconds()
            if covered_seconds > plan.duration_seconds:
                break
            end_index += 1
        blocks.append((start_index, frames[start_index:end_index]))
        start_index = end_index
    return blocks


def compute_midpoint(block_frames: list[FitsFrame]) -> tuple[datetime, float]:
    first_frame = block_frames[0]
    start_time = first_frame.date_obs - timedelta(seconds=first_frame.exptime)
    end_time = max(frame.date_obs for frame in block_frames)
    duration = end_time - start_time
    if duration.total_seconds() <= 0:
        raise ValueError("Non-positive duration computed for block")
    total_exposure = sum(frame.exptime for frame in block_frames)
    midpoint = start_time + duration / 2
    return midpoint.astimezone(timezone.utc), total_exposure


def write_midpoint_header(path: Path, mid_time: datetime, total_exposure: float, frame_count: int) -> None:
    iso_mid = mid_time.isoformat().replace("+00:00", "Z")
    with fits.open(path, mode="update") as hdul:
        header = hdul[0].header
        header["DATE-OBS"] = (iso_mid, "Stack mid-exposure (UTC)")
        header["EXPTIME"] = (float(total_exposure), "Total stack exposure in seconds")
        header["NCOMBINE"] = (frame_count, "Frames stacked")
        hdul.flush()


def empty_dir(path: Path) -> None:
    if not path.exists():
        return
    for entry in path.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def remove_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def cleanup_empty_dir(path: Path, log_callback) -> None:
    cleanup_path(path, lambda: empty_dir(path), log_callback)


def cleanup_remove_dir(path: Path, log_callback) -> None:
    cleanup_path(path, lambda: remove_dir(path), log_callback)


def retry_empty_dir(path: Path) -> None:
    retry_path(lambda: empty_dir(path))


def cleanup_path(path: Path, operation, log_callback) -> None:
    last_exc = retry_path(operation, raise_on_failure=False)
    if last_exc is None:
        return

    log_callback(
        f"[WARN] Temp-Cleanup fehlgeschlagen fuer {path}: {last_exc}. "
        "Die Stack-Ergebnisse wurden bereits erzeugt; den Temp-Ordner spaeter manuell loeschen."
    )


def retry_path(operation, raise_on_failure: bool = True) -> Exception | None:
    last_exc: Exception | None = None
    for attempt in range(1, CLEANUP_RETRIES + 1):
        try:
            operation()
            return None
        except FileNotFoundError:
            return None
        except (PermissionError, OSError) as exc:
            last_exc = exc
            if attempt < CLEANUP_RETRIES:
                time.sleep(CLEANUP_RETRY_DELAY_SECONDS)

    if raise_on_failure and last_exc is not None:
        raise last_exc
    return last_exc


def siril_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def build_output_header(source_path: Path, channel_key: str) -> fits.Header:
    channel = CFA_CHANNELS[channel_key]
    header = fits.getheader(source_path).copy()
    source_filter = header.get("FILTER")
    for key in BAYER_HEADER_KEYS:
        if key in header:
            del header[key]
    if source_filter:
        header["SRCFILT"] = (str(source_filter), "Original source filter")
    header["FILTER"] = (channel["filter"], channel["comment"])
    header["CHANMODE"] = (channel["chanmode"], channel["mode_comment"])
    header["DERIVED"] = (True, "Derived from debayered CFA data")
    return header


def cfa_channel_suffix(channel_key: str) -> str:
    return str(CFA_CHANNELS[channel_key]["suffix"])


def cfa_channel_label(channel_key: str) -> str:
    return str(CFA_CHANNELS[channel_key]["label"])


def plan_result_dir(source_dir: Path, plan: StackPlan, channel_key: str | None = None) -> Path:
    channel_suffix = cfa_channel_suffix(channel_key) if channel_key is not None else ""
    return source_dir.parent / f"{source_dir.name}{channel_suffix}-stack{plan.suffix}"


def plan_temp_dir(source_dir: Path, plan: StackPlan) -> Path:
    return source_dir.parent / f"{source_dir.name}-tmp{plan.suffix}"


def debayer_temp_dir(source_dir: Path) -> Path:
    return source_dir.parent / f"{source_dir.name}-tmpdebayer"


def channel_temp_dir(source_dir: Path, channel_key: str) -> Path:
    return source_dir.parent / f"{source_dir.name}-tmp{cfa_channel_suffix(channel_key)}"


def channel_result_dir(source_dir: Path, channel_key: str) -> Path:
    return source_dir.parent / f"{source_dir.name}{cfa_channel_suffix(channel_key)}"


def assert_can_create_directory(path: Path) -> None:
    if path.exists():
        if not path.is_dir():
            raise NotADirectoryError(f"Pfad existiert, ist aber kein Ordner: {path}")
        return
    parent = path.parent
    if not parent.is_dir():
        raise FileNotFoundError(f"Ziel-Ebene existiert nicht: {parent}")
    try:
        path.mkdir()
        path.rmdir()
    except PermissionError as exc:
        raise PermissionError(
            f"Kann Ordner nicht neben dem gewaehlten Bildordner anlegen: {path}\n"
            f"Bitte waehle den konkreten Ordner mit den FITS-Bildern, nicht einen "
            f"uebergeordneten System-/Benutzerordner."
        ) from exc
    except OSError as exc:
        raise OSError(f"Kann Ordner nicht anlegen: {path} ({exc})") from exc


def build_register_command(log_callback) -> str:
    parts = [f"register {TEMP_BASENAME}_"]
    if REGISTRATION_TWO_PASS:
        parts.append("-2pass")
    parts.append(f"-transf={REGISTRATION_TRANSFORM}")
    parts.append(f"-maxstars={REGISTRATION_MAXSTARS}")
    parts.append(f"-minpairs={REGISTRATION_MINPAIRS}")

    interpolation = REGISTRATION_INTERPOLATION
    if REGISTRATION_TRANSFORM != "shift" and interpolation == "none":
        interpolation = "lanczos4"
        if VERBOSE_COMMAND_LOG:
            log_callback(
                "[INFO] Interpolation automatisch auf lanczos4 gesetzt, "
                "weil 'none' nur mit shift sinnvoll ist."
            )
    parts.append(f"-interp={interpolation}")
    return " ".join(parts)


def build_seqapplyreg_command() -> str:
    parts = [f"seqapplyreg {TEMP_BASENAME}_"]
    interpolation = REGISTRATION_INTERPOLATION
    if REGISTRATION_TRANSFORM != "shift" and interpolation == "none":
        interpolation = "lanczos4"
    if interpolation != "none":
        parts.append(f"-interp={interpolation}")
    return " ".join(parts)


def build_stack_command(outname: str) -> str:
    registered_sequence = f"r_{TEMP_BASENAME}_"
    parts = [f"stack {registered_sequence}", STACK_METHOD]
    if STACK_METHOD in {"rej", "mean"}:
        parts.extend([f"w {REJECTION_LOW:g} {REJECTION_HIGH:g}"])
    if NONORM:
        parts.append("-nonorm")
    if USE_REJECTION_MAPS and STACK_METHOD == "rej":
        parts.append("-rejmaps")
    parts.append(f"-out={outname}")
    return " ".join(parts)


def collect_registered_files(tmp_dir: Path) -> list[Path]:
    return sorted(path for path in tmp_dir.glob(f"r_{TEMP_BASENAME}_*.fit") if not is_hidden_fits(path))


def should_log_progress(index: int, total: int) -> bool:
    return index == 1 or index == total or index % PROGRESS_LOG_INTERVAL == 0


class StackWorker(QThread):
    log = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(
        self,
        source_dir: str,
        selected_plans: tuple[StackPlan, ...],
        selected_channels: tuple[str, ...],
    ):
        super().__init__()
        self.source_dir = Path(source_dir).expanduser()
        self.selected_plans = selected_plans
        self.selected_channels = selected_channels
        self.debayer_dirs: tuple[Path, ...] | None = None
        self.plan_temp_dirs = tuple(plan_temp_dir(self.source_dir, plan) for plan in selected_plans)

    def emit_log(self, message: str) -> None:
        print(message)
        self.log.emit(message)

    def run_cmd(self, siril: s.SirilInterface, command: str) -> None:
        if VERBOSE_COMMAND_LOG:
            self.emit_log(f"> {command}")
        siril.cmd(command)

    def move_siril_to_safe_directory(self, siril: s.SirilInterface) -> None:
        safe_dir = self.source_dir.parent
        self.run_cmd(siril, f'cd "{siril_path(safe_dir)}"')

    def run(self) -> None:
        try:
            result_dir = self.run_stack()
            if self.selected_plans:
                message = f"Stack fertig. Letzte Ergebnisse in {result_dir}"
            else:
                message = f"Kanal-Export fertig. Ergebnisse in {result_dir}"
            self.finished.emit(True, message)
        except Exception as exc:
            self.finished.emit(False, str(exc))

    def run_stack(self) -> Path:
        if not self.source_dir.is_dir():
            raise FileNotFoundError(f"Quellordner nicht gefunden: {self.source_dir}")

        source_frames = collect_valid_fits(self.source_dir, self.emit_log)
        if not source_frames:
            raise FileNotFoundError(
                f"Keine gueltigen .fit/.fits-Dateien direkt im gewaehlten Quellordner gefunden: "
                f"{self.source_dir}\n"
                f"Bitte den Ordner waehlen, der die einzelnen FITS-Lights enthaelt."
            )

        needs_debayer = frames_need_debayer(source_frames)
        if needs_debayer and not self.selected_channels:
            raise ValueError("Mindestens ein CFA-Output muss ausgewaehlt sein.")
        self.preflight_output_dirs(needs_debayer)

        siril = s.SirilInterface()
        siril.connect()
        if VERBOSE_COMMAND_LOG:
            self.emit_log("[OK] Mit Siril verbunden.")

        final_result_dir: Path | None = None
        completed = False
        try:
            frames_by_channel = self.prepare_processing_frames(siril, source_frames, needs_debayer)
            self.keep_channel_images(frames_by_channel)
            for channel_key, frames in frames_by_channel.items():
                for plan in self.selected_plans:
                    final_result_dir = self.run_stack_plan(siril, plan, frames, channel_key)
            completed = True
        finally:
            try:
                self.move_siril_to_safe_directory(siril)
                siril.disconnect()
            except Exception:
                pass
            if completed or not KEEP_TEMP_ON_ERROR:
                for directory in self.plan_temp_dirs:
                    cleanup_remove_dir(directory, self.emit_log)
                if self.debayer_dirs is not None:
                    for directory in self.debayer_dirs:
                        cleanup_remove_dir(directory, self.emit_log)

        if final_result_dir is None:
            if needs_debayer and KEEP_LUMA_IMAGES:
                return channel_result_dir(self.source_dir, self.selected_channels[-1])
            raise RuntimeError(
                "Keine Stack-Ergebnisse erzeugt. Bitte mindestens einen Stack-Plan auswaehlen."
            )
        return final_result_dir

    def prepare_processing_frames(
        self,
        siril: s.SirilInterface,
        source_frames: list[FitsFrame],
        needs_debayer: bool,
    ) -> dict[str | None, list[FitsFrame]]:
        if not needs_debayer:
            if VERBOSE_FRAME_LOG:
                self.emit_log("[INFO] Keine CFA/Bayer-Header erkannt; stacke Eingabebilder direkt.")
            return {None: source_frames}

        if not self.selected_channels:
            raise ValueError("Mindestens ein CFA-Output muss ausgewaehlt sein.")

        if VERBOSE_FRAME_LOG:
            channel_labels = ", ".join(cfa_channel_label(channel_key) for channel_key in self.selected_channels)
            self.emit_log(f"[INFO] CFA/Bayer-Header erkannt; erzeuge Kanaele: {channel_labels}.")
        debayer_dir = debayer_temp_dir(self.source_dir)
        channel_dirs = {
            channel_key: channel_temp_dir(self.source_dir, channel_key)
            for channel_key in self.selected_channels
        }
        self.debayer_dirs = (debayer_dir, *channel_dirs.values())

        if debayer_dir.exists():
            empty_dir(debayer_dir)
        else:
            debayer_dir.mkdir(parents=True)
        for channel_dir in channel_dirs.values():
            if channel_dir.exists():
                empty_dir(channel_dir)
            else:
                channel_dir.mkdir(parents=True)

        try:
            frame_count = len(source_frames)
            channel_labels = ", ".join(cfa_channel_label(channel_key) for channel_key in self.selected_channels)
            self.emit_log(f"[INFO] Kopiere CFA-Quellbilder: {frame_count} Dateien.")
            for index, frame in enumerate(source_frames, start=1):
                shutil.copy2(frame.path, debayer_dir / frame.path.name)
                if should_log_progress(index, frame_count):
                    self.emit_log(f"[INFO] CFA-Quellbilder kopiert: {index}/{frame_count}.")

            self.run_cmd(siril, f"requires {SIRIL_REQUIRES}")
            self.run_cmd(siril, OUTPUT_BITS_COMMAND)
            self.run_cmd(siril, f'cd "{siril_path(debayer_dir)}"')
            self.emit_log(f"[INFO] Starte Siril-Debayer fuer {frame_count} CFA-Bilder.")
            self.run_cmd(siril, "convert deb -debayer")
            self.emit_log(f"[OK] Siril-Debayer fertig: {frame_count} Bilder.")

            for index, frame in enumerate(source_frames, start=1):
                suffix = f"{index:05d}"
                self.run_cmd(siril, f"load deb_{suffix}.fit")
                self.run_cmd(siril, f"split rgb_{suffix}_r rgb_{suffix}_g rgb_{suffix}_b")
                rgb_data = self.read_rgb_split(debayer_dir, suffix)
                for channel_key, channel_dir in channel_dirs.items():
                    output_path = self.write_rgb_channel_output(
                        frame.path,
                        channel_dir,
                        channel_key,
                        rgb_data,
                    )
                    if VERBOSE_FRAME_LOG:
                        self.emit_log(f"[OK] {cfa_channel_label(channel_key)} erzeugt: {output_path.name}")
                if should_log_progress(index, frame_count):
                    self.emit_log(
                        f"[INFO] RGB-Split und Kanalbilder {channel_labels}: "
                        f"{index}/{frame_count}."
                    )

            frames_by_channel: dict[str | None, list[FitsFrame]] = {}
            for channel_key, channel_dir in channel_dirs.items():
                channel_frames = collect_valid_fits(channel_dir, self.emit_log)
                if not channel_frames:
                    raise FileNotFoundError(
                        f"Keine {cfa_channel_label(channel_key)}-FITS erzeugt in {channel_dir}"
                    )
                frames_by_channel[channel_key] = channel_frames
                self.emit_log(
                    f"[OK] {cfa_channel_label(channel_key)}-Bilder erzeugt: "
                    f"{len(channel_frames)} Dateien."
                )
            return frames_by_channel
        finally:
            if not KEEP_TEMP_ON_ERROR:
                remove_dir(debayer_dir)

    def read_rgb_split(self, workdir: Path, suffix: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        rgb_r = fits.getdata(workdir / f"rgb_{suffix}_r.fit").astype(np.float64)
        rgb_g = fits.getdata(workdir / f"rgb_{suffix}_g.fit").astype(np.float64)
        rgb_b = fits.getdata(workdir / f"rgb_{suffix}_b.fit").astype(np.float64)
        return rgb_r, rgb_g, rgb_b

    def write_rgb_channel_output(
        self,
        source_path: Path,
        output_dir: Path,
        channel_key: str,
        rgb_data: tuple[np.ndarray, np.ndarray, np.ndarray],
    ) -> Path:
        rgb_r, rgb_g, rgb_b = rgb_data
        if channel_key == "L":
            data = np.clip(
                np.rint(0.299 * rgb_r + 0.587 * rgb_g + 0.114 * rgb_b),
                0,
                65535,
            ).astype(np.uint16)
        elif channel_key == "R":
            data = np.clip(np.rint(rgb_r), 0, 65535).astype(np.uint16)
        elif channel_key == "G":
            data = np.clip(np.rint(rgb_g), 0, 65535).astype(np.uint16)
        elif channel_key == "B":
            data = np.clip(np.rint(rgb_b), 0, 65535).astype(np.uint16)
        else:
            raise ValueError(f"Unbekannter CFA-Kanal: {channel_key}")

        output_path = output_dir / f"{source_path.stem}{cfa_channel_suffix(channel_key)}.fit"
        fits.writeto(output_path, data, header=build_output_header(source_path, channel_key), overwrite=True)
        return output_path

    def preflight_output_dirs(self, needs_debayer: bool) -> None:
        if VERBOSE_COMMAND_LOG:
            self.emit_log(f"[INFO] Quellordner: {self.source_dir}")
            self.emit_log(f"[INFO] Ergebnis-/Temp-Ordner werden neben diesem Ordner angelegt.")

        for plan in self.selected_plans:
            result_channels: tuple[str | None, ...] = self.selected_channels if needs_debayer else (None,)
            for channel_key in result_channels:
                result_dir = plan_result_dir(self.source_dir, plan, channel_key)
                if result_dir.exists() and not OVERWRITE_RESULTS:
                    raise FileExistsError(
                        f"Ergebnisordner existiert bereits: {result_dir}. "
                        "Loeschen oder OVERWRITE_RESULTS = True setzen."
                    )
                assert_can_create_directory(result_dir)
            assert_can_create_directory(plan_temp_dir(self.source_dir, plan))

        if needs_debayer:
            assert_can_create_directory(debayer_temp_dir(self.source_dir))
            for channel_key in self.selected_channels:
                assert_can_create_directory(channel_temp_dir(self.source_dir, channel_key))
            if KEEP_LUMA_IMAGES:
                for channel_key in self.selected_channels:
                    result_dir = channel_result_dir(self.source_dir, channel_key)
                    if result_dir.exists() and not OVERWRITE_RESULTS:
                        raise FileExistsError(
                            f"Ergebnisordner existiert bereits: {result_dir}. "
                            "Loeschen oder OVERWRITE_RESULTS = True setzen."
                        )
                    assert_can_create_directory(result_dir)

    def prepare_result_dir(self, result_dir: Path) -> None:
        if result_dir.exists():
            if not OVERWRITE_RESULTS:
                raise FileExistsError(f"Ergebnisordner existiert bereits: {result_dir}")
            empty_dir(result_dir)
        else:
            result_dir.mkdir(parents=True)

    def keep_channel_images(self, frames_by_channel: dict[str | None, list[FitsFrame]]) -> None:
        if not KEEP_LUMA_IMAGES or self.debayer_dirs is None:
            return

        for channel_key, frames in frames_by_channel.items():
            if channel_key is None:
                continue

            channel_dir = channel_temp_dir(self.source_dir, channel_key)
            channel_frames = [frame for frame in frames if frame.path.parent == channel_dir]
            if not channel_frames:
                continue

            result_dir = channel_result_dir(self.source_dir, channel_key)
            self.prepare_result_dir(result_dir)
            frame_count = len(channel_frames)
            channel_label = cfa_channel_label(channel_key)
            self.emit_log(f"[INFO] Behalte {channel_label}-Einzelbilder: {frame_count} Dateien.")
            for index, frame in enumerate(channel_frames, start=1):
                shutil.copy2(frame.path, result_dir / frame.path.name)
                if should_log_progress(index, frame_count):
                    self.emit_log(
                        f"[INFO] {channel_label}-Einzelbilder kopiert: "
                        f"{index}/{frame_count}."
                    )
            self.emit_log(
                f"[OK] {channel_label}-Einzelbilder erhalten: "
                f"{frame_count} Dateien in {result_dir.name}."
            )

    def run_stack_plan(
        self,
        siril: s.SirilInterface,
        plan: StackPlan,
        frames: list[FitsFrame],
        channel_key: str | None,
    ) -> Path:
        tmp_dir = plan_temp_dir(self.source_dir, plan)
        result_dir = plan_result_dir(self.source_dir, plan, channel_key)
        completed = False

        self.prepare_result_dir(result_dir)
        self.move_siril_to_safe_directory(siril)
        if tmp_dir.exists():
            retry_path(lambda: remove_dir(tmp_dir))
        else:
            tmp_dir.mkdir(parents=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        try:
            channel_label = cfa_channel_label(channel_key) if channel_key is not None else "Input"
            self.emit_log(f"[INFO] Starte Stack-Plan {plan.name} fuer {channel_label}.")
            self.run_cmd(siril, f"requires {SIRIL_REQUIRES}")
            self.run_cmd(siril, OUTPUT_BITS_COMMAND)

            blocks = split_into_blocks(frames, plan)
            total_blocks = len(blocks)
            for block_number, (index, block) in enumerate(blocks, start=1):
                if not block:
                    continue

                start_idx = index + 1
                end_idx = index + len(block)
                outname = f"stack_{plan.suffix}_{start_idx:05d}-{end_idx:05d}"
                block_tmp_dir = tmp_dir / f"{outname}_work"

                if len(block) < MIN_FRAMES_PER_STACK:
                    self.emit_log(
                        f"[WARN] Ueberspringe {outname}: nur {len(block)} Frames, "
                        f"mindestens {MIN_FRAMES_PER_STACK} erforderlich."
                    )
                    continue

                self.emit_log(
                    f"[INFO] Stack {channel_label} Plan {plan.name} "
                    f"Block {block_number}/{total_blocks}: "
                    f"Frames {start_idx:05d}-{end_idx:05d}."
                )
                midpoint, total_exposure = compute_midpoint(block)
                if block_tmp_dir.exists():
                    retry_path(lambda: remove_dir(block_tmp_dir))
                block_tmp_dir.mkdir(parents=True)
                for frame in block:
                    shutil.copy2(frame.path, block_tmp_dir / frame.path.name)

                self.run_cmd(siril, f'cd "{siril_path(block_tmp_dir)}"')
                self.run_cmd(siril, f"link {TEMP_BASENAME} -out=.")

                try:
                    self.run_cmd(siril, build_register_command(self.emit_log))
                    if REGISTRATION_TWO_PASS:
                        self.run_cmd(siril, build_seqapplyreg_command())
                except Exception as exc:
                    self.emit_log(
                        f"[WARN] Ueberspringe {outname}: Registrierung fehlgeschlagen ({exc})."
                    )
                    self.move_siril_to_safe_directory(siril)
                    cleanup_remove_dir(block_tmp_dir, self.emit_log)
                    continue

                registered_files = collect_registered_files(block_tmp_dir)
                if not registered_files:
                    self.emit_log(
                        f"[WARN] Ueberspringe {outname}: Registrierung fehlgeschlagen, "
                        f"keine registrierten Dateien r_{TEMP_BASENAME}_*.fit gefunden."
                    )
                    self.move_siril_to_safe_directory(siril)
                    cleanup_remove_dir(block_tmp_dir, self.emit_log)
                    continue
                if len(registered_files) < MIN_FRAMES_PER_STACK:
                    self.emit_log(
                        f"[WARN] Ueberspringe {outname}: nur {len(registered_files)} von "
                        f"{len(block)} Frames registriert, mindestens {MIN_FRAMES_PER_STACK} erforderlich."
                    )
                    self.move_siril_to_safe_directory(siril)
                    cleanup_remove_dir(block_tmp_dir, self.emit_log)
                    continue
                if len(registered_files) < len(block):
                    self.emit_log(
                        f"[WARN] {outname}: {len(registered_files)} von {len(block)} Frames registriert; "
                        "stacke die registrierten Frames."
                    )

                self.run_cmd(siril, build_stack_command(outname))

                output_fit = block_tmp_dir / f"{outname}.fit"
                if not output_fit.exists():
                    self.emit_log(
                        f"[WARN] Ueberspringe {outname}: Siril hat keine Stack-Datei erzeugt."
                    )
                    self.move_siril_to_safe_directory(siril)
                    cleanup_remove_dir(block_tmp_dir, self.emit_log)
                    continue

                try:
                    write_midpoint_header(output_fit, midpoint, total_exposure, len(registered_files))
                except Exception as exc:
                    raise RuntimeError(
                        f"Stack-Datei erzeugt, Header-Update fehlgeschlagen fuer {outname}: {exc}"
                    ) from exc
                shutil.move(str(output_fit), result_dir / output_fit.name)
                self.emit_log(
                    f"[OK] Gruppe {plan.name} {start_idx:05d}-{end_idx:05d}: "
                    f"{output_fit.name} gespeichert ({len(registered_files)}/{len(block)} Frames)."
                )
                self.move_siril_to_safe_directory(siril)
                cleanup_remove_dir(block_tmp_dir, self.emit_log)

            completed = True
            return result_dir
        finally:
            if completed:
                try:
                    self.move_siril_to_safe_directory(siril)
                except Exception:
                    pass
                cleanup_empty_dir(tmp_dir, self.emit_log)


class StackWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.worker: StackWorker | None = None
        self.source_dir = str(Path.home())
        self.init_ui()

    def init_ui(self) -> None:
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)

        layout = QVBoxLayout()

        intro = QLabel(
            "Waehle den Ordner mit den originalen Seestar .fit-Bildern sowie "
            "die Debayer- und Stack-Optionen. Danach auf Start klicken."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QHBoxLayout()
        self.source_edit = QLineEdit(self.source_dir)
        self.source_edit.setReadOnly(True)
        browse_button = QPushButton("Verzeichnis waehlen")
        browse_button.clicked.connect(self.choose_directory)
        row.addWidget(self.source_edit)
        row.addWidget(browse_button)
        layout.addLayout(row)

        plan_group = QGroupBox("Stack")
        plan_layout = QFormLayout()

        self.plan_mode_combo = QComboBox()
        self.plan_mode_combo.addItem("Sekunden", "time")
        self.plan_mode_combo.addItem("Frames", "frames")
        if DEFAULT_PLAN_MODE == "frames":
            self.plan_mode_combo.setCurrentIndex(1)
        self.plan_mode_combo.currentIndexChanged.connect(self.update_plan_spinboxes)

        self.plan_one_spin = QSpinBox()
        self.plan_one_spin.setRange(1, 100000)
        self.plan_two_spin = QSpinBox()
        self.plan_two_spin.setRange(1, 100000)

        self.plan_one_check = QCheckBox()
        self.plan_two_check = QCheckBox()
        self.plan_all_check = QCheckBox()
        for check in (self.plan_one_check, self.plan_two_check, self.plan_all_check):
            check.setChecked(True)
            check.setText("")
            check.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.plan_one_show_button = QPushButton("Show First")
        self.plan_one_show_button.clicked.connect(lambda: self.show_first_plan_image(0))
        self.plan_two_show_button = QPushButton("Show First")
        self.plan_two_show_button.clicked.connect(lambda: self.show_first_plan_image(1))
        self.plan_all_show_button = QPushButton("Show First")
        self.plan_all_show_button.clicked.connect(lambda: self.show_first_plan_image(2))

        self.plan_one_spin.setFixedWidth(120)
        self.plan_two_spin.setFixedWidth(120)

        plan_layout.addRow("Gruppierung", self.plan_mode_combo)
        plan_layout.addRow("Gruppe 1", self.build_plan_row(self.plan_one_spin, self.plan_one_check, self.plan_one_show_button))
        plan_layout.addRow("Gruppe 2", self.build_plan_row(self.plan_two_spin, self.plan_two_check, self.plan_two_show_button))
        all_label = QLabel(ALL_PLAN_NAME)
        all_label.setFixedWidth(120)
        plan_layout.addRow("Gruppe 3", self.build_plan_row(all_label, self.plan_all_check, self.plan_all_show_button))
        plan_group.setLayout(plan_layout)

        cfa_group = QGroupBox("CFA Debayer")
        cfa_layout = QHBoxLayout()
        self.channel_checks: dict[str, QCheckBox] = {}
        for channel_key, channel in CFA_CHANNELS.items():
            check = QCheckBox(str(channel["label"]))
            check.setChecked(channel_key in DEFAULT_CFA_CHANNELS)
            self.channel_checks[channel_key] = check
            cfa_layout.addWidget(check)
        cfa_layout.addStretch(1)
        cfa_group.setLayout(cfa_layout)

        layout.addWidget(cfa_group)
        layout.addWidget(plan_group)

        button_row = QHBoxLayout()
        self.help_button = QPushButton("Help")
        self.help_button.clicked.connect(self.show_help)
        self.stack_button = QPushButton("Start")
        self.stack_button.clicked.connect(self.start_stack)
        button_row.addWidget(self.help_button)
        button_row.addWidget(self.stack_button)
        layout.addLayout(button_row)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)

        self.setLayout(layout)
        self.update_plan_spinboxes()

    def build_plan_row(self, value_widget: QWidget, check: QCheckBox, show_button: QPushButton) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(14)
        row_layout.addWidget(value_widget)
        row_layout.addWidget(check)
        row_layout.addWidget(show_button)
        row_layout.addStretch(1)
        return row

    def choose_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Ordner mit FITS-Lights waehlen",
            self.source_dir,
        )
        if selected:
            self.source_dir = selected
            self.source_edit.setText(selected)

    def append_log(self, message: str) -> None:
        self.log_view.append(message)

    def current_plan_values(self) -> tuple[int, int]:
        return self.plan_one_spin.value(), self.plan_two_spin.value()

    def current_plan_mode(self) -> str:
        return str(self.plan_mode_combo.currentData())

    def build_available_plans(self) -> tuple[StackPlan, StackPlan, StackPlan]:
        return build_stack_plans(self.current_plan_mode(), self.current_plan_values())

    def build_selected_plans(self) -> tuple[StackPlan, ...]:
        available = self.build_available_plans()
        selected: list[StackPlan] = []
        if self.plan_one_check.isChecked():
            selected.append(available[0])
        if self.plan_two_check.isChecked():
            selected.append(available[1])
        if self.plan_all_check.isChecked():
            selected.append(available[2])
        return tuple(selected)

    def build_selected_channels(self) -> tuple[str, ...]:
        return tuple(
            channel_key
            for channel_key in CFA_CHANNELS
            if self.channel_checks[channel_key].isChecked()
        )

    def update_plan_spinboxes(self) -> None:
        mode = self.current_plan_mode()
        if mode == "time":
            values = DEFAULT_DURATION_PLANS
            suffix = " s"
        else:
            values = DEFAULT_FRAME_PLANS
            suffix = " Frames"

        self.plan_one_spin.blockSignals(True)
        self.plan_two_spin.blockSignals(True)
        self.plan_one_spin.setSuffix(suffix)
        self.plan_two_spin.setSuffix(suffix)
        self.plan_one_spin.setValue(values[0])
        self.plan_two_spin.setValue(values[1])
        self.plan_one_spin.blockSignals(False)
        self.plan_two_spin.blockSignals(False)

    def show_first_plan_image(self, plan_index: int) -> None:
        source_dir = Path(self.source_edit.text().strip()).expanduser()
        if not source_dir:
            QMessageBox.critical(self, "Fehlender Ordner", "Bitte zuerst einen Quellordner waehlen.")
            return

        try:
            plan = self.build_available_plans()[plan_index]
            candidate_channels: tuple[str | None, ...] = (*self.build_selected_channels(), None)
            checked_dirs: list[Path] = []
            result_files: list[Path] = []
            for channel_key in candidate_channels:
                candidate_dir = plan_result_dir(source_dir, plan, channel_key)
                checked_dirs.append(candidate_dir)
                result_files = sorted(
                    path for path in candidate_dir.glob("stack_*.fit") if not is_hidden_fits(path)
                )
                if not result_files:
                    result_files = sorted(path for path in candidate_dir.glob("*.fit") if not is_hidden_fits(path))
                if result_files:
                    break

            if result_files:
                first_image = result_files[0]
                self.append_log(
                    f"[INFO] Zeige erstes Stack-Ergebnis fuer Plan {plan.name}: {first_image.name}"
                )
            else:
                raise FileNotFoundError(
                    f"Kein Stack-Ergebnis fuer Plan {plan.name} vorhanden: "
                    f"{', '.join(path.name for path in checked_dirs)}"
                )

            siril = s.SirilInterface()
            siril.connect()
            try:
                siril.cmd(f'load "{siril_path(first_image)}"')
                siril.cmd("autostretch")
                self.append_log(f"[OK] Bild geladen: {first_image.name}")
            finally:
                siril.disconnect()
        except Exception as exc:
            QMessageBox.critical(self, "Vorschau-Fehler", str(exc))

    def start_stack(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return

        source_dir = self.source_edit.text().strip()
        if not source_dir:
            QMessageBox.critical(self, "Fehlender Ordner", "Bitte zuerst einen Quellordner waehlen.")
            return

        self.log_view.clear()
        self.stack_button.setEnabled(False)
        self.worker = StackWorker(source_dir, self.build_selected_plans(), self.build_selected_channels())
        self.worker.log.connect(self.append_log)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def build_help_text(self) -> str:
        return (
            "Diese App debayert und stackt Seestar Bilder mit Siril.\n\n"
            "Ablauf:\n"
            "1. Ordner mit .fit/.fits-Lights waehlen.\n"
            "2. Stack-Plaene auswaehlen.\n"
            "3. CFA-Outputs auswaehlen: L, R, G und/oder B.\n"
            "4. Optional mit 'Show First' das erste vorhandene Stack-Ergebnis laden.\n"
            "5. Mit 'Start' debayern, registrieren und stacken.\n\n"
            "Debayer/CFA-Outputs:\n"
            "- Wenn CFA/Bayer-Header erkannt werden, erzeugt die App zuerst die ausgewaehlten Kanalbilder.\n"
            "- L: Siril debayer + RGB split + ITU-R BT.601 Luma aus 0.299 R + 0.587 G + 0.114 B.\n"
            "- R/G/B: die voll aufgeloesten debayerten CFA-Farbkanaele.\n"
            "- Die Kanalbilder erhalten FILTER='L', 'R', 'G' oder 'B'; CHANMODE dokumentiert LUMA601 oder CFA_R/G/B.\n"
            "- Wenn KEEP_LUMA_IMAGES = True ist, bleiben die Kanalbilder in eigenen Ordnern erhalten.\n"
            "- Wenn keine CFA/Bayer-Header erkannt werden, werden die Eingabebilder direkt gestackt.\n\n"
            "Ordner:\n"
            "- Originalbilder werden nicht veraendert.\n"
            "- Ergebnisordner fuer CFA-Daten werden kanalbezogen angelegt, z.B. '<quelle>_g-stack100sec'.\n"
            "- Ergebnisordner fuer Nicht-CFA-Daten bleiben ohne Kanal-Suffix, z.B. '<quelle>-stack100sec'.\n"
            "- Kanal-Einzelbilder werden neben dem Quellordner in '<quelle>_l', '<quelle>_r', '<quelle>_g' oder '<quelle>_b' abgelegt.\n"
            "- Temp-Ordner werden ebenfalls daneben angelegt, z.B. '<quelle>-tmp100sec'.\n"
            "- Debayer-Zwischenordner werden temporaer daneben angelegt, z.B. '<quelle>-tmpdebayer' und '<quelle>-tmp_g'.\n"
            "- Temp-Ordner werden erst nach erfolgreichem Gesamtlauf und nach Siril-Disconnect geloescht.\n\n"
            "Sicherheit:\n"
            f"- OVERWRITE_RESULTS steht aktuell auf {OVERWRITE_RESULTS}.\n"
            "- Wenn OVERWRITE_RESULTS = False ist, bricht der Lauf bei vorhandenen Ergebnisordnern ab.\n\n"
            "Stack-Qualitaet:\n"
            f"- Stack-Bloecke mit weniger als {MIN_FRAMES_PER_STACK} Frames werden uebersprungen.\n"
            "- Nach der Registrierung wird geprueft, ob alle Frames registriert wurden.\n\n"
            "FITS-Header:\n"
            "- DATE-OBS ist erforderlich; Dateien ohne DATE-OBS werden uebersprungen.\n"
            f"- EXPTIME wird verwendet, falls vorhanden, sonst {DEFAULT_SUBFRAME_EXPOSURE:g}s als Fallback.\n\n"
            "Anpassbare Defaults stehen oben im Script im Abschnitt 'User settings'."
        )

    def show_help(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Seestar Stack Help")
        dialog.resize(760, 520)

        layout = QVBoxLayout(dialog)
        text_view = QTextEdit()
        text_view.setReadOnly(True)
        text_view.setPlainText(self.build_help_text())
        layout.addWidget(text_view)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def on_finished(self, success: bool, message: str) -> None:
        self.stack_button.setEnabled(True)
        self.append_log(message)
        if success:
            QMessageBox.information(self, "Stack", message)
        else:
            QMessageBox.critical(self, "Fehler", message)
        self.worker = None


def run_app() -> None:
    app = QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QApplication(sys.argv)

    window = StackWindow()
    window.show()

    if owns_app:
        app.exec()


def main() -> None:
    run_app()


if __name__ == "__main__":
    main()
