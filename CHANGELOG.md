# Changelog

## 0.2.3 - 2026-05-04

- Fixed stacked FITS `EXPTIME` after partial registration so it reflects only the frames actually used by Siril.
- Added deterministic temporary source numbering so registered `r_tmp_*.fit` files can be mapped back to their source frames.
- Changed the minimum stack acceptance logic from a fixed 3-frame rule to at least 2 frames plus, for time-based plans, a minimum exposure derived from the selected stack duration.
- Updated stack log messages to include the final `EXPTIME` written to the FITS header.

## 0.2.2 - 2026-05-03

- Improved the main window wording for original Seestar `.fit` images and Debayer/Stack options.
- Removed the redundant `Stack` tab and placed `CFA Debayer` above `Stack`.
- Renamed the option groups to `CFA Debayer` and `Stack`.

## 0.2.1 - 2026-05-01

- Fixed stack block processing so each block runs in its own clean temporary work directory.
- Prevented stale frames and rejection maps from earlier blocks from being included in later Siril sequences.

## 0.2.0 - 2026-05-01

- Added selectable CFA channel outputs for `Seestar_stack.py`: `L`, `R`, `G`, and `B`.
- Added channel-specific CFA output directories, for example `<source>_g-stack100sec`.
- Added derived FITS header metadata: `FILTER`, `CHANMODE`, `DERIVED`, and optional `SRCFILT`.
- Removed Bayer header keys from derived channel FITS files.
- Kept non-CFA stack output directory names unchanged.

## 0.1.0

- Initial Seestar debayer and stack workflow.
