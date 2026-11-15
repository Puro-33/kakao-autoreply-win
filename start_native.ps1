# 카톡 AI 봇 실행: iris_native 셔임 + bot.py 를 띄우고 죽으면 다시 띄운다.
# 자동 시작을 원하면 작업 스케줄러에 'KakaoBot'(로그온 때)·'KakaoBotWatchdog'(주기적)으로 숨김 창 실행을 등록한다(README 참고).
# Python: 기본은 py 런처의 3.12. 다른 해석기를 쓰려면 환경 변수 KAKAO_BOT_PYTHON 에 python.exe 경로를 넣는다.
#   자동 시작 끄기: Disable-ScheduledTask KakaoBot; Disable-ScheduledTask KakaoBotWatchdog
# 하는 일
#   1) 중복 방지: 다른 start_native.ps1(뮤텍스)이나 start_native.py / bot.py 가 돌고 있으면 조용히 끝낸다
#      (터미널에서 손으로 띄운 봇이 있어도 그대로 둔다)
#   2) PC 카카오톡(KakaoTalk.exe)이 떠 있을 때까지 기다린다(전송이 카톡 창을 쓰므로). 안 떠 있으면 한 번 실행해 본다
#   3) start_native.py(셔임+bot.py)를 띄우고, 죽으면 5초 뒤 다시. 10분 안에 5번 넘게 죽으면 5분 쉼
#   꺼져 있던(절전·종료) 동안 온 메시지는 bot.py가 저장된 catchup_cursor 뒤부터 이어서 답한다.
#   단계마다 logs\start.log에 한 줄. 셔임 출력은 logs\native.log.
param([switch]$DryRun)

$RetrySec  = 30
$MutexName = 'Local\KakaoBot.start_native.ps1'
$Root      = $PSScriptRoot
$LogDir    = Join-Path $Root 'logs'
$LogFile   = Join-Path $LogDir 'start.log'
$NativeLog = Join-Path $LogDir 'native.log'
$Python    = if ($env:KAKAO_BOT_PYTHON) { $env:KAKAO_BOT_PYTHON } else { (Get-Command py -ErrorAction SilentlyContinue).Source }   # py 런처(-3.12)
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
    # 이 스크립트가 아닌 곳에서 돌고 있는 봇(start_native.py 또는 bot.py)
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
    Start-Sleep -Seconds 10   # 로그인·DB 열 시간
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
                # cmd로 넘겨 바이트 그대로 붙인다(PS 5.1의 *>>는 UTF-16으로 써서 한글이 깨졌다)
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
