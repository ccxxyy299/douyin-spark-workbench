<#
.SYNOPSIS
    一键启动开发环境（后端 + 前端）。

.DESCRIPTION
    会开两个窗口：
    * 后端 uvicorn  :8766
    * 前端 vite dev  :5173

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\dev.ps1
#>

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$server = Join-Path $root 'server'
$web = Join-Path $root 'web'

Write-Host "==> 检查 Python 依赖" -ForegroundColor Cyan
Push-Location $server
if (-not (Test-Path '.venv')) {
    Write-Host "    创建虚拟环境 .venv" -ForegroundColor Yellow
    python -m venv .venv
}
& "$server\.venv\Scripts\python.exe" -c "import fastapi" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "    安装后端依赖" -ForegroundColor Yellow
    & "$server\.venv\Scripts\pip.exe" install -r requirements.txt
    & "$server\.venv\Scripts\python.exe" -m playwright install chromium
}
Pop-Location

Write-Host "==> 检查前端依赖" -ForegroundColor Cyan
Push-Location $web
if (-not (Test-Path 'node_modules')) {
    Write-Host "    npm install" -ForegroundColor Yellow
    npm install
}
Pop-Location

Write-Host "==> 启动后端 :8766" -ForegroundColor Green
Start-Process -FilePath 'powershell' -ArgumentList @(
    '-NoExit', '-Command',
    "cd '$server'; & '$server\.venv\Scripts\python.exe' -m uvicorn app.main:app --port 8766 --reload"
)

Start-Sleep -Seconds 3

Write-Host "==> 启动前端 :5173" -ForegroundColor Green
Start-Process -FilePath 'powershell' -ArgumentList @(
    '-NoExit', '-Command',
    "cd '$web'; npm run dev"
)

Write-Host ""
Write-Host "前端: http://localhost:5173" -ForegroundColor Cyan
Write-Host "接口: http://127.0.0.1:8766/docs" -ForegroundColor Cyan
