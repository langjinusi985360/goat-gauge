<#
生成 GOAT Gauge 的应用图标:
  assets\goat-gauge.ico          给桌面快捷方式用
  app\web\icon-192.png           PWA 图标
  app\web\icon-512.png           PWA 图标
  app\web\icon-maskable-512.png  PWA 自适应图标

用法:
    powershell -ExecutionPolicy Bypass -File .\tools\make-icons.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$ProjectDir = Split-Path -Parent $PSScriptRoot
$AssetDir   = Join-Path $ProjectDir 'assets'
$WebDir     = Join-Path $ProjectDir 'app\web'
$IcoPath    = Join-Path $AssetDir 'goat-gauge.ico'

Add-Type -AssemblyName System.Drawing

$Accent = [System.Drawing.Color]::FromArgb(255, 124, 92, 246)

function New-GaugeBitmap {
    param(
        [int]$Size,
        [double]$Inset = 0.0,
        [System.Drawing.Color]$Background = [System.Drawing.Color]::Transparent
    )

    $bitmap = New-Object System.Drawing.Bitmap($Size, $Size)
    $g = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $g.Clear($Background)

        $pad = $Size * $Inset
        $diameter = $Size - 2 * $pad

        $bg = New-Object System.Drawing.SolidBrush($Accent)
        $g.FillEllipse($bg, $pad, $pad, $diameter, $diameter)

        $penWidth = [Math]::Max(1.4, $diameter * 0.10)
        $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::White, $penWidth)
        $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
        $arcInset = $diameter * 0.27
        $g.DrawArc(
            $pen,
            $pad + $arcInset,
            $pad + $arcInset,
            $diameter - 2 * $arcInset,
            $diameter - 2 * $arcInset,
            -90,
            285
        )

        $dotSize = [Math]::Max(1.5, $diameter * 0.11)
        $dot = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
        $g.FillEllipse(
            $dot,
            ($pad + $diameter / 2 - $dotSize / 2),
            ($pad + $diameter * 0.27),
            $dotSize,
            $dotSize
        )

        $bg.Dispose()
        $pen.Dispose()
        $dot.Dispose()
    }
    finally {
        $g.Dispose()
    }
    return $bitmap
}

New-Item -ItemType Directory -Force -Path $AssetDir, $WebDir | Out-Null

# ---------- PNG (PWA) ----------
foreach ($spec in @(
    @{ Path = (Join-Path $WebDir 'icon-192.png'); Size = 192; Inset = 0.0 },
    @{ Path = (Join-Path $WebDir 'icon-512.png'); Size = 512; Inset = 0.0 },
    @{ Path = (Join-Path $WebDir 'icon-maskable-512.png'); Size = 512; Inset = 0.12 }
)) {
    $bitmap = New-GaugeBitmap -Size $spec.Size -Inset $spec.Inset
    try {
        $bitmap.Save($spec.Path, [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $bitmap.Dispose()
    }
    Write-Host "图标已生成: $($spec.Path)"
}

# ---------- ICO (桌面快捷方式) ----------
$sizes = 16, 20, 24, 32, 40, 48, 64, 128, 256
$images = New-Object System.Collections.ArrayList

foreach ($size in $sizes) {
    $bitmap = New-GaugeBitmap -Size $size
    try {
        $stream = New-Object System.IO.MemoryStream
        $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
        [void]$images.Add($stream.ToArray())
        $stream.Dispose()
    }
    finally {
        $bitmap.Dispose()
    }
}

$file = [System.IO.File]::Create($IcoPath)
$writer = New-Object System.IO.BinaryWriter($file)
try {
    $writer.Write([UInt16]0)
    $writer.Write([UInt16]1)
    $writer.Write([UInt16]$sizes.Count)

    $offset = 6 + 16 * $sizes.Count
    for ($i = 0; $i -lt $sizes.Count; $i++) {
        $size = $sizes[$i]
        $data = $images[$i]
        $dimension = 0
        if ($size -lt 256) { $dimension = $size }

        $writer.Write([Byte]$dimension)
        $writer.Write([Byte]$dimension)
        $writer.Write([Byte]0)
        $writer.Write([Byte]0)
        $writer.Write([UInt16]1)
        $writer.Write([UInt16]32)
        $writer.Write([UInt32]$data.Length)
        $writer.Write([UInt32]$offset)
        $offset += $data.Length
    }

    foreach ($data in $images) { $writer.Write($data) }
}
finally {
    $writer.Dispose()
    $file.Dispose()
}

Write-Host "图标已生成: $IcoPath"
