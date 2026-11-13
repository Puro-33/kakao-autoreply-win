# 카톡 AI 봇 실행
# 전제: LDPlayer(설정에서 ROOT 켬, ADB 디버깅 '로컬 연결 열기') 안의 카톡이 로그인된 상태
# 작업 스케줄러 'KakaoBot'이 로그온 1분 뒤 숨김 창으로 이 파일을 실행한다.
#   자동 시작 끄기: Disable-ScheduledTask KakaoBot   (다시 켜기: Enable-ScheduledTask KakaoBot)
# 하는 일
#   1) 중복 방지: 다른 start.ps1(이름 있는 뮤텍스, 프로세스 목록)이나 kakao-bot\bot.py가 돌고 있으면 조용히 끝낸다
#   2) 에뮬레이터: LDPlayer가 꺼져 있으면 ldconsole launch로 켜고 adb 연결(get-state=device)과
#      부팅 완료(sys.boot_completed=1)를 최대 3분 기다린다. 안 되면 30초 뒤 다시 한다(그냥 끝나지 않는다)
#   3) adb forward/reverse -> 카톡 실행 -> Iris 시작 -> Iris 웹훅 설정(GET /config로 확인)
#   4) bot.py가 죽으면 5초 뒤 다시 띄운다. 10분 안에 5번 넘게 죽으면 5분 쉬었다가 띄운다
#   단계마다 logs\start.log에 한 줄씩 남긴다.
# 시험: powershell -NoProfile -ExecutionPolicy Bypass -File start.ps1 -DryRun
#   bot.py 실행만 빼고 모든 단계를 한다. 다른 인스턴스를 찾으면 기록만 하고 계속한다.
param(
    [switch]$DryRun
)

$BootWaitSec = 180                      # 에뮬레이터 부팅을 기다리는 최대 시간(초)
$RetrySec    = 30                       # 준비가 안 됐을 때 다시 시도하는 간격(초)
$LdIndex     = 0                        # LDPlayer 인스턴스 번호
$MutexName   = 'Local\KakaoBot.start.ps1'
$LogDir      = Join-Path $PSScriptRoot 'logs'
$LogFile     = Join-Path $LogDir 'start.log'
$Utf8Bom     = New-Object System.Text.UTF8Encoding($true)
$RunTag      = if ($DryRun) { 'DRYRUN ' } else { '' }
$script:Mutex = $null

# ───────────────────────── 기록 ─────────────────────────
function Write-Log([string]$Phase, [string]$Message) {
    $line = '{0} [{1}] {2}{3,-6} {4}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $PID, $RunTag, $Phase, $Message
    Write-Host $line
    for ($i = 0; $i -lt 3; $i++) {
        try {
            [void][System.IO.Directory]::CreateDirectory($LogDir)
            [System.IO.File]::AppendAllText($LogFile, $line + "`r`n", $Utf8Bom)
            return
        } catch {
            Start-Sleep -Milliseconds 300
        }
    }
}

function Limit-LogSize {
    # 1MB가 넘으면 start.log.1로 돌린다(이전 start.log.1은 덮어씀)
    try {
        $fi = New-Object System.IO.FileInfo($LogFile)
        if ($fi.Exists -and $fi.Length -gt 1MB) {
            Move-Item -LiteralPath $LogFile -Destination "$LogFile.1" -Force -ErrorAction Stop
        }
    } catch { }
}

function Get-Short([string]$Text, [int]$Max = 160) {
    $t = ([string]$Text -replace '\s+', ' ').Trim()
    if ($t.Length -gt $Max) { $t = $t.Substring(0, $Max) + '...' }
    return $t
}

# ───────────────────────── 외부 프로그램(시간 제한) ─────────────────────────
function Join-ProcessArgs([string[]]$ArgList) {
    ($ArgList | ForEach-Object {
        if ($_ -eq '') { '""' }
        elseif ($_ -notmatch '[\s"]') { $_ }
        else { '"' + (($_ -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"' }
    }) -join ' '
}

function Invoke-Exe([string]$Exe, [string[]]$ArgList, [int]$TimeoutSec = 30) {
    # 창 없이 실행하고 출력을 모은다. 시간 안에 안 끝나면 죽이고 Code -1
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = Join-ProcessArgs $ArgList
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    try {
        $p = [System.Diagnostics.Process]::Start($psi)
    } catch {
        return [pscustomobject]@{ Code = -1; Out = ''; Err = $_.Exception.Message }
    }
    $outTask = $p.StandardOutput.ReadToEndAsync()
    $errTask = $p.StandardError.ReadToEndAsync()
    if (-not $p.WaitForExit($TimeoutSec * 1000)) {
        try { $p.Kill() } catch { }
        return [pscustomobject]@{ Code = -1; Out = ''; Err = "시간 초과($($TimeoutSec)초)" }
    }
    $out = if ($outTask.Wait(3000)) { $outTask.Result.Trim() } else { '' }
    $err = if ($errTask.Wait(3000)) { $errTask.Result.Trim() } else { '' }
    return [pscustomobject]@{ Code = $p.ExitCode; Out = $out; Err = $err }
}

# ───────────────────────── 중복 실행 방지 ─────────────────────────
function Get-ProcessTable {
    try {
        return @(Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, Name, CommandLine, CreationDate -ErrorAction Stop)
    } catch {
        Write-Log 'guard' "프로세스 목록 조회 실패: $(Get-Short $_.Exception.Message)"
        return @()
    }
}

function Get-AncestorIds($Table, [int]$StartId) {
    $byId = @{}
    foreach ($p in $Table) { $byId[[int]$p.ProcessId] = $p }
    $ids = @()
    $cur = $byId[$StartId]
    for ($i = 0; $cur -and $i -lt 64; $i++) {
        $parentId = [int]$cur.ParentProcessId
        $parent = $byId[$parentId]
        if (-not $parent -or $parentId -eq 0 -or $ids -contains $parentId) { break }
        if ($parent.CreationDate -and $cur.CreationDate -and $parent.CreationDate -gt $cur.CreationDate) { break }  # PID 재사용
        $ids += $parentId
        $cur = $parent
    }
    return $ids
}

function Find-OtherInstances([switch]$BotOnly) {
    # 이 프로세스와 그 조상을 뺀 나머지 중 kakao-bot\bot.py를 돌리는 파이썬, (DryRun 아닌) start.ps1 PowerShell
    $table = Get-ProcessTable
    $mine = @($PID) + @(Get-AncestorIds $table $PID)
    $found = @()
    foreach ($p in $table) {
        $id = [int]$p.ProcessId
        if ($mine -contains $id) { continue }
        $cl = [string]$p.CommandLine
        if ($p.Name -match '^(python|pythonw|py|pyw)\.exe$' -and $cl -match '(?i)kakao-bot[\\/]+bot\.py') {
            $found += "bot.py($($p.Name) pid $id)"
        } elseif (-not $BotOnly -and $p.Name -match '^(powershell|pwsh)\.exe$' -and
                  $cl -match '(?i)-f(ile)?\s+"?[^"]*kakao-bot[\\/]+start\.ps1' -and $cl -notmatch '(?i)-dryrun') {
            $found += "start.ps1(pid $id)"
        }
    }
    return $found
}

function Enter-Mutex {
    # $true = 잡았음(또는 뮤텍스를 못 써서 프로세스 목록으로만 판단), $false = 다른 start.ps1이 잡고 있음
    try {
        $m = New-Object System.Threading.Mutex($false, $MutexName)
    } catch {
        Write-Log 'guard' "뮤텍스를 만들 수 없음: $(Get-Short $_.Exception.Message) - 프로세스 목록으로만 확인"
        return $true
    }
    $got = $false
    try { $got = $m.WaitOne(0) } catch { $got = ($_.Exception.ToString() -match 'AbandonedMutexException') }
    if ($got) { $script:Mutex = $m } else { $m.Dispose() }
    return $got
}

function Test-MutexFree {
    # DryRun용: 잡아 보고 바로 놓는다(진짜 실행을 막지 않도록)
    try { $m = New-Object System.Threading.Mutex($false, $MutexName) } catch { return $true }
    $got = $false
    try { $got = $m.WaitOne(0) } catch { $got = ($_.Exception.ToString() -match 'AbandonedMutexException') }
    if ($got) { try { $m.ReleaseMutex() } catch { } }
    $m.Dispose()
    return $got
}

function Invoke-Guard {
    if ($DryRun) {
        $others = @(Find-OtherInstances)
        if (-not (Test-MutexFree)) { $others = @('start.ps1(뮤텍스 잡고 있음)') + $others }
        if ($others.Count) {
            Write-Log 'guard' "다른 인스턴스 감지: $($others -join ', ') -> 실제 실행이면 여기서 조용히 끝냄(DryRun이라 계속)"
        } else {
            Write-Log 'guard' '다른 인스턴스 없음'
        }
        return
    }
    if (-not (Enter-Mutex)) {
        Write-Log 'guard' '다른 start.ps1이 이미 실행 중(뮤텍스) - 조용히 끝냄'
        exit 0
    }
    $others = @(Find-OtherInstances)
    if ($others.Count -and -not ($others -like 'bot.py*')) {
        # start.ps1만 보이면 막 끝나는 중복 실행일 수 있다. 5초 뒤 다시 본다
        Start-Sleep -Seconds 5
        $others = @(Find-OtherInstances)
    }
    if ($others.Count) {
        Write-Log 'guard' "다른 봇이 이미 실행 중: $($others -join ', ') - 조용히 끝냄"
        exit 0
    }
    Write-Log 'guard' '다른 인스턴스 없음 - 계속'
}

# ───────────────────────── 설정 ─────────────────────────
function Read-Config {
    $n = 0
    while ($true) {
        $n++
        try {
            return (Get-Content "$PSScriptRoot\config.json" -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop)
        } catch {
            Write-Log 'config' "config.json 읽기 실패(시도 $n): $(Get-Short $_.Exception.Message) - $($RetrySec)초 뒤 다시"
            Start-Sleep -Seconds $RetrySec
        }
    }
}

function Get-ParentName {
    try {
        $me = Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -Property ParentProcessId -ErrorAction Stop
        $pp = Get-CimInstance Win32_Process -Filter "ProcessId=$($me.ParentProcessId)" -Property Name -ErrorAction Stop
        if ($pp) { return $pp.Name }
    } catch { }
    return '?'
}

# ───────────────────────── 에뮬레이터 ─────────────────────────
function Test-EmulatorReady {
    $st = Invoke-Exe $adb @('-s', $s, 'get-state') 15
    if ($st.Out -ne 'device') { return $false }
    $bc = Invoke-Exe $adb @('-s', $s, 'shell', 'getprop sys.boot_completed') 15
    return ($bc.Out -eq '1')
}

function Get-LdStatus {
    # 'running' / 'stop' / 확인 실패
    $r = Invoke-Exe $ldconsole @('isrunning', '--index', "$LdIndex") 20
    if ($r.Out) { return $r.Out }
    return "확인 실패($(Get-Short $r.Err 80))"
}

function Test-LdAndroidStarted {
    # ldconsole list2 한 줄: 번호,제목,창,창,안드로이드 시작(1/0),pid,vbox pid,...
    $r = Invoke-Exe $ldconsole @('list2') 20
    foreach ($line in ($r.Out -split "`r?`n")) {
        $f = $line.Split(',')
        if ($f.Count -ge 5 -and $f[0].Trim() -eq "$LdIndex") { return ($f[4].Trim() -eq '1') }
    }
    return $false
}

function Start-LdPlayer {
    if (-not (Test-Path -LiteralPath $ldconsole)) {
        Write-Log 'emu' "ldconsole.exe가 없음: $ldconsole"
        return
    }
    try {
        # Start-Process(ShellExecute)로 띄워 이 창의 핸들이 LDPlayer로 넘어가지 않게 한다
        $p = Start-Process -FilePath $ldconsole -ArgumentList @('launch', '--index', "$LdIndex") `
            -WorkingDirectory (Split-Path -Parent $ldconsole) -WindowStyle Hidden -PassThru -ErrorAction Stop
        if (-not $p.WaitForExit(60000)) {
            Write-Log 'emu' 'ldconsole launch가 60초 안에 끝나지 않음 - 그대로 부팅을 기다림'
        } elseif ($p.ExitCode -ne 0) {
            Write-Log 'emu' "ldconsole launch 종료 코드 $($p.ExitCode)"
        }
    } catch {
        Write-Log 'emu' "ldconsole launch 실패: $(Get-Short $_.Exception.Message)"
    }
}

function Send-AdbEmulatorHint {
    # 에뮬레이터가 adb 서버에 자기를 알릴 때 쓰는 요청(host:emulator:<adb 포트>)을 대신 보낸다.
    # adb 서버가 에뮬레이터보다 먼저 떠 있으면 기기를 못 찾는 경우가 있어서다. 이미 연결돼 있으면 아무 일도 안 한다.
    if ($s -notmatch '^emulator-(\d+)$') { return }
    $svc = 'host:emulator:{0}' -f ([int]$Matches[1] + 1)
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect('127.0.0.1', 5037)
        $bytes = [System.Text.Encoding]::ASCII.GetBytes(('{0:x4}{1}' -f $svc.Length, $svc))
        $client.GetStream().Write($bytes, 0, $bytes.Length)
        Start-Sleep -Milliseconds 300
    } catch {
    } finally {
        if ($client) { $client.Close() }
    }
}

function Wait-Emulator {
    # 'ready' = 이미 준비돼 있었음, 'booted' = 이번에 켜거나 부팅을 기다려서 준비됨, 'timeout' = 시간 안에 안 됨
    [void](Invoke-Exe $adb @('start-server') 30)
    if (Test-EmulatorReady) { return 'ready' }

    $ld = Get-LdStatus
    if ($ld -eq 'running') {
        Write-Log 'emu' "LDPlayer는 켜져 있음 - adb 연결과 부팅 완료를 최대 $($BootWaitSec)초 기다림"
    } else {
        Write-Log 'emu' "LDPlayer 꺼짐(isrunning=$ld) - ldconsole launch --index $LdIndex, 부팅을 최대 $($BootWaitSec)초 기다림"
        Start-LdPlayer
    }

    $t0 = Get-Date
    $androidAt = $null
    $adbRestarted = $false
    while (((Get-Date) - $t0).TotalSeconds -lt $BootWaitSec) {
        Start-Sleep -Seconds 5
        if (Test-EmulatorReady) {
            Write-Log 'emu' ("에뮬레이터 준비됨({0} device, boot_completed=1, {1}초 걸림)" -f $s, [int]((Get-Date) - $t0).TotalSeconds)
            return 'booted'
        }
        Send-AdbEmulatorHint
        # 안드로이드는 다 켜졌는데 1분 넘게 adb에 안 보이면 adb 서버를 한 번 새로 띄워 다시 찾게 한다
        if (-not $adbRestarted -and (Test-LdAndroidStarted)) {
            if (-not $androidAt) {
                $androidAt = Get-Date
            } elseif (((Get-Date) - $androidAt).TotalSeconds -ge 60) {
                $adbRestarted = $true
                if ($DryRun) {
                    Write-Log 'emu' '안드로이드는 켜졌는데 adb에 기기가 안 보임 - DryRun이라 adb 서버 재시작은 건너뜀'
                } elseif (@(Find-OtherInstances -BotOnly).Count) {
                    Write-Log 'emu' '안드로이드는 켜졌는데 adb에 기기가 안 보임 - 다른 bot.py가 있어 adb 서버는 그대로 둠'
                } else {
                    Write-Log 'emu' '안드로이드는 켜졌는데 adb에 기기가 안 보임 - adb 서버를 다시 띄움'
                    [void](Invoke-Exe $adb @('kill-server') 20)
                    Start-Sleep -Seconds 2
                    [void](Invoke-Exe $adb @('start-server') 30)
                }
            }
        }
    }
    return 'timeout'
}

function Wait-EmulatorForever {
    # 준비될 때까지 돌아오지 않는다. 이번에 부팅을 기다렸으면 $true
    $n = 0
    while ($true) {
        $n++
        $r = Wait-Emulator
        if ($r -eq 'ready') {
            Write-Log 'emu' "에뮬레이터 준비돼 있음($s device, boot_completed=1)"
            return $false
        }
        if ($r -eq 'booted') { return $true }
        Write-Log 'emu' "에뮬레이터가 $($BootWaitSec)초 안에 준비되지 않음(시도 $n) - $($RetrySec)초 뒤 다시"
        Start-Sleep -Seconds $RetrySec
    }
}

# ───────────────────────── 포트·카톡·Iris·웹훅 ─────────────────────────
function Set-AdbPorts {
    # PC:3000 -> 에뮬레이터 Iris,  에뮬레이터:<listen_port> -> PC 봇
    $f = Invoke-Exe $adb @('-s', $s, 'forward', 'tcp:3000', 'tcp:3000') 15
    $r = Invoke-Exe $adb @('-s', $s, 'reverse', "tcp:$port", "tcp:$port") 15
    $fs = if ($f.Code -eq 0) { 'OK' } else { "실패($(Get-Short "$($f.Out) $($f.Err)" 80))" }
    $rs = if ($r.Code -eq 0) { 'OK' } else { "실패($(Get-Short "$($r.Out) $($r.Err)" 80))" }
    Write-Log 'adb' "forward tcp:3000 $fs, reverse tcp:$port $rs"
}

function Get-KakaoPid {
    # pid 문자열 / '' (안 떠 있음) / $null (확인 실패)
    $r = Invoke-Exe $adb @('-s', $s, 'shell', 'pidof com.kakao.talk; echo __OK__') 15
    if ($r.Out -notmatch '__OK__') { return $null }
    if ($r.Out -match '(?m)^\s*(\d+)') { return $Matches[1] }
    return ''
}

function Start-KakaoTalk([bool]$AfterBoot) {
    # 부팅 직후에는 항상 띄우고, 원래 켜져 있던 에뮬레이터에서는 카톡이 꺼져 있을 때만 띄운다(화면을 건드리지 않게)
    $k = Get-KakaoPid
    if ($k -and -not $AfterBoot) {
        Write-Log 'kakao' "카톡 이미 실행 중(pid $k)"
        return
    }
    $why = if ($AfterBoot) { '부팅 직후' } elseif ($null -eq $k) { '상태 확인 실패' } else { '카톡이 꺼져 있음' }
    $r = Invoke-Exe $adb @('-s', $s, 'shell', 'monkey -p com.kakao.talk -c android.intent.category.LAUNCHER 1') 30
    $k2 = $null
    for ($i = 0; $i -lt 10 -and -not $k2; $i++) {
        Start-Sleep -Seconds 2
        $k2 = Get-KakaoPid
    }
    if ($k2) {
        Write-Log 'kakao' "카톡 실행함($why) - pid $k2"
        Start-Sleep -Seconds 5      # 카톡이 DB를 열 시간
    } else {
        Write-Log 'kakao' "카톡 실행 명령 뒤에도 프로세스가 안 보임($why, monkey: $(Get-Short "$($r.Out) $($r.Err)" 120))"
    }
}

function Test-IrisRunning {
    # $true / $false / $null(확인 실패)
    $r = Invoke-Exe $adb @('-s', $s, 'shell', 'ps -ef | grep party.qwer.iris | grep -v grep; echo __OK__') 20
    if ($r.Out -notmatch '__OK__') { return $null }
    return ($r.Out -match 'party\.qwer\.iris')
}

function Start-Iris {
    $running = Test-IrisRunning
    if ($null -eq $running) {
        Write-Log 'iris' 'Iris 상태 확인 실패(adb) - 시작은 건너뜀(봇이 1분마다 확인해 재시작)'
        return
    }
    if ($running) {
        Write-Log 'iris' 'Iris 이미 실행 중'
        return
    }
    Write-Host 'Iris 시작...'
    try {
        Start-Process -WindowStyle Hidden $adb -ArgumentList "-s", $s, "shell", "su -c 'nohup app_process -cp /data/local/tmp/Iris.apk / party.qwer.iris.Main > /data/local/tmp/iris.log 2>&1 &'" -ErrorAction Stop
    } catch {
        Write-Log 'iris' "Iris 시작 명령 실패: $(Get-Short $_.Exception.Message)"
        return
    }
    Start-Sleep -Seconds 8
    if (Test-IrisRunning) {
        Write-Log 'iris' 'Iris 시작함(프로세스 확인)'
    } else {
        Write-Log 'iris' 'Iris 시작 명령을 보냈지만 프로세스가 안 보임 - 에뮬레이터의 /data/local/tmp/iris.log 확인'
    }
}

function Set-IrisEndpoint {
    # POST /config/endpoint 뒤 GET /config의 web_server_endpoint로 실제 반영을 확인한다. 10초 간격 6번까지
    $want = "http://127.0.0.1:$port/webhook"
    $last = ''
    for ($i = 1; $i -le 6; $i++) {
        try {
            $resp = Invoke-RestMethod "http://127.0.0.1:3000/config/endpoint" -Method Post -ContentType "application/json" `
                -Body (@{ endpoint = $want } | ConvertTo-Json) -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
            $respText = Get-Short (ConvertTo-Json $resp -Compress -Depth 3) 120
            if ($resp -and $resp.PSObject.Properties['success'] -and -not $resp.success) {
                $last = "POST 응답이 실패: $respText"
            } else {
                $now = Invoke-RestMethod "http://127.0.0.1:3000/config" -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
                if ($now.web_server_endpoint -eq $want) {
                    Write-Log 'hook' "Iris 웹훅 설정 확인: $want (POST 응답 $respText)"
                    return $true
                }
                $last = "POST 뒤 /config의 web_server_endpoint=$($now.web_server_endpoint) (POST 응답 $respText)"
            }
        } catch {
            $last = Get-Short $_.Exception.Message
        }
        if ($i -lt 6) { Start-Sleep -Seconds 10 }
    }
    Write-Log 'hook' "Iris 웹훅 설정 실패(6번 시도): $last - 봇이 1분마다 다시 시도함"
    return $false
}

function Invoke-Setup([bool]$AfterBoot) {
    Set-AdbPorts
    Start-KakaoTalk $AfterBoot
    Start-Iris
    [void](Set-IrisEndpoint)
}

# ───────────────────────── 실행 ─────────────────────────
try {
    Limit-LogSize
    Write-Log 'start' ("start.ps1 시작(사용자 {0}, 부모 {1}, PowerShell {2})" -f $env:USERNAME, (Get-ParentName), $PSVersionTable.PSVersion)
    Invoke-Guard

    $cfg  = Read-Config
    $adb  = $cfg.adb_path
    $s    = $cfg.adb_serial
    $port = $cfg.listen_port
    $ldconsole = Join-Path (Split-Path -Parent $adb) 'ldconsole.exe'
    if (-not (Test-Path -LiteralPath $ldconsole)) { $ldconsole = 'C:\LDPlayer\LDPlayer9\ldconsole.exe' }
    Write-Log 'config' "adb=$adb, serial=$s, port=$port, ldconsole=$ldconsole"

    # 에뮬레이터 준비 -> 포트·카톡·Iris·웹훅. 도중에 예외가 나면 30초 뒤 처음부터 다시
    while ($true) {
        try {
            $booted = Wait-EmulatorForever
            Invoke-Setup $booted
            break
        } catch {
            Write-Log 'error' "준비 중 오류: $(Get-Short $_.Exception.Message) - $($RetrySec)초 뒤 다시"
            Start-Sleep -Seconds $RetrySec
        }
    }

    if ($DryRun) {
        Write-Log 'bot' 'DryRun이라 bot.py는 띄우지 않고 끝냄'
        return
    }

    # 봇이 죽으면 5초 뒤 다시 띄운다(10분 안에 5번 넘게 죽으면 5분 쉼).
    # 끝내려면 Ctrl+C 두 번. 숨김 실행이면 Stop-ScheduledTask KakaoBot 뒤 bot.py 파이썬 프로세스도 끈다.
    $deaths = @()
    $run = 0
    $waitingForOther = $false
    while ($true) {
        try {
            Limit-LogSize
            # 그 사이 누가 bot.py를 따로 띄웠으면 두 번째 봇을 만들지 않는다
            $others = @(Find-OtherInstances -BotOnly)
            if ($others.Count) {
                if (-not $waitingForOther) {
                    Write-Log 'bot' "다른 bot.py가 실행 중($($others -join ', ')) - 새로 띄우지 않고 1분마다 확인"
                    $waitingForOther = $true
                }
                Start-Sleep -Seconds 60
                continue
            }
            $waitingForOther = $false
            # 다시 띄우기 전에 에뮬레이터가 꺼졌는지 본다. 꺼졌으면 켜고 포트·카톡·Iris·웹훅부터 다시
            if ($run -gt 0 -and -not (Test-EmulatorReady)) {
                Write-Log 'emu' '에뮬레이터가 준비 안 됨 - 봇을 다시 띄우기 전에 다시 준비'
                $booted = Wait-EmulatorForever
                Invoke-Setup $booted
            }
            $run++
            Write-Log 'bot' "bot.py 시작(#$run)"
            $t0 = Get-Date
            try {
                py -3.12 -I "$PSScriptRoot\bot.py"
                $code = $LASTEXITCODE
            } catch {
                $code = "실행 실패: $(Get-Short $_.Exception.Message 120)"
            }
            $secs = [int]((Get-Date) - $t0).TotalSeconds
            $now = Get-Date
            $deaths = @(@($deaths) + $now | Where-Object { $_ -gt $now.AddMinutes(-10) })
            if ($deaths.Count -gt 5) {
                Write-Log 'bot' "bot.py 종료(코드 $code, $($secs)초 실행) - 10분 안에 $($deaths.Count)번 죽어서 5분 쉬고 다시 띄움"
                Start-Sleep -Seconds 300
            } else {
                Write-Log 'bot' "bot.py 종료(코드 $code, $($secs)초 실행) - 5초 뒤 다시 띄움"
                Start-Sleep -Seconds 5
            }
        } catch {
            Write-Log 'error' "봇 반복 중 오류: $(Get-Short $_.Exception.Message) - $($RetrySec)초 뒤 다시"
            Start-Sleep -Seconds $RetrySec
        }
    }
} finally {
    if ($script:Mutex) {
        try { $script:Mutex.ReleaseMutex() } catch { }
        $script:Mutex.Dispose()
    }
}
