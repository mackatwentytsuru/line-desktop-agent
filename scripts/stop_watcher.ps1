# LINE見張りを一度止める。STOP ファイルを作り、止まるまで待つ。
# STOP を消すまで、タスク「LINE見張り」も再起動しない。再開するときは $env:LOCALAPPDATA\line-desktop-agent\STOP を消す。
$virtual = @(Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Packages\Claude_*\LocalCache\Local\line-desktop-agent') -Directory -ErrorAction SilentlyContinue)[0]
if ($virtual) { $dataDir = $virtual.FullName } else { $dataDir = Join-Path $env:LOCALAPPDATA 'line-desktop-agent' }
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
New-Item -ItemType File -Force -Path (Join-Path $dataDir 'STOP') | Out-Null
for ($i = 0; $i -lt 90; $i++) {
    $r = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match 'line_agent' -and $_.CommandLine -match '\bwatch\b' })
    if ($r.Count -eq 0) { Write-Output '停止しました(STOP は残してあります)'; exit 0 }
    Start-Sleep -Seconds 1
}
Write-Output '90秒待っても止まりません'; exit 1
