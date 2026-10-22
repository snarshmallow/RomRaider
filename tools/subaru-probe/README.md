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

## Batch G (added 2026-10-22): the RAM behind the native switches
`probe_params.csv` rows X1000-X1019 read the RAM byte the ECU serves for each SSM switch byte (0x61-0x6E, 0xD4-0xD7, 0x120,
0x121), taken from the pointer table at ROM 0xA1A28 (index = SSM logical address). Load `probe_batch_G_native_switch_bytes.xml`
next to the native clutch/neutral/brake switches: X1019 (0xFF9D5A) bit 7 should follow native clutch S63, X1001 (0xFF9B76) bit 7
neutral S4. The script can extend an existing probe definition in place (ids already present are skipped):
`python make_probe_def.py --base <existing logger_PROBE.xml> --out-dir out`.
