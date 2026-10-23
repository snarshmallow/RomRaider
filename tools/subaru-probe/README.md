# subaru-probe: raw-RAM logging in RomRaider without hand-picking parameters

Car: 2013 Impreza 2.0i, Denso SH7058. The ROM carries ECU ID **8424387007** (bytes at 0xDD140; 8424385107 appears
nowhere in the stock or current reads), but earlier probe logs did offer parameters keyed to 8424385107, so it is not
settled which ID RomRaider reports. The generated definition is therefore keyed to BOTH (`--ecu-id a,b`). Note the
ID RomRaider shows at connect in every log note.

## Use (Windows)
1. Copy your *stock* `logger_STD_EN_v370.xml` somewhere (do not use an earlier probe file as the base).
2. In this folder:
   ```
   python make_probe_def.py --base "C:\path\to\logger_STD_EN_v370.xml" --params probe_params.csv --ecu-id 8424387007 --out-dir out
   ```
3. RomRaider: Logger > Settings > Logger definition file = `out\logger_PROBE.xml`; restart the logger; connect.
4. Logger > Profile > Load = `out\probe_profile_lean.xml` first (18 channels, fast polling, includes X900 = RPM read
   from RAM as a positive control that must track P8), or `out\probe_profile.xml` for everything. All probe parameters, a few standard ones
   (RPM, speed, throttle, MAF, coolant) and the standard input switches (clutch, neutral, brake, cruise buttons,
   defogger, handle, ...) are then selected. Start logging; the CSV lands in RomRaider's normal log folder.

## Changing what is logged
Edit `probe_params.csv` (one row per RAM byte/word/float) and re-run step 2; ask Claude for a new CSV and paste it
over the file. Addresses may be written 0xFFFF6990 or 0xFF6990. SSM is a 24-bit address space; the top byte is dropped.

## Limits
- Read-only: this only reads RAM through the standard SSM read path RomRaider already uses.
- Each additional parameter slows the sample rate; keep the list to what the current question needs.
- It does not prove what a byte means. A CAND address is promoted only by a log that shows it moving as claimed.

## Batch G / H (2026-10-22/23): RETRACTED
Rows X1000-X1019 and X1100-X1119 in probe_params.csv were chosen from ROM 0xA1A28, which I took to be an SSM pointer table. It is the literal pool of the
routine at ~0xA1960, so those cells are not the SSM switch/standard bytes. A Fast-K run with native ground truth confirmed it (RAM cells stayed 0 while the
native clutch, brake and neutral bits moved). Keep the rows only as history; do not read meaning into them. The script's in-place extension still works
(`python make_probe_def.py --base <existing logger_PROBE.xml> --out-dir out` skips ids already present).

## Batch R (2026-10-23): the real RAM flags behind the native bits
The Ghidra pass (cloud/arcturus-ghidra) found the SSM A8 handler: logical addresses < 0x350 call getter routines from the table at ROM
0x6DD68; the getter for 0x121 (0x6399A, re-read by me) packs bytes from RAM: clutch 0xFFFF692F, stop lamp 6239, set 6236, resume 6237, brake 6238,
main 6235, status 623C; getter 0x62EF4 packs neutral 6908 and others; cancel is 6249. Batch R (X1200-X1224) reads those cells next to the native bits.
`probe_batch_R_input_flags.xml` selects them together with the native switches; every value should match its native bit (1 = pressed).
