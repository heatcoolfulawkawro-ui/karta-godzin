# Backup karta-godzin: pobiera pelny zrzut danych z Apps Script i zapisuje w kilku miejscach.
# Klucz kopii jest zaszyfrowany kontem Windows (DPAPI) w F:\AI\_backupy\_config\key.dat (poza repo).
# Nie uzywamy %LOCALAPPDATA%: pakiet aplikacji Claude wirtualizuje tam zapisy i Harmonogram
# widzialby inne pliki niz sesja Claude.
# Skrypt NICZEGO nie kasuje - kopie sa male, wiec zostaja wszystkie.
# Uruchamiany z Harmonogramu zadan (codziennie + przy logowaniu) albo recznie:
#   powershell -ExecutionPolicy Bypass -File backup-karta-godzin.ps1 [-Force]
param([switch]$Force)

$ErrorActionPreference = 'Stop'
$GasUrl    = 'https://script.google.com/macros/s/AKfycby09rSaJwoPPl6KeFn80xCOTiOzYM4EZyKy5XuJ0pBA28-x051wB9HXg_osSqUrjoHA/exec'
$Primary   = 'F:\AI\_backupy\karta-godzin'
$ConfigDir = 'F:\AI\_backupy\_config'
$KeyFile   = Join-Path $ConfigDir 'key.dat'
$LogFile   = Join-Path $ConfigDir 'backup.log'

function Log($msg) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Output $line
}

# Miejsca docelowe: glowne, druga partycja "BACKUP" (H:) i kazdy podpiety dysk zewnetrzny
# z etykieta zaczynajaca sie od KOPIE / ZEWN / BACKUP-ZEWN.
function Get-Destinations {
    $list = @($Primary)
    $h = Get-Volume -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter -eq 'H' }
    if ($h) { $list += 'H:\BACKUP-karta-godzin' }
    Get-Volume -ErrorAction SilentlyContinue |
        Where-Object { $_.DriveLetter -and $_.FileSystemLabel -match '^(KOPIE|ZEWN|BACKUP-ZEWN)' } |
        ForEach-Object { $list += ('{0}:\BACKUP\karta-godzin' -f $_.DriveLetter) }
    return $list
}

try {
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    New-Item -ItemType Directory -Force -Path $Primary | Out-Null

    # Dostep do klucza sprawdzany przy kazdym uruchomieniu (takze gdy dzisiejsza kopia juz jest).
    if (-not (Test-Path $KeyFile)) { throw "Brak klucza kopii: $KeyFile" }
    $secure = Get-Content -Path $KeyFile | ConvertTo-SecureString
    Log ('Uruchomienie jako {0}; klucz kopii odczytany.' -f $env:USERNAME)

    $today = Get-Date -Format 'yyyy-MM-dd'
    $existing = Get-ChildItem -Path $Primary -Filter "karta-godzin_${today}_*.json" -ErrorAction SilentlyContinue
    if ($existing -and -not $Force) {
        Log "Kopia z dzisiaj juz istnieje ($($existing[0].Name)) - pomijam (uzyj -Force, by wymusic)."
        $target = $existing[0]
    } else {
        $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
        $body = [Text.Encoding]::UTF8.GetBytes((@{ action = 'backup'; backupKey = $key } | ConvertTo-Json -Compress))
        $key = $null

        $resp = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $GasUrl -ContentType 'text/plain;charset=utf-8' -Body $body -TimeoutSec 120
        $text = [Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray())
        $dump = $text | ConvertFrom-Json
        if (-not $dump.ok) { throw "Serwer odrzucil zadanie kopii: $($dump.error)" }
        if ($dump.counts.data -lt 1) { throw 'Zrzut jest pusty - nie zapisuje.' }

        $name = 'karta-godzin_{0}.json' -f (Get-Date -Format 'yyyy-MM-dd_HHmm')
        $target = Join-Path $Primary $name
        [IO.File]::WriteAllText($target, $text, (New-Object Text.UTF8Encoding $false))
        $sha = (Get-FileHash -Path $target -Algorithm SHA256).Hash
        Set-Content -Path ($target + '.sha256') -Value "$sha  $name" -Encoding ASCII
        Log ("Zapisano {0}: wiersze danych={1}, konta={2}, wpisy dziennika={3}" -f $name, $dump.counts.data, $dump.counts.users, $dump.counts.audit)
        $target = Get-Item $target
    }

    foreach ($dest in (Get-Destinations)) {
        if ($dest -eq $Primary) { continue }
        try {
            New-Item -ItemType Directory -Force -Path $dest | Out-Null
            foreach ($f in @($target.FullName, ($target.FullName + '.sha256'))) {
                if (-not (Test-Path $f)) { continue }
                $to = Join-Path $dest (Split-Path $f -Leaf)
                if (-not (Test-Path $to)) { Copy-Item -Path $f -Destination $to }
            }
            Log "Skopiowano do $dest"
        } catch {
            Log "UWAGA: nie udalo sie skopiowac do ${dest}: $($_.Exception.Message)"
        }
    }

    $newest = Get-ChildItem -Path $Primary -Filter 'karta-godzin_*.json' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    Log ("Najnowsza kopia: {0} ({1} KB)" -f $newest.Name, [math]::Round($newest.Length / 1KB, 1))
    exit 0
} catch {
    try { Log ("BLAD KOPII: " + $_.Exception.Message) } catch { }
    exit 1
}
