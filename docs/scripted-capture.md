# Scripted logger captures

The logger accepts a bounded XML capture plan with `--capture-script`. Plans select channels already defined by the loaded RomRaider logger definitions, check an expected ECU ID, walk the operator through timed prompts, and write action markers beside the normal logger CSV. The script language is data only; it cannot execute Java, Python, or shell code and has no ECU write operations.

## Run

Start RomRaider as usual and pass the plan path to the logger:

```text
RomRaider.exe --capture-script="C:\path\to\capture.xml"
```

The logger uses the existing selected serial port and protocol. If it is not connected, the script starts the normal logger connection. It waits for the ECU ID and aborts before recording if the ID does not match `expected-ecu-id`. It then replaces the current selection in the Parameters and Switches lists with the exact channel IDs in the script. Missing IDs abort the capture before a CSV is started.

For every step, a dialog shows the action and its duration. Press OK once the action is stable; the script records the step start, logs for the requested number of seconds, then records the step end. Cancel ends the capture. The normal RomRaider CSV is written to the configured log folder. A companion `romraider_events_<name>_<timestamp>.csv` records epoch and elapsed milliseconds, event name, step ID, and prompt text.

Keep the channel list short for K-Line. The plan does not change the logger's polling cadence. It does not disable the UI, so the operator can still stop the logger manually.

## EP5I400F clutch/neutral correlation plan

`capture_scripts/ep5i400f_switch_correlation.xml` is a narrow stationary plan for comparing the native clutch and neutral channels with custom RAM probes X943/X944 (RAM addresses 0xFFFF9E9B/0xFFFF9E9C). The current [probe logger definition in the Subaru tuning repository](https://github.com/snarshmallow/Subaru_tuning_suite_32bit_Denso/blob/codex/ecu-id-7007-launch-control-2026-10-21/defs/probe_logger.xml) associates those custom probe addresses with ECU ID `8424385107`, while the current target assumption is `8424387007`. The plan requires `8424387007` and will abort if the custom IDs are not loaded for that ECU. Update and verify those probe definitions against the target ROM before using this plan; do not change the expected ID to bypass the check.

This script is for stationary, read-only logging. It does not authorize editing or flashing a ROM.
