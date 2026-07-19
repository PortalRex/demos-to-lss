# Demos to LSS

Turn a Portal 2 speedrun's demo recordings into a LiveSplit splits file (.lss) with
exact game-time splits for all 62 chambers.

**Use it in your browser: https://portalrex.github.io/demos-to-lss/** — drop the run's
.dem files, their folder, or a .zip / .rar / .tar.gz archive. Everything runs locally;
demos are never uploaded.

## How it works

SAR (SourceAutoRecord) embeds a complete speedrun record in the final demo of a
finished run — every split with per-session tick counts. The converter reads that
record and reproduces LiveSplit's times exactly, including SAR's float32 arithmetic
(`float32(ticks × float32(1/60))`) and LiveSplit's truncated-TimeSpan storage.

Older demos without embedded timing (pre-2022 SAR) are reconstructed from per-map
tick sums with calibrated anchors:

- timer start: intro1's scripted autosave fires a fixed 215 ticks after the start moment
- run end: the ending script's `map_wants_save_disable` fires 6 ticks before the
  portal opens on the moon (matches UntitledParser's entity-level detection)
- standard 5:16.33 offset; pause time counts only on `sp_a1_wakeup`

Stray demos that aren't part of the run's filename series are ignored automatically.

## Local tools

- `demos_to_lss.py` — the same converter as a Python script. Double-click
  `Demos to LSS.bat` (or the script) for a folder picker, drag a demo folder onto the
  .bat, or run `python demos_to_lss.py <demo_folder>`. No dependencies beyond Python 3.
- `index.html` — the website, a single self-contained file (fonts and the RAR decoder
  are inlined). Open it straight from disk if you like.

The parsing logic lives in both `demos_to_lss.py` and the `core` script inside
`index.html` — keep them in sync when changing timing rules.
