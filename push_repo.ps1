# ============================================================
#  push_repo.ps1 - GitHub repo yaratish va kodni yuklash
#  Talab: gh auth login bajarilgan bo'lishi kerak
# ============================================================

$ErrorActionPreference = "Continue"

$RepoName = "trading-engine"
$Description = "Algorithmic trading infrastructure for crypto futures - data collection, backtesting, and volatility-adaptive risk management"

Write-Host ""
Write-Host "=== GitHub'ga yuklanmoqda ===" -ForegroundColor Cyan

# -- 1. gh mavjudmi ------------------------------------------
$ghPath = Get-Command gh -ErrorAction SilentlyContinue
if (-not $ghPath) {
    Write-Host "GitHub CLI topilmadi. O'rnating:" -ForegroundColor Red
    Write-Host "  winget install --id GitHub.cli -e" -ForegroundColor Yellow
    Write-Host "Keyin PowerShell'ni qayta oching." -ForegroundColor Yellow
    exit 1
}

# -- 2. Auth -------------------------------------------------
gh auth status 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "GitHub'ga ulanmagansiz. Ishlating:" -ForegroundColor Red
    Write-Host "  gh auth login" -ForegroundColor Yellow
    exit 1
}
Write-Host "  [ok] GitHub auth" -ForegroundColor Green

# -- 3. .env himoyasi ----------------------------------------
$envProtected = Select-String -Path .gitignore -Pattern '^\.env$' -Quiet
if (-not $envProtected) {
    Write-Host "XAVF: .gitignore da .env yo'q! To'xtatildi." -ForegroundColor Red
    exit 1
}
Write-Host "  [ok] .env himoyalangan" -ForegroundColor Green

# -- 4. Git init ---------------------------------------------
if (-not (Test-Path .git)) {
    git init | Out-Null
    git branch -M main
    Write-Host "  [ok] git init" -ForegroundColor Green
}

# -- 5. Bosqichma-bosqich commitlar --------------------------

function Invoke-Commit {
    param(
        [string[]] $Paths,
        [string]   $Message
    )

    $existing = @()
    foreach ($p in $Paths) {
        if (Test-Path $p) { $existing += $p }
    }
    if ($existing.Count -eq 0) { return }

    git add -- $existing 2>&1 | Out-Null

    $staged = git diff --cached --name-only
    if ($staged) {
        git commit -q -m $Message
        Write-Host ("  + " + $Message) -ForegroundColor DarkGray
    }
}

Invoke-Commit -Paths @(".gitignore", "LICENSE", ".env.example", "pytest.ini") -Message "chore: project scaffolding, license and environment template"

Invoke-Commit -Paths @("requirements.txt") -Message "build: pin runtime dependencies"

Invoke-Commit -Paths @("core/__init__.py", "core/storage.py") -Message "feat(storage): SQLite candle store with gap detection and validation"

Invoke-Commit -Paths @("core/collector.py") -Message "feat(collector): historical backfill, gap repair and live polling"

Invoke-Commit -Paths @("core/risk_engine.py") -Message "feat(risk): ATR-adaptive position sizing with circuit breakers"

Invoke-Commit -Paths @("tests") -Message "test(storage): cover deduplication, gaps and malformed candles"

Invoke-Commit -Paths @(".github") -Message "ci: run pytest on push and pull request"

Invoke-Commit -Paths @("README.md", "docs") -Message "docs: architecture overview and design rationale"

# Qolganlari
git add -A 2>&1 | Out-Null
$staged = git diff --cached --name-only
if ($staged) {
    git commit -q -m "chore: remaining project files"
    Write-Host "  + chore: remaining project files" -ForegroundColor DarkGray
}

# -- 6. Repo yaratish / push ---------------------------------
$user = gh api user --jq .login

gh repo view "$user/$RepoName" 2>&1 | Out-Null
$exists = ($LASTEXITCODE -eq 0)

if ($exists) {
    Write-Host ""
    Write-Host "  Repo mavjud - push qilinmoqda..." -ForegroundColor Yellow
    git remote remove origin 2>$null
    git remote add origin "https://github.com/$user/$RepoName.git"
    git push -u origin main
}
else {
    Write-Host ""
    Write-Host "  Yangi repo yaratilmoqda..." -ForegroundColor Yellow
    gh repo create $RepoName --public --source=. --remote=origin --description $Description --push
}

# -- 7. Topiklar ---------------------------------------------
gh repo edit "$user/$RepoName" --add-topic "algorithmic-trading" --add-topic "cryptocurrency" --add-topic "backtesting" --add-topic "python" --add-topic "binance" --add-topic "quantitative-finance" 2>&1 | Out-Null

Write-Host ""
Write-Host "=== Tayyor ===" -ForegroundColor Cyan
Write-Host ("  https://github.com/" + $user + "/" + $RepoName) -ForegroundColor Green
Write-Host ""
