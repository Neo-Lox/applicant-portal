# PowerShell setup script for Applicant Portal

Write-Host "Applicant Portal Setup" -ForegroundColor Green
Write-Host "=====================" -ForegroundColor Green
Write-Host ""

# Check Python
Write-Host "Checking Python installation..." -ForegroundColor Yellow
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd) {
    $pythonVersion = python --version 2>&1
    Write-Host "✓ Found: $pythonVersion" -ForegroundColor Green
} else {
    Write-Host "✗ Python not found. Please install Python 3.8+ from python.org" -ForegroundColor Red
    exit 1
}

# Create virtual environment
Write-Host ""
Write-Host "Setting up virtual environment..." -ForegroundColor Yellow
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    Write-Host "✓ Virtual environment created" -ForegroundColor Green
} else {
    Write-Host "✓ Virtual environment already exists" -ForegroundColor Green
}

# Activate and install dependencies
Write-Host ""
Write-Host "Installing dependencies..." -ForegroundColor Yellow
& .\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
Write-Host "✓ Dependencies installed" -ForegroundColor Green

# Check PostgreSQL
Write-Host ""
Write-Host "Checking PostgreSQL..." -ForegroundColor Yellow
$psqlCmd = Get-Command psql -ErrorAction SilentlyContinue
if ($psqlCmd) {
    $psqlVersion = psql --version 2>&1
    Write-Host "✓ Found: $psqlVersion" -ForegroundColor Green
    Write-Host ""
    Write-Host "To set up database, run:" -ForegroundColor Cyan
    Write-Host "  createdb applicant_portal" -ForegroundColor White
    Write-Host "  psql -d applicant_portal -f migrations/001_create_magic_link_tokens.sql" -ForegroundColor White
    Write-Host "  psql -d applicant_portal -f migrations/002_create_mvp_schema.sql" -ForegroundColor White
} else {
    Write-Host "⚠ PostgreSQL client not found. You can:" -ForegroundColor Yellow
    Write-Host "  1. Install PostgreSQL and add psql to PATH" -ForegroundColor White
    Write-Host "  2. Use SQLite for development (set DATABASE_URL or leave unset)" -ForegroundColor White
}

# Set environment variables
Write-Host ""
Write-Host "Setting environment variables..." -ForegroundColor Yellow
$env:FLASK_APP = "wsgi.py"
$env:FLASK_ENV = "development"
if (-not $env:SECRET_KEY) {
    $env:SECRET_KEY = "dev-secret-change-me-$(Get-Random)"
    Write-Host "  SECRET_KEY set to random dev value" -ForegroundColor Cyan
}
if (-not $env:MAGIC_LINK_HMAC_SECRET) {
    $env:MAGIC_LINK_HMAC_SECRET = "dev-hmac-change-me-$(Get-Random)"
    Write-Host "  MAGIC_LINK_HMAC_SECRET set to random dev value" -ForegroundColor Cyan
}
if (-not $env:DATABASE_URL) {
    Write-Host "  DATABASE_URL not set (will use SQLite or default)" -ForegroundColor Cyan
} else {
    Write-Host "  DATABASE_URL is set" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "✓ Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Set up database (see above)" -ForegroundColor White
Write-Host "  2. Run: flask seed-mvp" -ForegroundColor White
Write-Host "  3. Run: flask run" -ForegroundColor White
Write-Host ""
Write-Host "The app will be available at http://127.0.0.1:5000/" -ForegroundColor Cyan
