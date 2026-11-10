# LINE 拡張を入れた専用 Chrome を起動する(9233 番が答えていなければ)。
# - すでに 9233 番が答えるなら何もしない(2 つ目は起動しない)。
# - 同じプロフィールの Chrome が動いているのに 9233 番が答えない場合も、起動せずに止める(プロフィールは 1 つの Chrome しか使えない)。
# - デバッグの待ち受けは既定の 127.0.0.1 のまま。アドレスを広げる指定は付けない(外部からは使えない)。
# - Chrome を名前で止めない。止めるときは PID を指定する。
$ErrorActionPreference = 'Stop'
$Port = 9233
# プロフィールの置き場: 環境変数 LINE_AGENT_CHROME_PROFILE > データ置き場の config.json の "chrome_profile" > 既定。
$virtual = @(Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Packages\Claude_*\LocalCache\Local\line-desktop-agent') -Directory -ErrorAction SilentlyContinue)[0]
if ($env:LINE_AGENT_HOME) { $dataDir = $env:LINE_AGENT_HOME } elseif ($virtual) { $dataDir = $virtual.FullName } else { $dataDir = Join-Path $env:LOCALAPPDATA 'line-desktop-agent' }
$Profile = $env:LINE_AGENT_CHROME_PROFILE
if (-not $Profile) {
    $cfgPath = Join-Path $dataDir 'config.json'
    if (Test-Path $cfgPath) { try { $Profile = (Get-Content -Raw -Encoding UTF8 $cfgPath | ConvertFrom-Json).chrome_profile } catch { } }
}
if (-not $Profile) { $Profile = Join-Path $env:LOCALAPPDATA 'line-desktop-agent\chrome-profile' }

function Test-Port {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri "http://127.0.0.1:$Port/json/version" | Out-Null; return $true } catch { return $false }
}

if (Test-Port) { Write-Output "already running: port $Port answers"; exit 0 }

$running = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
    Where-Object { $_.CommandLine -like "*$Profile*" }
if ($running) {
    Write-Output "NOT started: a Chrome with this profile is running (PID $($running[0].ProcessId)) but port $Port does not answer. Close it by PID first."
    exit 2
}

$candidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) { Write-Output "NOT started: chrome.exe not found"; exit 3 }

Start-Process -FilePath $chrome -WindowStyle Minimized -ArgumentList @("--user-data-dir=$Profile", "--remote-debugging-port=$Port")
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Port) { Write-Output "started: port $Port answers"; exit 0 }
}
Write-Output "started but port $Port does not answer yet"
exit 4
