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
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# User settings
SCRIPT_VERSION = "0.1.0"
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
LUMA_IMAGE_SUFFIX = "_d_l"
OVERWRITE_RESULTS = True
KEEP_TEMP_ON_ERROR = True
CLEANUP_RETRIES = 6
CLEANUP_RETRY_DELAY_SECONDS = 0.5
VERBOSE_COMMAND_LOG = False
VERBOSE_FRAME_LOG = False

WINDOW_TITLE = f"Seestar Debayer & Stack {SCRIPT_VERSION}"
WINDOW_WIDTH = 760
WINDOW_HEIGHT = 420

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
        return any(key in header for key in ("BAYERPAT", "XBAYROFF", "YBAYROFF", "ROWORDER"))


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


def temp_dir_has_contents(path: Path) -> bool:
    return path.exists() and any(path.iterdir())


def siril_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def build_output_header(source_path: Path) -> fits.Header:
    header = fits.getheader(source_path).copy()
    source_filter = header.get("FILTER")
    for key in ("BAYERPAT", "XBAYROFF", "YBAYROFF", "ROWORDER"):
        if key in header:
            del header[key]
    if source_filter:
        header["SRCFILT"] = (str(source_filter), "Original source filter")
    header["FILTER"] = ("L", "Derived luminance channel")
    header["CHANMODE"] = ("LUMA601", "RGB luma weights: 0.299 R, 0.587 G, 0.114 B")
    header["DERIVED"] = (True, "Derived from debayered CFA data")
    return header


def plan_result_dir(source_dir: Path, plan: StackPlan) -> Path:
    return source_dir.parent / f"{source_dir.name}-stack{plan.suffix}"


def plan_temp_dir(source_dir: Path, plan: StackPlan) -> Path:
    return source_dir.parent / f"{source_dir.name}-tmp{plan.suffix}"


def debayer_temp_dir(source_dir: Path) -> Path:
    return source_dir.parent / f"{source_dir.name}-tmpdebayer"


def luma_temp_dir(source_dir: Path) -> Path:
    return source_dir.parent / f"{source_dir.name}-tmpluma"


def luma_result_dir(source_dir: Path) -> Path:
    return source_dir.parent / f"{source_dir.name}{LUMA_IMAGE_SUFFIX}"


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


class StackWorker(QThread):
    log = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, source_dir: str, selected_plans: tuple[StackPlan, ...]):
        super().__init__()
        self.source_dir = Path(source_dir).expanduser()
        self.selected_plans = selected_plans
        self.debayer_dirs: tuple[Path, Path] | None = None
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
                message = f"Luma-Export fertig. Ergebnisse in {result_dir}"
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
        self.preflight_output_dirs(needs_debayer)

        siril = s.SirilInterface()
        siril.connect()
        if VERBOSE_COMMAND_LOG:
            self.emit_log("[OK] Mit Siril verbunden.")

        final_result_dir: Path | None = None
        completed = False
        try:
            frames = self.prepare_processing_frames(siril, source_frames, needs_debayer)
            self.keep_luma_images(frames)
            for plan in self.selected_plans:
                final_result_dir = self.run_stack_plan(siril, plan, frames)
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
                return luma_result_dir(self.source_dir)
            raise RuntimeError(
                "Keine Stack-Ergebnisse erzeugt. Bitte mindestens einen Stack-Plan auswaehlen."
            )
        return final_result_dir

    def prepare_processing_frames(
        self,
        siril: s.SirilInterface,
        source_frames: list[FitsFrame],
        needs_debayer: bool,
    ) -> list[FitsFrame]:
        if not needs_debayer:
            if VERBOSE_FRAME_LOG:
                self.emit_log("[INFO] Keine CFA/Bayer-Header erkannt; stacke Eingabebilder direkt.")
            return source_frames

        if VERBOSE_FRAME_LOG:
            self.emit_log("[INFO] CFA/Bayer-Header erkannt; erzeuge RGB-Luma601-L-Bilder vor dem Stack.")
        debayer_dir = debayer_temp_dir(self.source_dir)
        luma_dir = luma_temp_dir(self.source_dir)
        self.debayer_dirs = (debayer_dir, luma_dir)

        if debayer_dir.exists():
            empty_dir(debayer_dir)
        else:
            debayer_dir.mkdir(parents=True)
        if luma_dir.exists():
            empty_dir(luma_dir)
        else:
            luma_dir.mkdir(parents=True)

        try:
            for frame in source_frames:
                shutil.copy2(frame.path, debayer_dir / frame.path.name)

            self.run_cmd(siril, f"requires {SIRIL_REQUIRES}")
            self.run_cmd(siril, OUTPUT_BITS_COMMAND)
            self.run_cmd(siril, f'cd "{siril_path(debayer_dir)}"')
            self.run_cmd(siril, "convert deb -debayer")

            for index, frame in enumerate(source_frames, start=1):
                suffix = f"{index:05d}"
                self.run_cmd(siril, f"load deb_{suffix}.fit")
                self.run_cmd(siril, f"split rgb_{suffix}_r rgb_{suffix}_g rgb_{suffix}_b")
                output_path = self.write_rgb_luma_output(frame.path, debayer_dir, luma_dir, suffix)
                if VERBOSE_FRAME_LOG:
                    self.emit_log(f"[OK] Luma erzeugt: {output_path.name}")

            luma_frames = collect_valid_fits(luma_dir, self.emit_log)
            if not luma_frames:
                raise FileNotFoundError(f"Keine Luma-FITS erzeugt in {luma_dir}")
            self.emit_log(f"[OK] Luma-Bilder erzeugt: {len(luma_frames)} Dateien.")
            return luma_frames
        finally:
            if not KEEP_TEMP_ON_ERROR:
                remove_dir(debayer_dir)

    def write_rgb_luma_output(
        self,
        source_path: Path,
        workdir: Path,
        output_dir: Path,
        suffix: str,
    ) -> Path:
        rgb_r = fits.getdata(workdir / f"rgb_{suffix}_r.fit").astype(np.float64)
        rgb_g = fits.getdata(workdir / f"rgb_{suffix}_g.fit").astype(np.float64)
        rgb_b = fits.getdata(workdir / f"rgb_{suffix}_b.fit").astype(np.float64)
        rgb_luma = np.clip(
            np.rint(0.299 * rgb_r + 0.587 * rgb_g + 0.114 * rgb_b),
            0,
            65535,
        ).astype(np.uint16)

        output_path = output_dir / f"{source_path.stem}{LUMA_IMAGE_SUFFIX}.fit"
        fits.writeto(output_path, rgb_luma, header=build_output_header(source_path), overwrite=True)
        return output_path

    def preflight_output_dirs(self, needs_debayer: bool) -> None:
        if VERBOSE_COMMAND_LOG:
            self.emit_log(f"[INFO] Quellordner: {self.source_dir}")
            self.emit_log(f"[INFO] Ergebnis-/Temp-Ordner werden neben diesem Ordner angelegt.")

        for plan in self.selected_plans:
            result_dir = plan_result_dir(self.source_dir, plan)
            if result_dir.exists() and not OVERWRITE_RESULTS:
                raise FileExistsError(
                    f"Ergebnisordner existiert bereits: {result_dir}. "
                    "Loeschen oder OVERWRITE_RESULTS = True setzen."
                )
            assert_can_create_directory(result_dir)
            assert_can_create_directory(plan_temp_dir(self.source_dir, plan))

        if needs_debayer:
            assert_can_create_directory(debayer_temp_dir(self.source_dir))
            assert_can_create_directory(luma_temp_dir(self.source_dir))
            if KEEP_LUMA_IMAGES:
                result_dir = luma_result_dir(self.source_dir)
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

    def keep_luma_images(self, frames: list[FitsFrame]) -> None:
        if not KEEP_LUMA_IMAGES or self.debayer_dirs is None:
            return

        luma_dir = self.debayer_dirs[1]
        luma_frames = [frame for frame in frames if frame.path.parent == luma_dir]
        if not luma_frames:
            return

        result_dir = luma_result_dir(self.source_dir)
        self.prepare_result_dir(result_dir)
        for frame in luma_frames:
            shutil.copy2(frame.path, result_dir / frame.path.name)
        self.emit_log(f"[OK] Luma-Einzelbilder erhalten: {len(luma_frames)} Dateien in {result_dir.name}.")

    def run_stack_plan(self, siril: s.SirilInterface, plan: StackPlan, frames: list[FitsFrame]) -> Path:
        tmp_dir = plan_temp_dir(self.source_dir, plan)
        result_dir = plan_result_dir(self.source_dir, plan)
        completed = False

        self.prepare_result_dir(result_dir)
        if tmp_dir.exists():
            retry_empty_dir(tmp_dir)
        else:
            tmp_dir.mkdir(parents=True)

        try:
            if VERBOSE_COMMAND_LOG:
                self.emit_log(f"[INFO] Starte Stack-Plan {plan.name}")
            self.run_cmd(siril, f"requires {SIRIL_REQUIRES}")
            self.run_cmd(siril, OUTPUT_BITS_COMMAND)

            blocks = split_into_blocks(frames, plan)
            for index, block in blocks:
                if not block:
                    continue

                start_idx = index + 1
                end_idx = index + len(block)
                outname = f"stack_{plan.suffix}_{start_idx:05d}-{end_idx:05d}"

                if len(block) < MIN_FRAMES_PER_STACK:
                    self.emit_log(
                        f"[WARN] Ueberspringe {outname}: nur {len(block)} Frames, "
                        f"mindestens {MIN_FRAMES_PER_STACK} erforderlich."
                    )
                    continue

                midpoint, total_exposure = compute_midpoint(block)
                if temp_dir_has_contents(tmp_dir):
                    retry_empty_dir(tmp_dir)
                for frame in block:
                    shutil.copy2(frame.path, tmp_dir / frame.path.name)

                self.run_cmd(siril, f'cd "{siril_path(tmp_dir)}"')
                self.run_cmd(siril, f"link {TEMP_BASENAME} -out=.")

                self.run_cmd(siril, build_register_command(self.emit_log))
                if REGISTRATION_TWO_PASS:
                    self.run_cmd(siril, build_seqapplyreg_command())

                registered_files = collect_registered_files(tmp_dir)
                if not registered_files:
                    raise RuntimeError(
                        f"Registrierung fehlgeschlagen fuer {outname}: "
                        f"keine registrierten Dateien r_{TEMP_BASENAME}_*.fit in {tmp_dir} gefunden."
                    )
                if len(registered_files) < len(block):
                    raise RuntimeError(
                        f"Registrierung unvollstaendig fuer {outname}: "
                        f"{len(registered_files)} von {len(block)} Frames registriert."
                    )

                self.run_cmd(siril, build_stack_command(outname))

                output_fit = tmp_dir / f"{outname}.fit"
                if not output_fit.exists():
                    raise FileNotFoundError(f"Siril hat keine Stack-Datei erzeugt: {output_fit}")

                write_midpoint_header(output_fit, midpoint, total_exposure, len(block))
                shutil.move(str(output_fit), result_dir / output_fit.name)
                self.emit_log(
                    f"[OK] Gruppe {plan.name} {start_idx:05d}-{end_idx:05d}: "
                    f"{output_fit.name} gespeichert."
                )
                self.move_siril_to_safe_directory(siril)

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

        intro = QLabel("Waehle den Ordner mit den FITS-Lights und starte den Stack-Workflow.")
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

        plan_group = QGroupBox("Stack-Plaene")
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

        tabs = QTabWidget()
        stack_tab = QWidget()
        stack_layout = QVBoxLayout(stack_tab)
        stack_layout.addWidget(plan_group)
        stack_layout.addStretch(1)
        tabs.addTab(stack_tab, "Stack")
        tabs.setDocumentMode(True)
        layout.addWidget(tabs)

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
            result_dir = plan_result_dir(source_dir, plan)
            result_files = sorted(path for path in result_dir.glob("stack_*.fit") if not is_hidden_fits(path))
            if not result_files:
                result_files = sorted(path for path in result_dir.glob("*.fit") if not is_hidden_fits(path))

            if result_files:
                first_image = result_files[0]
                self.append_log(
                    f"[INFO] Zeige erstes Stack-Ergebnis fuer Plan {plan.name}: {first_image.name}"
                )
            else:
                raise FileNotFoundError(
                    f"Kein Stack-Ergebnis fuer Plan {plan.name} vorhanden: {result_dir}"
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
        self.worker = StackWorker(source_dir, self.build_selected_plans())
        self.worker.log.connect(self.append_log)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def build_help_text(self) -> str:
        return (
            "Diese App stackt Seestar-FITS-Lights mit Siril.\n\n"
            "Ablauf:\n"
            "1. Ordner mit .fit/.fits-Lights waehlen.\n"
            "2. Stack-Plaene auswaehlen.\n"
            "3. Optional alle Stack-Plaene abwaehlen, um nur Luma-Einzelbilder zu erzeugen.\n"
            "4. Optional mit 'Show First' das erste vorhandene Stack-Ergebnis laden.\n"
            "5. Mit 'Start' debayern, registrieren und stacken.\n\n"
            "Debayer/Luma:\n"
            "- Wenn CFA/Bayer-Header erkannt werden, erzeugt die App zuerst L-Bilder.\n"
            "- Methode: Siril debayer + RGB split + RGB-Luma601 = 0.299 R + 0.587 G + 0.114 B.\n"
            "- Die L-Bilder erhalten FILTER='L'; Bayer-Header werden entfernt.\n"
            f"- Wenn KEEP_LUMA_IMAGES = True ist, bleiben die L-Bilder als '*{LUMA_IMAGE_SUFFIX}.fit' in einem eigenen Ordner erhalten.\n"
            "- Wenn keine CFA/Bayer-Header erkannt werden, werden die Eingabebilder direkt gestackt.\n\n"
            "Ordner:\n"
            "- Originalbilder werden nicht veraendert.\n"
            "- Ergebnisordner werden neben dem Quellordner angelegt, z.B. '<quelle>-stack100sec'.\n"
            f"- Luma-Einzelbilder werden neben dem Quellordner in '<quelle>{LUMA_IMAGE_SUFFIX}' abgelegt.\n"
            "- Temp-Ordner werden ebenfalls daneben angelegt, z.B. '<quelle>-tmp100sec'.\n"
            "- Debayer-Zwischenordner werden ebenfalls temporaer daneben angelegt.\n"
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
