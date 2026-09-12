<#
创建 GOAT Gauge 的桌面快捷方式 (可固定到任务栏)。

用法 (普通 PowerShell, 无需管理员):
    powershell -ExecutionPolicy Bypass -File .\tools\make-shortcut.ps1

可选参数:
    -Name       快捷方式显示名, 默认 "GOAT Gauge (Chrome)"
    -NoIcon     不重新生成图标, 直接复用已有 assets\goat-gauge.ico

说明:
  快捷方式的目标是 powershell.exe (隐藏窗口) 而不是 cmd.exe,
  所以固定到任务栏后图标正确, 点击时也不会弹出黑色控制台。
#>
[CmdletBinding()]
param(
    [string]$Name = 'GOAT Gauge (Chrome)',
    [switch]$NoIcon
)

$ErrorActionPreference = 'Stop'

$ProjectDir = Split-Path -Parent $PSScriptRoot
$LaunchPs1  = Join-Path $ProjectDir 'launch.ps1'
$AssetDir   = Join-Path $ProjectDir 'assets'
$IconPath   = Join-Path $AssetDir 'goat-gauge.ico'

if (-not (Test-Path -LiteralPath $LaunchPs1)) {
    throw "找不到启动器: $LaunchPs1"
}

New-Item -ItemType Directory -Force -Path $AssetDir | Out-Null

function New-GoatGaugeIcon {
    param([string]$Path)

    Add-Type -AssemblyName System.Drawing

    $sizes = 16, 20, 24, 32, 40, 48, 64, 128, 256
    $images = New-Object System.Collections.ArrayList

    foreach ($size in $sizes) {
        $bitmap = New-Object System.Drawing.Bitmap($size, $size)
        $g = [System.Drawing.Graphics]::FromImage($bitmap)
        $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $g.Clear([System.Drawing.Color]::Transparent)

        # 紫色圆底 (与界面主色 #7c5cf6 一致)
        $bg = New-Object System.Drawing.SolidBrush(
            [System.Drawing.Color]::FromArgb(255, 124, 92, 246)
        )
        $g.FillEllipse($bg, 0.5, 0.5, $size - 1.5, $size - 1.5)

        # 白色量表弧
        $penWidth = [Math]::Max(1.4, $size * 0.10)
        $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::White, $penWidth)
        $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
        $inset = $size * 0.27
        $g.DrawArc($pen, $inset, $inset, $size - 2 * $inset, $size - 2 * $inset, -90, 285)

        # 指针圆点
        $dot = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
        $dotSize = [Math]::Max(1.5, $size * 0.11)
        $g.FillEllipse($dot, ($size / 2 - $dotSize / 2), ($size * 0.27), $dotSize, $dotSize)

        $g.Dispose()
        $bg.Dispose()
        $pen.Dispose()
        $dot.Dispose()

        $stream = New-Object System.IO.MemoryStream
        $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
        [void]$images.Add($stream.ToArray())
        $stream.Dispose()
        $bitmap.Dispose()
    }

    # 组装 ICO 容器 (PNG 负载, Vista 及以上均支持)
    $file = [System.IO.File]::Create($Path)
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
}

if (-not $NoIcon -or -not (Test-Path -LiteralPath $IconPath)) {
    New-GoatGaugeIcon -Path $IconPath
    Write-Host "图标已生成: $IconPath"
}

$Desktop  = [Environment]::GetFolderPath('Desktop')
$LinkPath = Join-Path $Desktop ($Name + '.lnk')
$PowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($LinkPath)
$link.TargetPath       = $PowerShell
$link.Arguments        = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $LaunchPs1 + '"'
$link.WorkingDirectory = $ProjectDir
$link.IconLocation     = "$IconPath,0"
$link.Description      = 'GOAT Gauge — Command Code GOAT 用量面板'
$link.WindowStyle      = 7
$link.Save()

Write-Host "桌面快捷方式已创建: $LinkPath"
Write-Host ''
Write-Host '固定到任务栏: 右键该快捷方式 -> 固定到任务栏'
Write-Host '(Windows 11 需先点"显示更多选项")'
