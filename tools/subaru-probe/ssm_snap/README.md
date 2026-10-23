# ssm_snap: find which RAM byte follows an input (read-only)

RomRaider shows the clutch, brake, neutral and cruise buttons through the ECU's SSM switch bytes, but not which RAM cell the
firmware itself reads. This tool takes snapshots of a RAM span with the input held in state A, state B, A, B and lists the
bytes that are constant inside each state and differ between them. Pure standard library; one file.

**Read-only.** It sends only the SSM init (0xBF) and SSM read (0xA8) frames RomRaider already sends. It refuses the on-chip
I/O range (>= 0xFFC000), because some I/O registers clear on read.

## Requirements
- Windows, **32-bit** Python 3.10+ for the real adapter (the OpenPort DLL `op20pt32.dll` is 32-bit). `selftest`/`simulate` run on any Python.
- Close RomRaider, SubaruEdit and EcuFlash first (one program may hold the OpenPort). Ignition on, engine idling in neutral, parking brake as normal, laptop on AC.

## Check it first (no car)
```
py -3-32 ssm_snap.py selftest     (frame builder reproduces RomRaider's recorded request byte for byte)
py -3-32 ssm_snap.py simulate     (simulated ECU: finds the two planted bytes)
```
## Then in the car, engine idling
```
py -3.10-32 ssm_snap.py probe --n 64   (init + a full-size 64-byte read; RAM RPM float @FF6990 should be ~1000 rpm)
py -3-32 ssm_snap.py capture --start 0xFF9800 --end 0xFFA000 --states up,down,up,down --out snap_clutch_1
py -3-32 ssm_snap.py diff snap_clutch_1
```
`capture` asks you to set each state ("up" = pedal released, "down" = pedal held) and holds it for the whole pass (about 0.8 s per
64 bytes, so 2 KB is ~25 s, 8 KB ~100 s). Spans, in order: 0xFF9800-0xFFA000, 0xFF8000-0xFF9800, 0xFF6000-0xFF8000, 0xFF2400-0xFF6000.
`diff` prints addresses and values that are stable in each state and differ between states, with the XOR (which bit). Repeat
with brake, handbrake and the spare toggle (use `--states off,on,off,on`).

If `probe` fails with a status code or bad checksum, stop and report the message; do not retry in a loop. `--sub 00` selects the
single-shot A8 form (unproven on K-line); the default `01` is what RomRaider uses.

## Fixed 2026-10-23 after the first hardware run
`probe` (4-byte reads) worked but `capture` failed with J2534 status 9 (ERR_TIMEOUT) on the first full-size request. Cause: a 64-address
request is ~200 bytes, about 415 ms on the wire at 4800 baud, and the write timeout was 100 ms. The write timeout is now wire time + 1.5 s,
every pass starts with a line break + fresh SSM init, any failed read re-inits before retrying, and `--countdown N` (default 5) gives
audible beeps before a pass starts and when it ends. Use `probe --n 64` to test a full-size request before capturing.
