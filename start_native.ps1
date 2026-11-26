param([switch]$DryRun, [switch]$GuardCheck)

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

function Test-StaleRunner([bool]$HasPython, [datetime]$Created, $LastTime, [string]$LastText, [datetime]$Now, [bool]$KakaoUp) {
    if ($HasPython) { return @{ Stale = $false; Reason = 'python 자식 있음' } }
    if ($LastTime -and $LastTime -lt $Created.AddSeconds(-1)) { $LastTime = $null; $LastText = '' }
    if (-not $LastTime) {
        $idle = [int]($Now - $Created).TotalSeconds
        return @{ Stale = ($idle -gt 300); Reason = "python 자식 없음, 이 PID 로그 없음, 뜬 지 $($idle)초(기준 300)" }
    }
    $idle = [int]($Now - [datetime]$LastTime).TotalSeconds
    if ($LastText -match '카톡이 안 떠 있음' -and -not $KakaoUp) { return @{ Stale = $false; Reason = "PC 카톡 기다리는 중($($idle)초)" } }
    $limit = 300
    if ($LastText -match '5초 뒤 다시') { $limit = 120 }
    elseif ($LastText -match '5분 쉼') { $limit = 420 }
    return @{ Stale = ($idle -gt $limit); Reason = "python 자식 없음, 마지막 로그 '$($LastText.Trim())' 뒤 $($idle)초(기준 $limit)" }
}

function Get-LastRunnerLog([int]$RunnerPid) {
    try { $lines = [IO.File]::ReadAllLines($LogFile, $Utf8Bom) } catch { return $null }
    $pat = '^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[' + $RunnerPid + '\] (.*)$'
    for ($i = $lines.Count - 1; $i -ge 0; $i--) {
        if ($lines[$i] -match $pat) { return @{ Time = [datetime]::ParseExact($Matches[1], 'yyyy-MM-dd HH:mm:ss', $null); Text = $Matches[2] } }
    }
    return $null
}

function Get-OtherRunners {
    try { $all = @(Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, Name, CommandLine, CreationDate -ErrorAction Stop) } catch { return @() }
    $kakaoUp = [bool](Get-Process -Name KakaoTalk -ErrorAction SilentlyContinue)
    $now = Get-Date
    $out = @()
    foreach ($p in $all) {
        if ($p.ProcessId -eq $PID -or $p.Name -notmatch '^(powershell|pwsh)\.exe$' -or [string]$p.CommandLine -notmatch '(?i)start_native\.ps1') { continue }
        $ids = @([int]$p.ProcessId); $py = $false
        for ($i = 0; $i -lt $ids.Count; $i++) {
            foreach ($c in $all) {
                if ($c.ParentProcessId -eq $ids[$i] -and $ids -notcontains [int]$c.ProcessId -and $c.CreationDate -ge $p.CreationDate) {
                    $ids += [int]$c.ProcessId
                    if ($c.Name -match '^(python|pythonw|py)\.exe$') { $py = $true }
                }
            }
        }
        $last = Get-LastRunnerLog $p.ProcessId
        $lt = $null; $tx = ''
        if ($last) { $lt = $last.Time; $tx = $last.Text }
        $v = Test-StaleRunner $py $p.CreationDate $lt $tx $now $kakaoUp
        $out += [pscustomobject]@{ Pid = [int]$p.ProcessId; Created = $p.CreationDate; Stale = $v.Stale; Reason = $v.Reason }
    }
    return $out
}

function Stop-StaleRunner($Runner) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($Runner.Pid)" -ErrorAction SilentlyContinue
    if (-not $p -or $p.Name -notmatch '^(powershell|pwsh)\.exe$' -or [string]$p.CommandLine -notmatch '(?i)start_native\.ps1' -or $p.CreationDate -ne $Runner.Created) { return $false }
    try { Stop-Process -Id $Runner.Pid -Force -ErrorAction Stop; return $true } catch { return $false }
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
    if ($GuardCheck) {
        foreach ($r in @(Get-OtherRunners)) { Write-Host ("GUARDCHECK pid {0} stale={1} {2}" -f $r.Pid, $r.Stale, $r.Reason) }
        exit 0
    }
    if (-not $DryRun -and -not (Enter-Mutex)) {
        $runners = @(Get-OtherRunners)
        $stale = @($runners | Where-Object { $_.Stale })
        if (-not $stale.Count -or $stale.Count -ne $runners.Count) {
            $why = ($runners | ForEach-Object { "pid $($_.Pid): $($_.Reason)" }) -join '; '
            Write-Log 'guard' "다른 start_native.ps1 실행 중($why) - 끝냄"; exit 0
        }
        foreach ($r in $stale) {
            $ok = Stop-StaleRunner $r
            Write-Log 'guard' "낡은 start_native.ps1 pid $($r.Pid) $(if ($ok) { '끝냄' } else { '끝내기 실패' }): $($r.Reason) - 새로 시작"
        }
        Start-Sleep -Seconds 2
        if (-not (Enter-Mutex)) { Write-Log 'guard' '낡은 실행을 끝냈지만 뮤텍스를 못 얻음 - 끝냄'; exit 0 }
    }
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
