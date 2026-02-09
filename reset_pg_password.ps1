# PostgreSQL Password Reset Script
# This script temporarily enables trust authentication to reset the password

$PG_DATA = "C:\Program Files\PostgreSQL\18\data"
$PG_HBA = Join-Path $PG_DATA "pg_hba.conf"
$PG_HBA_BACKUP = Join-Path $PG_DATA "pg_hba.conf.backup"

# Prompt for a new password (won't echo)
$NEW_PASSWORD_SECURE = Read-Host "Enter new password for 'postgres' user" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($NEW_PASSWORD_SECURE)
try {
    $NEW_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

if (-not $NEW_PASSWORD) {
    Write-Host "[ERROR] Password cannot be empty" -ForegroundColor Red
    exit 1
}

Write-Host "PostgreSQL Password Reset Tool" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Check if running as Administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] This script requires Administrator privileges" -ForegroundColor Red
    Write-Host "Please run PowerShell as Administrator and try again" -ForegroundColor Yellow
    exit 1
}

Write-Host "[1/6] Backing up pg_hba.conf..." -ForegroundColor Yellow
Copy-Item -Path $PG_HBA -Destination $PG_HBA_BACKUP -Force
Write-Host "      Backup created at: $PG_HBA_BACKUP" -ForegroundColor Green

Write-Host "[2/6] Modifying authentication to 'trust'..." -ForegroundColor Yellow
$content = Get-Content $PG_HBA
$newContent = $content -replace "scram-sha-256", "trust"
$newContent = $newContent -replace "md5", "trust"
$newContent | Set-Content $PG_HBA -Encoding ASCII
Write-Host "      Authentication changed to trust mode" -ForegroundColor Green

Write-Host "[3/6] Restarting PostgreSQL service..." -ForegroundColor Yellow
Restart-Service postgresql-x64-18
Start-Sleep -Seconds 3
Write-Host "      Service restarted" -ForegroundColor Green

Write-Host "[4/6] Resetting postgres user password..." -ForegroundColor Yellow
$env:PGPASSWORD = ""
$sqlPassword = $NEW_PASSWORD.Replace("'", "''")
$resetCmd = "ALTER USER postgres WITH PASSWORD '$sqlPassword';"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -d postgres -c $resetCmd 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "      Password reset successfully" -ForegroundColor Green
} else {
    Write-Host "      [WARNING] Password reset may have failed" -ForegroundColor Yellow
}

Write-Host "[5/6] Restoring pg_hba.conf..." -ForegroundColor Yellow
Copy-Item -Path $PG_HBA_BACKUP -Destination $PG_HBA -Force
Write-Host "      Original authentication restored" -ForegroundColor Green

Write-Host "[6/6] Restarting PostgreSQL service..." -ForegroundColor Yellow
Restart-Service postgresql-x64-18
Start-Sleep -Seconds 3
Write-Host "      Service restarted" -ForegroundColor Green

Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
Write-Host "[OK] Password reset complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Next: update env.local with your new password:" -ForegroundColor Yellow
Write-Host "DATABASE_URL=postgresql://postgres:<your_password>@localhost:5432/applicant_portal" -ForegroundColor White
Write-Host ""
