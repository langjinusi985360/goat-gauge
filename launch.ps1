<#
GOAT Gauge 启动器

桌面快捷方式 / 任务栏固定项都指向这个脚本, 它做两件事:
  1. 本地服务没跑 -> 用 pythonw 静默拉起 (不弹控制台)
  2. 用专用 Chrome 配置打开仪表盘应用窗口

因为宿主进程是隐藏窗口的 PowerShell 而不是 cmd.exe, 固定到任务栏后
图标与点击行为都是正常的, 也不会出现黑框闪一下。
#>
[CmdletBinding()]
param(
    # 只保证本地服务在跑, 不开窗口 (用于开机自启)
    [switch]$ServerOnly
)

$ErrorActionPreference = 'SilentlyContinue'

Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue

$ProjectDir = $PSScriptRoot
$Port       = 18927
$DevTools   = 9333
$DataDir    = Join-Path $env:LOCALAPPDATA 'GOATGauge'
$ProfileDir = Join-Path $DataDir 'chrome-profile'
$EntryPy    = Join-Path $ProjectDir 'entry.py'
$PwaAppId   = 'bbdcaiplkiaanemdcpjjbnndfalddhag'

$ChromeCandidates = @(
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
)
$Chrome = $ChromeCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $Chrome) {
    [System.Windows.Forms.MessageBox]::Show('找不到 Google Chrome。', 'GOAT Gauge') | Out-Null
    exit 1
}
$ChromeProxy = Join-Path (Split-Path -Parent $Chrome) 'chrome_proxy.exe'
$PwaMarker = Join-Path $ProfileDir "Default\Web Applications\_crx_$PwaAppId\GOAT Gauge.ico"

function Get-GaugeState {
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/state" -TimeoutSec 3
    } catch {
        return $null
    }
}

function Test-GaugeServer {
    return $null -ne (Get-GaugeState)
}

function Resolve-Pythonw {
    $fromPath = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if ($fromPath -and (Test-Path -LiteralPath $fromPath)) { return $fromPath }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\pythonw.exe'),
        'C:\Python312\pythonw.exe',
        'C:\Python311\pythonw.exe'
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

function Start-GaugeServer {
    Start-Process -FilePath $script:pythonw `
        -ArgumentList @(
            "`"$script:EntryPy`"",
            '--chrome',
            '--no-browser',
            '--port',
            "$script:Port"
        ) `
        -WorkingDirectory $script:ProjectDir `
        -WindowStyle Hidden

    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-GaugeServer) { return $true }
    }
    return $false
}

function Stop-GaugeServer {
    try {
        Invoke-RestMethod `
            -Method Post `
            -Uri "http://127.0.0.1:$script:Port/api/quit" `
            -TimeoutSec 3 | Out-Null
    } catch {
        return
    }

    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 250
        if (-not (Test-GaugeServer)) { return }
    }
}

function Open-GaugeWindow {
    if ((Test-Path -LiteralPath $ChromeProxy) -and (Test-Path -LiteralPath $PwaMarker)) {
        Start-Process -FilePath $ChromeProxy -ArgumentList @(
            "--remote-debugging-port=$DevTools",
            '--remote-allow-origins=*',
            "--user-data-dir=$ProfileDir",
            '--profile-directory=Default',
            "--app-id=$PwaAppId"
        )
        return
    }

    Start-Process -FilePath $Chrome -ArgumentList @(
        "--remote-debugging-port=$DevTools",
        '--remote-allow-origins=*',
        "--user-data-dir=$ProfileDir",
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-features=Translate',
        "--app=http://127.0.0.1:$Port/",
        '--window-size=1280,860'
    )
}

$pythonw = Resolve-Pythonw
if (-not $pythonw) {
    [System.Windows.Forms.MessageBox]::Show(
        '找不到 pythonw.exe，请先安装 Python 3。',
        'GOAT Gauge'
    ) | Out-Null
    exit 1
}

if ($ServerOnly) {
    if (-not (Test-GaugeServer)) {
        Start-GaugeServer | Out-Null
    }
    exit 0
}

if (-not (Test-GaugeServer)) {
    Start-GaugeServer | Out-Null
}

# 先打开 PWA 让专用 Chrome 暴露 CDP, Cookie Watcher 会立即恢复会话
Open-GaugeWindow

$state = Get-GaugeState
if ($state -and $state.configured) {
    exit 0
}

# 等登录态恢复; 网络波动或 Cookie 刷新通常只需几秒
for ($i = 0; $i -lt 24; $i++) {
    Start-Sleep -Milliseconds 500
    $state = Get-GaugeState
    if ($state -and $state.configured) {
        exit 0
    }
}

# 仍未恢复到可用登录态 -> 自动重启后端一次, 清除卡死的旧状态
Stop-GaugeServer
if (Start-GaugeServer) {
    Open-GaugeWindow
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        $state = Get-GaugeState
        if ($state -and $state.configured) {
            exit 0
        }
    }
}
