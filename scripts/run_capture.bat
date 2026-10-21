@echo off
setlocal

set "APP_DIR=%~dp0"
set "JAVA_HOME=%ProgramFiles(x86)%\Java\jdk1.6.0_45"
if defined ROMRAIDER_JAVA_HOME set "JAVA_HOME=%ROMRAIDER_JAVA_HOME%"
set "JAVA=%JAVA_HOME%\bin\java.exe"
set "JAR=%APP_DIR%RomRaider.jar"
set "CAPTURE=%APP_DIR%capture_scripts\ep5i400f_7007_native_switches.xml"
set "LOG_DIR=%USERPROFILE%\.RomRaider"
set "LOG_FILE=%LOG_DIR%\romraider_sout.log"

if not exist "%JAVA%" (
    echo 32-bit Java not found: "%JAVA%"
    echo Set ROMRAIDER_JAVA_HOME to a 32-bit JDK or JRE folder.
    pause
    exit /b 1
)
if not exist "%JAR%" (
    echo RomRaider.jar not found: "%JAR%"
    pause
    exit /b 1
)
if not exist "%CAPTURE%" (
    echo Capture plan not found: "%CAPTURE%"
    pause
    exit /b 1
)
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

start "RomRaider scripted logger" /D "%APP_DIR%" /MIN "%JAVA%" -Djava.library.path=lib/windows/32 -Dawt.useSystemAAFontSettings=lcd -Dswing.aatext=true -Dsun.java2d.d3d=true -Xms64M -Xmx512M -jar "%JAR%" -logger --capture-script="%CAPTURE%" >>"%LOG_FILE%" 2>&1

endlocal
