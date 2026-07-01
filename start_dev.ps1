# PET Lab Monitor — dev startup script
# Detects the current WiFi IP, writes mobile/.env, opens firewall ports, starts all services.
# Run from repo root: .\start_dev.ps1
# Run as Administrator on first use (needed to add firewall rules once).

param(
    [string]$ForceIP = ""   # override auto-detection: .\start_dev.ps1 -ForceIP 10.0.0.5
)

$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }

# ── 1. Detect network IP ────────────────────────────────────────────────────

function Get-WiFiIP {
    # Try the physical Wi-Fi adapter first
    $wifi = Get-NetIPAddress -InterfaceAlias "WiFi" -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "169.254.*" } |
        Select-Object -First 1 -ExpandProperty IPAddress
    if ($wifi) { return $wifi }

    # Fall back: any non-loopback, non-link-local IPv4 address
    $any = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notlike "127.*" -and
            $_.IPAddress -notlike "169.254.*"
        } |
        Sort-Object PrefixLength -Descending |   # prefer narrower subnets (/24 before /8)
        Select-Object -First 1 -ExpandProperty IPAddress
    return $any
}

if ($ForceIP) {
    $ip = $ForceIP
    Write-Host "[IP] Using forced IP: $ip"
} else {
    $ip = Get-WiFiIP
    if (-not $ip) {
        Write-Warning "Could not detect a network IP. Defaulting to hotspot IP 172.20.10.2."
        Write-Warning "Connect to WiFi or use: .\start_dev.ps1 -ForceIP <your-ip>"
        $ip = "172.20.10.2"
    } else {
        Write-Host "[IP] Detected: $ip"
    }
}

$apiUrl  = "http://${ip}:8000"
$metroUrl = "exp://${ip}:8082"

# ── 2. Write mobile/.env ────────────────────────────────────────────────────

$envPath = Join-Path $root "mobile\.env"
Set-Content -Path $envPath -Value "EXPO_PUBLIC_API_URL=$apiUrl" -Encoding utf8
Write-Host "[ENV] Wrote $envPath → $apiUrl"

# ── 3. Windows Firewall (needs admin — silently skips if not elevated) ──────

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($isAdmin) {
    foreach ($entry in @(
        @{ name = "PET Lab API (8000)";   port = 8000 },
        @{ name = "PET Lab Metro (8082)"; port = 8082 }
    )) {
        $exists = Get-NetFirewallRule -DisplayName $entry.name -ErrorAction SilentlyContinue
        if (-not $exists) {
            New-NetFirewallRule -DisplayName $entry.name -Direction Inbound -Protocol TCP -LocalPort $entry.port -Action Allow | Out-Null
            Write-Host "[FW] Rule added: $($entry.name)"
        }
    }
} else {
    Write-Warning "[FW] Not running as admin — firewall rules not added."
    Write-Warning "     If phone can't connect: run 'Start-Process powershell -Verb RunAs' then re-run this script."
}

# ── 4. Start services in separate windows ───────────────────────────────────

Write-Host "[SVC] Starting Ollama..."
Start-Process cmd -ArgumentList "/k ollama serve"

Write-Host "[SVC] Starting FastAPI..."
# On-prem dev machines need Ollama as the gauge-OCR fallback (Gemini quota/outage).
# The source default is intentionally empty for cloud/Render (no Ollama binary there,
# and render.yaml sets its own env) — but start_dev.ps1 only ever runs for local dev,
# so it's safe to hardcode the on-prem model here for the spawned FastAPI window.
$env:GAUGE_OLLAMA_MODEL = 'qwen2.5vl:7b'
Start-Process cmd -ArgumentList "/k `"cd /d `"$root`" && python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload`""

Start-Sleep -Seconds 2   # let uvicorn bind before Metro prints the QR

Write-Host "[SVC] Starting Expo Metro..."
Start-Process cmd -ArgumentList "/k `"cd /d `"$root\mobile`" && npx expo start --port 8082`""

# ── 5. Regenerate QR code on Desktop ────────────────────────────────────────

$qrScript = @"
import qrcode, os
from PIL import Image, ImageDraw, ImageFont

url = "$metroUrl"
qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=3)
qr.add_data(url)
qr.make(fit=True)
img = qr.make_image(fill_color='white', back_color='#1a1a2e').convert('RGB')
w, h = img.size
banner = 36
canvas = Image.new('RGB', (w, h + banner), '#1a1a2e')
canvas.paste(img, (0, 0))
draw = ImageDraw.Draw(canvas)
try:
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 13)
except:
    font = ImageFont.load_default()
draw.text((w // 2, h + banner // 2), url, fill='white', font=font, anchor='mm')
out = os.path.join(os.path.expanduser('~'), 'Desktop', 'petlab_qr.png')
canvas.save(out)
print('QR saved:', out)
"@

$qrScriptPath = Join-Path $env:TEMP "petlab_qr_gen.py"
Set-Content -Path $qrScriptPath -Value $qrScript -Encoding utf8
python $qrScriptPath

# ── 6. Regenerate gauge QR label PDFs for the current WiFi IP ───────────────
# The printed labels' QR codes encode $apiUrl -- if the WiFi network (and
# therefore the IP) changed since they were last printed, they'd silently
# point at the wrong address. Every start_dev.ps1 run re-detects the IP
# (step 1) and regenerates the PDFs against it, so they're always current
# as of the last time the dev environment was started.
Write-Host "[QR] Regenerating gauge label PDFs for $apiUrl ..."
python (Join-Path $root "scripts\generate_gauge_qr_labels.py") `
    --db-path (Join-Path $root "data\cyclotron.db") `
    --base-url $apiUrl `
    --output-dir (Join-Path $root "qr_labels")

# ── 7. Summary ───────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "=============================================="
Write-Host " PET Lab Monitor dev environment started"
Write-Host "=============================================="
Write-Host "  API   : $apiUrl"
Write-Host "  Expo  : $metroUrl"
Write-Host "  QR    : ~/Desktop/petlab_qr.png"
Write-Host ""
Write-Host "  Phone must be on the SAME WiFi network as this machine."
Write-Host "  Open Expo Go → scan the QR in the terminal or on Desktop."
Write-Host "=============================================="
