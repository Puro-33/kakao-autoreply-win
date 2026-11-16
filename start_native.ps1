param([switch]$DryRun)

$RetrySec  = 30
$MutexName = 'Local\KakaoBot.start_native.ps1'
$Root      = $PSScriptRoot
$LogDir    = Join-Path $Root 'logs'
$LogFile   = Join-Path $LogDir 'start.log'
$NativeLog = Join-Path $LogDir 'native.log'
$Python    = if ($env:KAKAO_BOT_PYTHON) { $env:KAKAO_BOT_PYTHON } else { (Get-Command py -ErrorAction SilentlyContinue).Source }
$PyArgs    = if ($env:KAKAO_BOT_PYTHON) { '' } else { '-3.12' }
$KakaoExe  = @('C:\Program Files (x86)\Kakao\KakaoTalk\KakaoTalk.exe', 'C:\Program Files\Kakao\KakaoTalk\KakaoTalk.exe')
$Utf8Bom   = New-Object System.Text.UTF8Encoding($true)
$RunTag    = if ($DryRun) { 'DRYRUN ' } else { '' }
$script:Mutex = $null

function Write-Log([string]$Phase, [string]$Message) {
    $line = '{0} [{1}] {2}{3,-6} {4}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $PID, $RunTag, $Phase, $Message
    Write-Host $line
    for ($i = 0; $i -lt 3; $i++) {
        try { [void][IO.Directory]::CreateDirectory($LogDir); [IO.File]::AppendAllText($LogFile, $line + "`r`n", $Utf8Bom); return }
        catch { Start-Sleep -Milliseconds 300 }
    }
}

function Limit-Log([string]$Path) {
    try { $fi = New-Object IO.FileInfo($Path); if ($fi.Exists -and $fi.Length -gt 2MB) { Move-Item -LiteralPath $Path -Destination "$Path.1" -Force } } catch { }
}

function Find-RunningBot {
    try { $t = @(Get-CimInstance Win32_Process -Property ProcessId, Name, CommandLine -ErrorAction Stop) } catch { return @() }
    $found = @()
    foreach ($p in $t) {
        if ($p.Name -match '^(python|pythonw|py)\.exe$' -and [string]$p.CommandLine -match '(?i)(start_native\.py|kakao[^\\/]*[\\/]+bot\.py)') {
            $found += "$($p.Name) pid $($p.ProcessId)"
        }
    }
    return $found
}

function Enter-Mutex {
    try { $m = New-Object Threading.Mutex($false, $MutexName) } catch { return $true }
    $got = $false
    try { $got = $m.WaitOne(0) } catch { $got = ($_.Exception.ToString() -match 'AbandonedMutexException') }
    if ($got) { $script:Mutex = $m } else { $m.Dispose() }
    return $got
}

function Wait-KakaoTalk {
    $tried = $false
    while (-not (Get-Process -Name KakaoTalk -ErrorAction SilentlyContinue)) {
        if (-not $tried) {
            $exe = $KakaoExe | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
            if ($exe -and -not $DryRun) {
                Write-Log 'kakao' "PC 카톡이 안 떠 있음 - 실행: $exe"
                try { Start-Process -FilePath $exe -ErrorAction Stop } catch { Write-Log 'kakao' "카톡 실행 실패: $($_.Exception.Message)" }
            } else {
                Write-Log 'kakao' 'PC 카톡이 안 떠 있음 - 켜질 때까지 기다림'
            }
            $tried = $true
        }
        Start-Sleep -Seconds $RetrySec
    }
    Write-Log 'kakao' 'PC 카톡 실행 중'
    Start-Sleep -Seconds 10
}

try {
    Limit-Log $LogFile
    Write-Log 'start' "start_native.ps1 시작(사용자 $env:USERNAME)"
    if (-not $DryRun -and -not (Enter-Mutex)) { Write-Log 'guard' '다른 start_native.ps1 실행 중 - 끝냄'; exit 0 }
    $others = @(Find-RunningBot)
    if ($others.Count) { Write-Log 'guard' "봇이 이미 실행 중($($others -join ', ')) - 끝냄"; exit 0 }
    Wait-KakaoTalk
    if ($DryRun) { Write-Log 'bot' 'DryRun이라 봇은 띄우지 않고 끝냄'; return }

    $deaths = @(); $run = 0
    while ($true) {
        try {
            $others = @(Find-RunningBot)
            if ($others.Count) { Write-Log 'bot' "다른 봇 실행 중($($others -join ', ')) - 1분 뒤 다시 확인"; Start-Sleep -Seconds 60; continue }
            if (-not (Get-Process -Name KakaoTalk -ErrorAction SilentlyContinue)) { Wait-KakaoTalk }
            Limit-Log $NativeLog
            $run++
            Write-Log 'bot' "start_native.py 시작(#$run)"
            $t0 = Get-Date
            Push-Location $Root
            try {
                $env:PYTHONIOENCODING = 'utf-8'
                cmd.exe /c "`"$Python`" $PyArgs -B start_native.py >> `"$NativeLog`" 2>&1"
                $code = $LASTEXITCODE
            } catch { $code = "실행 실패: $($_.Exception.Message)" } finally { Pop-Location }
            $secs = [int]((Get-Date) - $t0).TotalSeconds
            $now = Get-Date
            $deaths = @(@($deaths) + $now | Where-Object { $_ -gt $now.AddMinutes(-10) })
            if ($deaths.Count -gt 5) { Write-Log 'bot' "종료(코드 $code, $($secs)초) - 10분 안에 $($deaths.Count)번이라 5분 쉼"; Start-Sleep -Seconds 300 }
            else { Write-Log 'bot' "종료(코드 $code, $($secs)초) - 5초 뒤 다시"; Start-Sleep -Seconds 5 }
        } catch {
            Write-Log 'error' "반복 중 오류: $($_.Exception.Message) - $($RetrySec)초 뒤 다시"; Start-Sleep -Seconds $RetrySec
        }
    }
} finally {
    if ($script:Mutex) { try { $script:Mutex.ReleaseMutex() } catch { }; $script:Mutex.Dispose() }
}
