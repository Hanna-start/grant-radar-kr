# Grant Radar KR 주간 자동 실행 (PowerShell 5.1 호환)
# 공고는 원천별로 한 번만 수집하고, 발견된 회사 프로필마다 별도로 판정해 한 통의 메일로 보낸다.
# 회사 프로필: data/company.json, data/company_*.json. 둘 다 없으면 가상 샘플을 사용한다.

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Py = Join-Path $Root ".venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$StartedAt = Get-Date
$Stamp = $StartedAt.ToString("yyyyMMdd")
$RunDate = $StartedAt.ToString("yyyy-MM-dd")
$StartedIso = $StartedAt.ToString("yyyy-MM-ddTHH:mm:ss")
$LogDir = Join-Path $Root "logs"
$ReportDir = Join-Path $Root "reports\weekly"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
$Log = Join-Path $LogDir "weekly-$Stamp.log"
$StateFile = Join-Path $Root "data\last_success.txt"

if (Test-Path $StateFile) {
    $Since = (Get-Content $StateFile -Raw).Trim()
} else {
    $Since = $StartedAt.AddDays(-7).ToString("yyyy-MM-dd")
}

function Write-Log($text) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $text
    $line | Out-File -FilePath $Log -Append -Encoding utf8
}

function Invoke-Radar([string[]]$RadarArgs) {
    $quoted = ($RadarArgs | ForEach-Object { '"' + $_ + '"' }) -join " "
    Write-Log ("> grant_radar " + ($RadarArgs -join " "))
    cmd /c "`"$Py`" -m grant_radar $quoted >> `"$Log`" 2>&1"
    return $LASTEXITCODE
}

if (-not (Test-Path $Py)) {
    Write-Log "venv python 없음: $Py"
    exit 1
}

$CompanyFiles = @()
$PrimaryCompany = Join-Path $Root "data\company.json"
if (Test-Path $PrimaryCompany) {
    $CompanyFiles += Get-Item $PrimaryCompany
}
$CompanyFiles += @(Get-ChildItem (Join-Path $Root "data") -Filter "company_*.json" -File -ErrorAction SilentlyContinue)
if ($CompanyFiles.Count -eq 0) {
    $CompanyFiles += Get-Item (Join-Path $Root "data\sample_company.json")
}

Write-Log "주간 실행 시작 (since=$Since, 회사 프로필=$($CompanyFiles.Count)개)"
foreach ($FetchArgs in @(
    @("fetch", "--per-page", "100", "--pages", "3"),
    @("fetch", "--source", "bizinfo", "--per-page", "100", "--pages", "17")
)) {
    $code = Invoke-Radar $FetchArgs
    if ($code -ne 0) {
        Write-Log "공고 수집 실패 (exit $code)"
        Invoke-Radar @("mail", "--failure-log", $Log, "--date", $RunDate) | Out-Null
        exit 1
    }
}

$MailArgs = @("mail")
$Index = 0
foreach ($CompanyFile in $CompanyFiles) {
    $Index += 1
    $Slug = [IO.Path]::GetFileNameWithoutExtension($CompanyFile.Name) -replace '^company_', ''
    if ($CompanyFile.Name -eq "company.json") { $Slug = "primary" }
    if ($CompanyFile.Name -eq "sample_company.json") { $Slug = "sample" }
    $Report = "reports\weekly\report-$Slug-$Stamp.md"
    $Json = "reports\weekly\eval-$Slug-$Stamp.json"
    $code = Invoke-Radar @(
        "evaluate", "--company", $CompanyFile.FullName,
        "--open-only", "--since", $Since, "--report", $Report, "--json", $Json
    )
    if ($code -ne 0) {
        Write-Log "회사 프로필 $($CompanyFile.Name) 판정 실패 (exit $code)"
        Invoke-Radar @("mail", "--failure-log", $Log, "--date", $RunDate) | Out-Null
        exit 1
    }
    $MailArgs += @("--report", $Report, "--json", $Json, "--label", $Slug)
    if (Test-Path $Report) {
        Copy-Item $Report (Join-Path $ReportDir "latest-$Slug.md") -Force
    }
}

$code = Invoke-Radar ($MailArgs + @("--date", $RunDate))
if ($code -ne 0) {
    Write-Log "메일 발송 실패 (exit $code) — last_success 갱신하지 않음"
    exit 1
}

$StartedIso | Out-File -FilePath $StateFile -Encoding ascii
Write-Log "성공 — last_success=$StartedIso"

$cutoff = (Get-Date).AddDays(-30)
$old = @(Get-ChildItem (Join-Path $Root "data\raw") -File -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt $cutoff })
if ($old.Count -gt 0) {
    $old | Remove-Item -Force -Confirm:$false
    Write-Log ("data/raw 30일 초과 파일 {0}개 삭제" -f $old.Count)
}

exit 0
