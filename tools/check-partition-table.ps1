# check-partition-table.ps1 - verify the vendor rawprogram partition table
# against what this project was built for. No Python needed.
# Kept ASCII-only on purpose: Windows PowerShell 5.1 reads .ps1 as ANSI,
# so non-ASCII text here would be mojibake unless the file carried a BOM.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\check-partition-table.ps1 -Fw C:\R200_6.00.8\Maincode
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\check-partition-table.ps1 -Fw <fw dir> -Diff
#
# Pass criteria (all three match => the CI artifact can be flashed as-is):
#   system start_sector = 64832, system num_partition_sectors = 66240, SECTOR_SIZE = 4096
param(
    [Parameter(Mandatory = $true)][Alias("Fw","FwDir")][string]$FirmwareDir,
    [switch]$Diff
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$mine = Join-Path $here 'firmware\rawprogram_nand_p4K_b256K.xml'

$xmlPath = Join-Path $FirmwareDir 'rawprogram_nand_p4K_b256K.xml'
if (-not (Test-Path -LiteralPath $xmlPath)) {
    Write-Host "[X] not found: $xmlPath" -ForegroundColor Red
    exit 2
}

function Get-Parts {
    param([string]$path)
    [xml]$x = Get-Content -LiteralPath $path -Encoding UTF8
    $list = @()
    foreach ($n in $x.data.program) {
        $obj = New-Object psobject
        $obj | Add-Member NoteProperty Label ([string]$n.label)
        $obj | Add-Member NoteProperty Start ([int]$n.start_sector)
        $obj | Add-Member NoteProperty Sectors ([int]$n.num_partition_sectors)
        $obj | Add-Member NoteProperty Sector ([int]$n.SECTOR_SIZE_IN_BYTES)
        $obj | Add-Member NoteProperty File ([string]$n.filename)
        $list += $obj
    }
    return $list
}

$partList = @(Get-Parts $xmlPath)
Write-Host ""
Write-Host "=== $xmlPath ===" -ForegroundColor Cyan
Write-Host ("partitions: {0}" -f $partList.Count)

$endMax = 0
foreach ($p in $partList) {
    $end = $p.Start + $p.Sectors
    if ($end -gt $endMax) { $endMax = $end }
}
Write-Host ("covered sectors: {0}  ({1:N1} MiB)" -f $endMax, ($endMax * 4096 / 1048576))

$sys = $null
foreach ($p in $partList) {
    if ($p.Label -eq 'system' -and $null -eq $sys) { $sys = $p }
}
if ($null -eq $sys) {
    Write-Host "[X] no 'system' partition - this does not look like R200 rawprogram" -ForegroundColor Red
    exit 3
}

$ok = $true
Write-Host ""
Write-Host "=== system partition check (expected values measured on 6.00.7) ===" -ForegroundColor Cyan
if ($sys.Start -eq 64832) {
    Write-Host ("  [OK] Start   = {0}" -f $sys.Start) -ForegroundColor Green
} else {
    Write-Host ("  [!!] Start   = {0}  (expected 64832)" -f $sys.Start) -ForegroundColor Red
    $ok = $false
}
if ($sys.Sectors -eq 66240) {
    Write-Host ("  [OK] Sectors = {0}" -f $sys.Sectors) -ForegroundColor Green
} else {
    Write-Host ("  [!!] Sectors = {0}  (expected 66240)" -f $sys.Sectors) -ForegroundColor Red
    $ok = $false
}
if ($sys.Sector -eq 4096) {
    Write-Host ("  [OK] Sector  = {0}" -f $sys.Sector) -ForegroundColor Green
} else {
    Write-Host ("  [!!] Sector  = {0}  (expected 4096)" -f $sys.Sector) -ForegroundColor Red
    $ok = $false
}
$sizeMiB = $sys.Sectors * $sys.Sector / 1048576
$offHex = '{0:X8}' -f ($sys.Start * $sys.Sector)
Write-Host ("  system size = {0:N2} MiB, offset = 0x{1}" -f $sizeMiB, $offHex)

if ($Diff) {
    if (Test-Path -LiteralPath $mine) {
        Write-Host ""
        Write-Host "=== line diff against the 6.00.7 baseline ===" -ForegroundColor Cyan
        $a = Get-Content -LiteralPath $xmlPath -Encoding UTF8
        $b = Get-Content -LiteralPath $mine -Encoding UTF8
        $d = Compare-Object -ReferenceObject $b -DifferenceObject $a
        if ($null -eq $d -or $d.Count -eq 0) {
            Write-Host "  [OK] identical" -ForegroundColor Green
        } else {
            Write-Host ("  [!!] {0} differing lines (left=baseline 6.00.7, right=yours):" -f $d.Count) -ForegroundColor Yellow
            $i = 0
            foreach ($line in $d) {
                if ($i -ge 40) { Write-Host "   ..."; break }
                $side = '?'
                if ($line.SideIndicator -eq '<=') { $side = '6.00.7' }
                if ($line.SideIndicator -eq '=>') { $side = 'yours ' }
                Write-Host ("   [{0}] {1}" -f $side, $line.InputObject.Trim())
                $i = $i + 1
            }
        }
    } else {
        Write-Host "[!] baseline file missing: $mine" -ForegroundColor Yellow
    }
}

Write-Host ""
if ($ok) {
    Write-Host "[PASS] partition table matches: the CI system.ubi can be flashed directly" -ForegroundColor Green
    exit 0
}
Write-Host "[FAIL] partition table differs: do not flash, send this output back" -ForegroundColor Red
exit 1

