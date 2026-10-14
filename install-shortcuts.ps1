# Creates Desktop + Start Menu shortcuts for My Working Hub.
# The shortcuts launch electron.exe directly, so no console window appears.
#
#   powershell -ExecutionPolicy Bypass -File install-shortcuts.ps1

$ErrorActionPreference = 'Stop'
$root     = $PSScriptRoot
$electron = Join-Path $root 'node_modules\electron\dist\electron.exe'
$logo     = Join-Path $root 'logo\logo.png'
$icon     = Join-Path $root 'logo\logo.ico'

if (-not (Test-Path $electron)) {
    Write-Host 'Electron not installed yet - running npm install...'
    Push-Location $root
    try { npm install } finally { Pop-Location }
    if (-not (Test-Path $electron)) { throw 'npm install failed - is Node.js installed?' }
}

# Build a 256x256 .ico from logo.png (ICO files can embed a PNG directly).
Add-Type -AssemblyName System.Drawing
$src = [System.Drawing.Image]::FromFile($logo)
$bmp = New-Object System.Drawing.Bitmap 256, 256
$g   = [System.Drawing.Graphics]::FromImage($bmp)
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.DrawImage($src, 0, 0, 256, 256)
$g.Dispose(); $src.Dispose()
$ms = New-Object System.IO.MemoryStream
$bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
$png = $ms.ToArray()

$fs = [System.IO.File]::Create($icon)
$w  = New-Object System.IO.BinaryWriter $fs
$w.Write([UInt16]0); $w.Write([UInt16]1); $w.Write([UInt16]1)   # header: reserved, type=icon, count=1
$w.Write([Byte]0); $w.Write([Byte]0)                             # width/height 0 = 256
$w.Write([Byte]0); $w.Write([Byte]0)                             # palette, reserved
$w.Write([UInt16]1); $w.Write([UInt16]32)                        # planes, bpp
$w.Write([UInt32]$png.Length); $w.Write([UInt32]22)              # size, offset
$w.Write($png)
$w.Close()

$shell   = New-Object -ComObject WScript.Shell
$targets = @(
    [Environment]::GetFolderPath('Desktop'),
    (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs')
)
foreach ($dir in $targets) {
    $lnk = $shell.CreateShortcut((Join-Path $dir 'My Working Hub.lnk'))
    $lnk.TargetPath       = $electron
    $lnk.Arguments        = '.'
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation     = "$icon,0"
    $lnk.Description      = 'My Working Hub'
    $lnk.Save()
    Write-Host "Created $($lnk.FullName)"
}
