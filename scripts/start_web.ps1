# Windows launcher. Existing CLI environments and company data are preserved.
$ErrorActionPreference = "Stop"
$Root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
Set-Location -LiteralPath $Root
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:STREAMLIT_BROWSER_GATHER_USAGE_STATS = "false"
$Venv = Join-Path $Root ".venv-web"
$Python = Join-Path $Venv "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "Preparing Python environment..."
    $Launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($Launcher) {
        & $Launcher.Source -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
        if ($LASTEXITCODE -eq 0) { & $Launcher.Source -3 -m venv $Venv }
    } else {
        $Launcher = Get-Command python -ErrorAction SilentlyContinue
        if ($Launcher) {
            & $Launcher.Source -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
            if ($LASTEXITCODE -eq 0) { & $Launcher.Source -m venv $Venv }
        }
    }
    if (-not (Test-Path -LiteralPath $Python)) {
        throw "Install Python 3.12 or later from https://www.python.org/downloads/ then run start-web.cmd again."
    }
}
& $Python -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw ".venv-web belongs to another Python installation. Rename that folder, then run again. Company data is in data/ and .env."
}
$Marker = Join-Path $Venv ".web-install-hash"
$Hasher = [Security.Cryptography.SHA256]::Create()
try { $Hash = [BitConverter]::ToString($Hasher.ComputeHash([IO.File]::ReadAllBytes((Join-Path $Root "pyproject.toml")))) }
finally { $Hasher.Dispose() }
$Installed = if (Test-Path -LiteralPath $Marker) { (Get-Content -LiteralPath $Marker -Raw).Trim() } else { "" }
if ($Installed -ne $Hash) {
    Write-Host "Installing local web dependencies (first run needs internet)..."
    & $Python -m pip install --disable-pip-version-check -e "${Root}[web]"
    if ($LASTEXITCODE -ne 0) { throw "Installation failed. Check the internet connection and run again." }
    Set-Content -LiteralPath $Marker -Value $Hash -Encoding ascii
}
Write-Host "Opening Grant Radar at http://127.0.0.1:8501"
& $Python -m streamlit run (Join-Path $Root "src\grant_radar\web_app.py") --server.address 127.0.0.1 --browser.gatherUsageStats false
if ($LASTEXITCODE -ne 0) { throw "The web app stopped. Check whether port 8501 is already in use." }
