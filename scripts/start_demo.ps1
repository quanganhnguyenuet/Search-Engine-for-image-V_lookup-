<#
.SYNOPSIS
  Starts the demo on this machine: API on :8000, production web on 127.0.0.1:3000, and
  optionally a Cloudflare Tunnel to the web server.
.DESCRIPTION
  The API and the web server open in their own windows. The script stops before the tunnel
  if a safety check fails. See docs/ke-hoach-deploy-2026-11-27.md.
.EXAMPLE
  .\scripts\start_demo.ps1                       # local only
  .\scripts\start_demo.ps1 -Tunnel quick         # random trycloudflare.com address
  .\scripts\start_demo.ps1 -Tunnel named -TunnelName vlookup
#>
param(
    [ValidateSet("none", "quick", "named")]
    [string]$Tunnel = "none",
    [string]$TunnelName = "vlookup",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$web = Join-Path $root "apps\web"
$python = Join-Path $root ".venv\product-ingest\Scripts\python.exe"
$apiUrl = "http://127.0.0.1:8000"
$webUrl = "http://127.0.0.1:3000"

function Get-HttpStatus([string]$Url, [string]$Method = "GET") {
    $status = & curl.exe -s -o NUL -m 20 -w "%{http_code}" -X $Method $Url
    return [int]$status
}

function Wait-HttpOk([string]$Url, [int]$Seconds, [string]$Name) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if ((Get-HttpStatus $Url) -eq 200) { return }
        Start-Sleep -Seconds 3
    }
    throw "$Name did not answer at $Url within $Seconds seconds. Look at its window."
}

function Assert-Status([string]$Url, [int]$Expected, [string]$What, [string]$Method = "GET") {
    $actual = Get-HttpStatus $Url $Method
    if ($actual -ne $Expected) { throw "$What (expected HTTP $Expected, got $actual from $Url)" }
    Write-Host "  ok  $What"
}

if (-not (Test-Path $python)) { throw "Python venv not found: $python" }

$wanted = (Get-Content (Join-Path $web "package.json") -Raw | ConvertFrom-Json).dependencies.next
$installedFile = Join-Path $web "node_modules\next\package.json"
$installed = if (Test-Path $installedFile) { (Get-Content $installedFile -Raw | ConvertFrom-Json).version }
if ($installed -ne $wanted) {
    throw "next $wanted is required but '$installed' is installed. Stop the web server, then: cd apps\web; npm ci"
}

if (Get-NetTCPConnection -State Listen -LocalPort 3000 -ErrorAction SilentlyContinue) {
    throw "Port 3000 is in use (npm run dev?). Stop it first: the tunnel must serve the production build."
}

if ($Tunnel -ne "none" -and -not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    throw "cloudflared is not installed. Install it, open a new terminal and run this again."
}

# --- API ---------------------------------------------------------------------------------
if ((Get-HttpStatus "$apiUrl/api/v1/health") -eq 200) {
    Write-Host "API is already running on :8000; reusing it."
} else {
    Write-Host "Starting the API (loading both models takes 1-2 minutes)..."
    $env:SEARCH_ENABLED = "true"
    $env:REQUIRE_LOGIN = "true"
    $env:APP_ENV = "production"
    $env:PYTHONPATH = "packages/retrieval/src"
    $apiCommand = "& '$python' -m uvicorn visualseek_api.main:app " +
        "--app-dir services/api/src --host 127.0.0.1 --port 8000"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $apiCommand -WorkingDirectory $root
    Wait-HttpOk "$apiUrl/api/v1/health" 300 "API"
}

# These hold however the API was started. An unknown image id is 404 once search is loaded
# and 503 while it is not.
Write-Host "Checking the API..."
Assert-Status "$apiUrl/api/v1/search/info" 401 "search needs a signed-in user (REQUIRE_LOGIN=true)"
Assert-Status "$apiUrl/api/v1/images/$('0' * 64)" 404 "search is loaded (SEARCH_ENABLED=true)"

# --- Web ---------------------------------------------------------------------------------
Push-Location $web
try {
    if (-not $SkipBuild) {
        Write-Host "Building the web app..."
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed." }
    }
    $chunks = Get-ChildItem ".next\static" -Recurse -File -Filter *.js
    if ($chunks | Select-String -SimpleMatch "localhost:8000" -List) {
        throw "The build calls localhost:8000. Set NEXT_PUBLIC_API_URL= (empty) in apps\web\.env.production.local and rebuild."
    }
} finally {
    Pop-Location
}

$webCommand = "node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $webCommand -WorkingDirectory $web
Wait-HttpOk "$webUrl/login" 90 "Web server"

Write-Host "Checking the public surface (through the web server)..."
Assert-Status "$webUrl/api/v1/health" 200 "the web server forwards /api/v1"
Assert-Status "$webUrl/api/v1/search/info" 401 "search stays closed without a token"
Assert-Status "$webUrl/api/v1/search/text" 401 "text search stays closed without a token" "POST"
Assert-Status "$webUrl/api/v1/images/$('0' * 64)" 404 "images are reachable without signing in"
Assert-Status "$webUrl/openapi.json" 307 "the API schema is not exposed"

Write-Host ""
Write-Host "Demo is up: $webUrl"
Write-Host "While a tunnel is open: never set REQUIRE_LOGIN=false (stop the tunnel before running"
Write-Host "bench_latency.py or evaluate_*.py), and close the tunnel after the demo."

# --- Tunnel ------------------------------------------------------------------------------
if ($Tunnel -eq "quick") {
    Write-Host "Opening a quick tunnel; the public address is printed below. Ctrl+C closes it."
    & cloudflared tunnel --url $webUrl
} elseif ($Tunnel -eq "named") {
    Write-Host "Running tunnel '$TunnelName' (its hostname must point to $webUrl). Ctrl+C closes it."
    & cloudflared tunnel run $TunnelName
}
