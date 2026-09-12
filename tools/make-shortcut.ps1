<#
创建 GOAT Gauge 的桌面快捷方式 (可固定到任务栏)。

用法 (普通 PowerShell, 无需管理员):
    powershell -ExecutionPolicy Bypass -File .\tools\make-shortcut.ps1

可选参数:
    -Name         快捷方式显示名, 默认 "GOAT Gauge (Chrome)"
    -SkipIcons    不再生成图标, 直接复用已有 assets\goat-gauge.ico

说明:
  快捷方式的目标是隐藏窗口的 powershell.exe 而不是 cmd.exe,
  所以固定到任务栏后图标正确, 点击时也不会闪出黑色控制台。
#>
[CmdletBinding()]
param(
    [string]$Name = 'GOAT Gauge (Chrome)',
    [switch]$SkipIcons,
    # 额外注册开机自启: 登录后静默启动本地服务 (不开窗口)
    [switch]$Startup
)

$ErrorActionPreference = 'Stop'

$ProjectDir = Split-Path -Parent $PSScriptRoot
$LaunchPs1  = Join-Path $ProjectDir 'launch.ps1'
$IconPath   = Join-Path $ProjectDir 'assets\goat-gauge.ico'
$MakeIcons  = Join-Path $PSScriptRoot 'make-icons.ps1'

if (-not (Test-Path -LiteralPath $LaunchPs1)) {
    throw "找不到启动器: $LaunchPs1"
}

if (-not $SkipIcons -or -not (Test-Path -LiteralPath $IconPath)) {
    & $MakeIcons
}

$Desktop    = [Environment]::GetFolderPath('Desktop')
$LinkPath   = Join-Path $Desktop ($Name + '.lnk')
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
Write-Host ''
Write-Host '提示: 如果想固定后带独立的应用图标与窗口归属, 走 PWA 安装:'
Write-Host '  面板里打开"设置" -> "安装为桌面应用"'

if ($Startup) {
    $StartupDir  = [Environment]::GetFolderPath('Startup')
    $StartupLink = Join-Path $StartupDir 'GOAT Gauge Server.lnk'

    $startupShortcut = $shell.CreateShortcut($StartupLink)
    $startupShortcut.TargetPath       = $PowerShell
    $startupShortcut.Arguments        = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $LaunchPs1 + '" -ServerOnly'
    $startupShortcut.WorkingDirectory = $ProjectDir
    $startupShortcut.IconLocation     = "$IconPath,0"
    $startupShortcut.Description      = 'GOAT Gauge 本地服务 (登录后静默启动)'
    $startupShortcut.WindowStyle      = 7
    $startupShortcut.Save()

    Write-Host "已注册开机自启: $StartupLink"
}
