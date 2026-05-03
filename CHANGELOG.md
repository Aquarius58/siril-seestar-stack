# Changelog

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
