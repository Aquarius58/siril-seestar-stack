# Changelog

## 0.4.0 - 2026-07-27

- Checked CFA/Bayer metadata across every input frame instead of relying on the
  first frame alone.
- Rejected mixed input folders containing original CFA frames and non-CFA or
  already processed FITS files, with actionable diagnostics and `.bfit` backup
  information.
- Displayed the underlying validation error in the start-failure dialog.
- Added Windows-native process detection for reliable stale single-instance
  lock handling.
- Made `SCRIPT_VERSION` the single authoritative version source.

## 0.3.0 - 2026-07-14

- Renamed the public script from `Seestar_stack.py` to `SeePhot_CFA.py`.
- Added a dark standalone interface and single-instance protection.
- Added editable and pasted source-path handling, including local `file://` URLs.
- Improved Siril display-context cleanup and per-stack-block failure recovery.
- Added FITS `SSAP` provenance and preservation of unambiguous channel metadata.
- Added integration hooks for reuse by the companion SeePhot light-curve application.
- Moved project links to GitHub and changed the license to GPL-3.0-or-later.

## 0.2.3 - 2026-05-19

Initial commit.

- Added self-contained script header and documentation for upstream review.
- Added photometry-oriented CFA channel extraction for `L`, `R`, `G`, and `B`.
- Added registered stack groups by duration, frame count, or `ALL`.
- Added FITS timing metadata for stack products: `DATE-OBS`, `DATE-END`, `DATE-AVG`, `MJD-AVG`, `EXPTIME`, and `NCOMBINE`.
- Added overwrite confirmation and temporary folder cleanup for generated products.
