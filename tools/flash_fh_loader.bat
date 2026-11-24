@echo off
setlocal
REM ==========================================================================
REM  R200 OpenWrt flash helper -- calls fh_loader.exe directly.
REM
REM  Why not the QFIL GUI: QFIL starts fh_loader with --setactivepartition=0,
REM  which makes the tool send <setbootablestoragedrive value="0"/>.
REM  The sdx55 NAND programmer answers "ERROR: Set Bootable Failed 1" and QFIL
REM  aborts with "Download Fail:FireHose Fail:FHLoader Fail:Process fail".
REM  That command is an eMMC/UFS concept; here we simply do not send it.
REM
REM  Usage:  flash_fh_loader.bat [com-number]      (default 10 -> COM10)
REM
REM  Precondition: the device must ALREADY be in Firehose mode, i.e. the
REM  programmer is loaded. Easiest way: press Download once in QFIL, let it
REM  fail on Set Bootable, do NOT unplug, then run this script.
REM ==========================================================================

set "DIR=%~dp0"
set "PORT=%~1"
if "%PORT%"=="" set "PORT=10"

set "FH=C:\Program Files (x86)\Qualcomm\QPST\bin\fh_loader.exe"
if not exist "%FH%" set "FH=C:\Program Files\Qualcomm\QPST\bin\fh_loader.exe"
if not exist "%FH%" set "FH=C:\Program Files (x86)\Qualcomm\QPST\fh_loader.exe"
if not exist "%FH%" (
  echo [X] fh_loader.exe not found. Install QPST/QFIL, or edit the FH path in this file.
  exit /b 2
)

if not exist "%DIR%prog_firehose_sdx55.mbn" (
  echo [X] missing prog_firehose_sdx55.mbn in this folder
  exit /b 2
)
if not exist "%DIR%system.ubi" (
  echo [X] missing system.ubi in this folder
  exit /b 2
)
if not exist "%DIR%rawprogram_r200_openwrt.xml" (
  echo [X] missing rawprogram_r200_openwrt.xml in this folder
  exit /b 2
)

echo [*] fh_loader : %FH%
echo [*] COM port  : COM%PORT%
echo [*] folder    : %DIR%
echo.
echo [i] Device must be in Firehose already. If unsure, run QFIL Download once,
echo     wait for the Set Bootable error, and keep the cable connected.
echo.
pause

"%FH%" --port=\\.\COM%PORT% --search_path="%DIR%" --sendxml=rawprogram_r200_openwrt.xml --noprompt --showpercentagecomplete --zlpawarehost=1 --memoryname=nand --verbose=2
set "RC=%ERRORLEVEL%"

echo.
echo [*] fh_loader exit code = %RC%
if "%RC%"=="0" (
  echo [OK] flash finished. Power-cycle the device, then open http://192.168.1.1
) else (
  echo [X] flash failed - read the messages above.
  echo     If it says the port is busy, close QFIL first and re-enter EDL.
)
endlocal
