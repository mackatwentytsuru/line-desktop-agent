# LINE見張りを起動する。すでに動いていれば何もしない(二重起動しない)。
# STOP ファイルがあれば起動しない(本人/エージェントが意図して止めたとみなす)。
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
# Claude デスクトップアプリ(MSIX)の中で動く作業役は、%LOCALAPPDATA% への書き込みが
# Packages\Claude_*\LocalCache\Local\ に付け替えられる。タスクスケジューラ経由の見張りは付け替えられないので、
# そのままだと別のデータ(別の line.db / STOP / watcher.log)を見てしまう(11-10 実測)。
# 作業役から見える本物の置き場を LINE_AGENT_HOME で明示して、両方を同じ場所にそろえる。
$virtual = @(Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Packages\Claude_*\LocalCache\Local\line-desktop-agent') -Directory -ErrorAction SilentlyContinue)[0]
if ($virtual) { $dataDir = $virtual.FullName } else { $dataDir = Join-Path $env:LOCALAPPDATA 'line-desktop-agent' }
$log = Join-Path $dataDir 'autostart.log'
$stop = Join-Path $dataDir 'STOP'
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

function Write-Log($msg) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'), $msg
    Add-Content -Path $log -Value $line -Encoding UTF8
}

try {
    $running = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match 'line_agent' -and $_.CommandLine -match '\bwatch\b' })
    if ($running.Count -gt 0) {
        # 10分ごとに呼ばれるので、動いているときは記録しない(ログが膨らむため)
        exit 0
    }
    if (Test-Path $stop) {
        Write-Log 'STOP file present, not starting'
        exit 0
    }
    # pythonw.exe は stdout が無く、cli.py の sys.stdout.reconfigure で落ちる(11-10 実測)。python.exe を非表示で起動する。
    $py = Join-Path $repo '.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) {
        Write-Log "ERROR venv python not found: $py"
        exit 1
    }
    $env:PYTHONPATH = 'src'
    $env:LINE_AGENT_HOME = $dataDir
    $args = @('-m', 'line_agent', 'watch', '--interval', '30')
    $p = Start-Process -FilePath $py -ArgumentList $args -WorkingDirectory $repo -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 5
    if ($p.HasExited) { Write-Log "ERROR watcher exited right away code=$($p.ExitCode)"; exit 1 }
    Write-Log "started python pid=$($p.Id)"
} catch {
    Write-Log "ERROR $($_.Exception.Message)"
    exit 1
}
