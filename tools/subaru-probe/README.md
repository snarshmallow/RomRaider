# subaru-probe: raw-RAM logging in RomRaider without hand-picking parameters

Car: 2013 Impreza 2.0i, Denso SH7058, ECU ID **8424387007** (read from ROM bytes at 0xDD140; the older
8424385107 in the tuning repo is wrong for this car, so RomRaider never offered parameters keyed to it).

## Use (Windows)
1. Copy your *stock* `logger_STD_EN_v370.xml` somewhere (do not use an earlier probe file as the base).
2. In this folder:
   ```
   python make_probe_def.py --base "C:\path\to\logger_STD_EN_v370.xml" --params probe_params.csv --ecu-id 8424387007 --out-dir out
   ```
3. RomRaider: Logger > Settings > Logger definition file = `out\logger_PROBE.xml`; restart the logger; connect.
4. Logger > Profile > Load = `out\probe_profile.xml`. All probe parameters, a few standard ones
   (RPM, speed, throttle, MAF, coolant) and the standard input switches (clutch, neutral, brake, cruise buttons,
   defogger, handle, ...) are then selected. Start logging; the CSV lands in RomRaider's normal log folder.

## Changing what is logged
Edit `probe_params.csv` (one row per RAM byte/word/float) and re-run step 2; ask Claude for a new CSV and paste it
over the file. Addresses may be written 0xFFFF6990 or 0xFF6990. SSM is a 24-bit address space; the top byte is dropped.

## Limits
- Read-only: this only reads RAM through the standard SSM read path RomRaider already uses.
- Each additional parameter slows the sample rate; keep the list to what the current question needs.
- It does not prove what a byte means. A CAND address is promoted only by a log that shows it moving as claimed.
