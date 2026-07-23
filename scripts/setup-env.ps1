[CmdletBinding()]
param([switch]$Force)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root "backend\.env"

if ((Test-Path -LiteralPath $envPath) -and -not $Force) {
    Write-Host "[env] Existing backend/.env preserved."
    return
}

$bytes = New-Object byte[] 48
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $rng.GetBytes($bytes)
} finally {
    $rng.Dispose()
}
$jwtSecret = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")

$content = @"
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=https://api.openai.com/v1
QWEN_API_KEY=
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
DB_HOST=postgres
DB_PORT=5432
DB_NAME=chestx_agent
DB_USER=chestx_admin
DB_PASSWORD=chestx_admin
REDIS_HOST=redis
REDIS_PORT=6379
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=chestx-agent-images
MINIO_SECURE=false
JWT_SECRET_KEY=$jwtSecret
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
DEFAULT_ADMIN_USERNAME=admin
DEFAULT_ADMIN_PASSWORD=admin123
DEFAULT_ADMIN_EMAIL=admin@chestvision.local
EMAIL_VERIFICATION_REQUIRED=true
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_EMAIL=
SMTP_FROM_NAME=ChestVision
SMTP_USE_SSL=true
SMTP_USE_STARTTLS=false
SMTP_TIMEOUT_SECONDS=15
EMAIL_CODE_EXPIRE_MINUTES=10
EMAIL_CODE_RESEND_SECONDS=60
EMAIL_CODE_MAX_ATTEMPTS=5
EMAIL_CODE_MAX_PER_EMAIL_PER_HOUR=5
EMAIL_CODE_MAX_PER_IP_PER_HOUR=20
APP_NAME=ChestVision
APP_VERSION=0.1.0
DEBUG=false
LOG_LEVEL=INFO
ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
"@

[IO.File]::WriteAllText($envPath, $content, (New-Object Text.UTF8Encoding($false)))
Write-Host "[env] Created backend/.env with a random JWT secret."
