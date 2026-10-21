# Windows x86 build for the Tactrix OpenPort 2.0

This branch creates a 32-bit Windows build of the scripted logger. The installed OpenPort J2534 entry is registered in the 32-bit registry view and points to `op20pt32.dll`; a 64-bit JVM cannot load that driver. The branch therefore sets the generated RomRaider architecture to `32` and uses `lib/windows/32`.

## Build on Windows

The build targets Java 6 bytecode. Use the installed 32-bit JDK at `C:\Program Files (x86)\Java\jdk1.6.0_45` and Apache Ant:

```powershell
$env:JAVA_HOME = 'C:\Program Files (x86)\Java\jdk1.6.0_45'
$env:JRE_DIR = Join-Path $env:JAVA_HOME 'jre'
$env:Path = "$env:JAVA_HOME\bin;$env:Path"
ant standalone-windows-x86
```

The ZIP is created under `build\dist\windows\`. Extract that ZIP to a separate folder. It includes `run_capture.bat` and `capture_scripts\ep5i400f_7007_native_switches.xml`. Run `run_capture.bat` to start the scripted logger. By default it uses the JDK above; set `ROMRAIDER_JAVA_HOME` to another **32-bit** Java folder to override it.

The logger still needs RomRaider's ECU/logger definitions and a configured log output folder. The plan checks for ECU ID `8424387007` and logs the native `Clutch Switch` and `Neutral Position Switch Signal` channels. It intentionally excludes X943/X944 because the existing definitions bind those probes to a different ECU ID.

This build is read-only during capture. It does not edit or flash the ROM. The existing x64 scripted-logger branch is unchanged.
