# esp_link.ps1 - CASSANDRA serial bridge, pure PowerShell (nothing to install)
#
# Windows owns the ESP32 (SatB) on a COM port. Redis runs inside WSL and is
# reachable from Windows at localhost:6379.
#
#   board -> Redis : real chip temp from "TELEM temp=.. heap=.." lines
#       hw:chip_temp   raw chip temperature
#       truth:temp     mapped satellite temperature (always the real value)
#       telemetry:temp same value, EXCEPT while attack:active is set, so a
#                      masking attack's poisoned value stays in place
#   Redis -> board : node:sat_a / node:sat_b / node:sat_c drive the 3 LEDs
#       green -> G     red -> R     anything else (dark/quarantined) -> O
#
# Run:
#   powershell -ExecutionPolicy Bypass -File .\esp_link.ps1 -Port COM9
# The ESP8266 firmware models SatB's bus temperature (25 C ambient). Setting
# attack:thermal=1 in Redis latches the heater on the board; 0 releases it.
# (-Sensor ntc / lm35 only applies to firmware that sends a raw A0 reading.)
# Stop with Ctrl+C. Close Arduino IDE / Serial Monitor first (they hold the port).

param(
    [string]$Port = "COM9",
    [string]$RedisHost = "localhost",
    [int]$RedisPort = 6379,
    [ValidateSet("lm35", "ntc", "raw")]
    [string]$Sensor = "ntc",     # ESP8266 only: what is wired to A0
    [double]$Gain = 1.0,         # satellite temp = Offset + Gain * sensor temp
    [double]$Offset = 0.0
)

$ErrorActionPreference = "Stop"
$inv = [System.Globalization.CultureInfo]::InvariantCulture   # French locale uses "," for decimals

# ---------- Redis over raw TCP (RESP protocol) ----------
$tcp = New-Object System.Net.Sockets.TcpClient
$tcp.Connect($RedisHost, $RedisPort)
$ns  = $tcp.GetStream()
$enc = [System.Text.Encoding]::ASCII
$rd  = New-Object System.IO.StreamReader($ns, $enc)
$wr  = New-Object System.IO.StreamWriter($ns, $enc)
$wr.AutoFlush = $true

function Invoke-Redis([string[]]$parts) {
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append("*" + $parts.Count + "`r`n")
    foreach ($p in $parts) { [void]$sb.Append('$' + $p.Length + "`r`n" + $p + "`r`n") }
    $wr.Write($sb.ToString())
    $line = $rd.ReadLine()
    if ($null -eq $line) { throw "Redis closed the connection" }
    $t = $line[0]
    if ($t -eq '$') {
        $n = [int]$line.Substring(1)
        if ($n -lt 0) { return $null }
        $buf = New-Object char[] ($n + 2)
        $got = 0
        while ($got -lt ($n + 2)) { $got += $rd.Read($buf, $got, ($n + 2) - $got) }
        return [string]::new($buf, 0, $n)
    }
    if ($t -eq '-') { Write-Host "  redis error: $line" -ForegroundColor Red; return $null }
    return $line.Substring(1)
}

function To-Led($v) {
    if ($null -eq $v -or $v -eq "") { return "G" }
    $s = $v.ToString().Trim().ToLower()
    if (@("green","g","ok","healthy","up","nominal") -contains $s) { return "G" }
    if (@("red","r","alert","attacked","compromised","alarm") -contains $s) { return "R" }
    return "O"
}

# ---------- Serial ----------
$sp = New-Object System.IO.Ports.SerialPort $Port, 115200, None, 8, one
$sp.Open()
Start-Sleep -Seconds 2
Write-Host "[esp_link] linked $Port <-> redis ${RedisHost}:$RedisPort   (Ctrl+C to stop)" -ForegroundColor Green

$map  = [ordered]@{ "sat_a" = 1; "sat_b" = 2; "sat_c" = 3 }
$last = @{}
$buf  = ""
$lastPrint = Get-Date
$chipS = "?"; $satS = "?"
$lastHeat = $null

try {
    while ($true) {
        # 1) board -> Redis
        $data = $sp.ReadExisting()
        if ($data) {
            $buf += $data
            while ($buf.Contains("`n")) {
                $i = $buf.IndexOf("`n")
                $line = $buf.Substring(0, $i).Trim()
                $buf = $buf.Substring($i + 1)
                $chip = $null
                if ($line -match '^TELEM temp=([-0-9.]+)') {          # ESP32 internal sensor
                    $chip = [double]::Parse($matches[1], $inv)
                    $sat  = [math]::Round(20 + ($chip - 45) * 1.25, 2)
                }
                elseif ($line -match '^TELEM sim=([-0-9.]+) heater=([01])') {   # ESP8266 thermal model
                    $chip = [double]::Parse($matches[1], $inv)
                    $sat  = [math]::Round($Offset + $chip * $Gain, 2)
                    [void](Invoke-Redis @("SET", "hw:heater", $matches[2]))
                }
                elseif ($line -match '^TELEM raw=([0-9]+)') {           # ESP8266: sensor on A0
                    $raw = [double]::Parse($matches[1], $inv)
                    $v = $raw * 3.3 / 1023.0
                    switch ($Sensor) {
                        "lm35" { $chip = $v * 100.0 }
                        "ntc"  {
                            if ($v -le 0.01 -or $v -ge 3.29) { $chip = $null }
                            else {
                                $r = 10000.0 * (3.3 / $v - 1.0)
                                $chip = 1.0 / (1.0 / 298.15 + [math]::Log($r / 10000.0) / 3950.0) - 273.15
                            }
                        }
                        default { $chip = $raw / 10.0 }
                    }
                    if ($null -ne $chip) {
                        $chip = [math]::Round($chip, 2)
                        $sat  = [math]::Round($Offset + $chip * $Gain, 2)
                    }
                }
                if ($null -ne $chip) {
                    $chipS = $chip.ToString($inv)
                    $satS  = $sat.ToString($inv)
                    [void](Invoke-Redis @("SET", "hw:chip_temp", $chipS))
                    [void](Invoke-Redis @("SET", "truth:temp", $satS))
                    $atk = Invoke-Redis @("GET", "attack:active")
                    if (-not $atk) { [void](Invoke-Redis @("SET", "telemetry:temp", $satS)) }
                }
            }
        }

        # 2) Redis -> board: thermal attack command (heater latch), on change only
        $th = Invoke-Redis @("GET", "attack:thermal")
        $hc = if ($th -eq "1") { "H" } else { "C" }
        if ($lastHeat -ne $hc) {
            $sp.Write("$hc`n")
            $lastHeat = $hc
            if ($hc -eq "H") { Write-Host "  >>> thermal attack: heater latched ON" -ForegroundColor Red }
            else             { Write-Host "  heater released" -ForegroundColor Green }
        }

        # 3) Redis -> LEDs (send only on change)
        $states = @()
        foreach ($k in $map.Keys) {
            $c = To-Led (Invoke-Redis @("GET", "node:$k"))
            $idx = $map[$k]
            if ($last[$idx] -ne $c) {
                $sp.Write("$idx$c`n")
                $last[$idx] = $c
            }
            $states += "$($k.ToUpper()):$c"
        }

        if (((Get-Date) - $lastPrint).TotalSeconds -ge 1) {
            $lastPrint = Get-Date
            Write-Host ("  chip={0}C  sat_temp={1}C   {2}" -f $chipS, $satS, ($states -join "  "))
        }
        Start-Sleep -Milliseconds 100
    }
}
finally {
    if ($sp.IsOpen) { $sp.Close() }
    $tcp.Close()
    Write-Host "[esp_link] stopped"
}
