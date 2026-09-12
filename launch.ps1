<#
GOAT Gauge 启动器

桌面快捷方式 / 任务栏固定项都指向这个脚本, 它做两件事:
  1. 本地服务没跑 -> 用 pythonw 静默拉起 (不弹控制台)
  2. 用专用 Chrome 配置打开仪表盘应用窗口

因为宿主进程是隐藏窗口的 PowerShell 而不是 cmd.exe, 固定到任务栏后
图标与点击行为都是正常的, 也不会出现黑框闪一下。
#>
$ErrorActionPreference = 'SilentlyContinue'

Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue

$ProjectDir = $PSScriptRoot
$Port       = 18927
$DevTools   = 9333
$DataDir    = Join-Path $env:LOCALAPPDATA 'GOATGauge'
$ProfileDir = Join-Path $DataDir 'chrome-profile'
$EntryPy    = Join-Path $ProjectDir 'entry.py'

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

function Test-GaugeServer {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/api/version" -TimeoutSec 2
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
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

function Open-GaugeWindow {
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

if (Test-GaugeServer) {
    Open-GaugeWindow
    exit 0
}

$pythonw = Resolve-Pythonw
if (-not $pythonw) {
    [System.Windows.Forms.MessageBox]::Show(
        '找不到 pythonw.exe，请先安装 Python 3。',
        'GOAT Gauge'
    ) | Out-Null
    exit 1
}

# entry.py --chrome 会同时启动本地服务并打开 Chrome 应用窗口
Start-Process -FilePath $pythonw `
    -ArgumentList @("`"$EntryPy`"", '--chrome') `
    -WorkingDirectory $ProjectDir `
    -WindowStyle Hidden

# 等本地服务就绪; 若超时也只是退出, entry.py 仍会继续启动
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-GaugeServer) { break }
}
