# siril-seestar-stack

Siril Python script for preprocessing Seestar FITS light frames: debayer raw CFA data, export derived L/R/G/B channel images, and stack frames in user-defined groups.

This script is a preprocessing tool. It prepares Seestar FITS frames for downstream photometry workflows; it does not perform photometric measurement itself.

## Features

- Debayers raw CFA/Bayer Seestar FITS frames in Siril.
- Exports derived `L`, `R`, `G`, and `B` channel images.
- Uses ITU-R BT.601 luma weights for `L`: `0.299 R + 0.587 G + 0.114 B`.
- Stacks by time span, by frame count, or all frames.
- Writes stack timing metadata to FITS headers.
- Confirms before clearing existing result folders.
- Ends each run with a compact summary in the log.

## Requirements

- Siril with Python scripting support.
- Siril Python environment with `sirilpy`, `PyQt6`, `astropy`, and `numpy`.
- Seestar `.fit` or `.fits` light frames in one dedicated source folder.

The script is intended to run inside Siril's Python environment, not as a standalone system-Python program.

## Installation in Siril

1. Copy `Seestar_stack.py` into a Siril script directory.
2. Open Siril.
3. Add the script directory in Siril Preferences if needed.
4. Start the script from Siril's Scripts menu.

Repository:

```bash
git clone https://gitlab.com/Aquarius58/siril-seestar-stack.git
cd siril-seestar-stack
```

## Workflow

1. Copy the original Seestar `.fit` / `.fits` light frames into one dedicated folder.
2. Start Siril.
3. Run `Seestar_stack.py` from the Scripts menu.
4. Select the folder containing the FITS light frames.
5. Select CFA output channels as needed: `L`, `R`, `G`, `B`.
6. Select stack grouping: seconds, frame count, or `ALL`.
7. Click `Start`.
8. Check the result folders, log summary, and FITS headers.

## Result Folders

Results are written next to the selected source folder. Original input frames are not modified.

Example source folder:

```text
Light_001
```

Possible result folders:

```text
Light_001_l
Light_001_r
Light_001_g
Light_001_b
Light_001_l-stack100sec
Light_001_l-stack1000sec
Light_001_l-stackall
```

For non-CFA input, stack folders use no channel suffix, for example:

```text
Light_001-stack100sec
```

Temporary folders use names such as `<source>-tmp100sec`, `<source>-tmpdebayer`, or `<source>-tmp_g`. They are removed after a successful run.

## Overwrite Safety

If result folders already exist, the script lists them before the run starts and asks for confirmation. Existing result folders are cleared only after explicit confirmation.

Use a dedicated source folder containing only the Seestar FITS frames for one session.

## Timing Notes

Seestar `DATE-OBS` is treated as the exposure end time of an individual frame.

For stack results:

- `DATE-OBS` is rewritten to the UTC mid-exposure time of the stack block.
- `EXPTIME` is rewritten to the total exposure time of the frames used in the stack.
- `NCOMBINE` records the number of frames used in the stack.

The script assumes that the input Seestar FITS timestamps are already correct. It does not apply clock corrections or camera-specific timestamp corrections.

## Stack Acceptance

Stack blocks must meet the minimum criteria configured in the script:

- at least `MIN_FRAMES_PER_STACK` registered frames
- for time-based plans, at least `MIN_STACK_COMPLETION_FRACTION` of the requested duration

End-of-sequence blocks are accepted when these criteria are met.

## Support

Bug reports and support requests:

https://gitlab.com/Aquarius58/siril-seestar-stack/-/issues

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
