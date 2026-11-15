# Run from any working directory with Windows PowerShell 5.1 or PowerShell 7.
[CmdletBinding()]
param(
    [ValidateSet('init', 'start', 'stop', 'status', 'check', 'restart-worker')]
    [string]$Action = 'start',
    [string]$MkcertPath = 'mkcert'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$state = Join-Path $repo '.artifacts/local-oidc'
$envFile = Join-Path $state '.env'
$pki = Join-Path $state 'pki'
$caHome = Join-Path $state 'mkcert-ca'
$app = 'https://travelmind.localhost:18443'
$issuer = 'https://id.travelmind.localhost:18443/realms/travelmind-dev'
$composeArgs = @(
    'compose', '-p', 'travelmind-local-oidc',
    '--env-file', $envFile, '-f', (Join-Path $PSScriptRoot 'compose.yaml')
)

function New-LocalSecret {
    $bytes = New-Object byte[] 24
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    return [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
}

function Initialize-LocalState {
    $mkcert = Get-Command $MkcertPath -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $mkcert -and $MkcertPath -eq 'mkcert') {
        $portable = Join-Path $state 'tools/mkcert.exe'
        $mkcert = Get-Command $portable -CommandType Application -ErrorAction SilentlyContinue
    }
    if (-not $mkcert) {
        throw 'mkcert is required. Install it or pass -MkcertPath with the executable path.'
    }
    New-Item -ItemType Directory -Force -Path $state, $pki, $caHome | Out-Null
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
        $names = @('POSTGRES_PASSWORD', 'CLIENT_SECRET', 'ALICE_PASSWORD', 'BOB_PASSWORD')
        $lines = foreach ($name in $names) { "TM_LOCAL_$name=$(New-LocalSecret)" }
        [IO.File]::WriteAllLines($envFile, [string[]]$lines, [Text.UTF8Encoding]::new($false))
    }
    $root = Join-Path $caHome 'rootCA.pem'
    $certificate = Join-Path $pki 'site.pem'
    $privateKey = Join-Path $pki 'site-key.pem'
    $needsLeaf = -not (Test-Path -LiteralPath $root -PathType Leaf) -or
        -not (Test-Path -LiteralPath $certificate -PathType Leaf) -or
        -not (Test-Path -LiteralPath $privateKey -PathType Leaf)
    $previousRoot = $env:CAROOT
    $previousStores = $env:TRUST_STORES
    try {
        $env:CAROOT = $caHome
        $env:TRUST_STORES = 'system'
        & $mkcert.Source -install
        if ($LASTEXITCODE -ne 0) { throw 'mkcert could not install the local trust root.' }
        if ($needsLeaf) {
            & $mkcert.Source -cert-file $certificate -key-file $privateKey travelmind.localhost id.travelmind.localhost
            if ($LASTEXITCODE -ne 0) { throw 'mkcert could not issue the local HTTPS certificate.' }
        }
        Copy-Item -LiteralPath $root -Destination (Join-Path $pki 'rootCA.pem') -Force
    } finally {
        $env:CAROOT = $previousRoot
        $env:TRUST_STORES = $previousStores
    }
    Write-Host "Local test credentials are stored in $envFile"
}

function Invoke-Compose {
    & docker @composeArgs @args
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed for '$($args[0])'." }
}

function Read-LocalHttps([string]$Url, [switch]$Redirect) {
    $target = [uri]$Url
    $arguments = @(
        '--silent', '--show-error', '--fail', '--noproxy', '*',
        # mkcert's local CA has no revocation endpoint. Schannel still checks hostname and trust.
        '--ssl-revoke-best-effort',
        '--connect-timeout', '3', '--max-time', '12',
        '--resolve', "$($target.Host):$($target.Port):127.0.0.1"
    )
    if ($Redirect) {
        $arguments += @('--output', 'NUL', '--write-out', '%{redirect_url}')
    }
    $response = & curl.exe @arguments $Url
    if ($LASTEXITCODE -ne 0) { throw "HTTPS check failed for $Url." }
    return ($response -join "`n")
}

function Test-LocalStack {
    $discoveryUrl = "$issuer/.well-known/openid-configuration"
    $endpoint = "$issuer/protocol/openid-connect"
    $metadata = Read-LocalHttps $discoveryUrl | ConvertFrom-Json
    if ($metadata.issuer -cne $issuer -or
        $metadata.authorization_endpoint -cne "$endpoint/auth" -or
        $metadata.token_endpoint -cne "$endpoint/token" -or
        $metadata.jwks_uri -cne "$endpoint/certs" -or
        'S256' -notin $metadata.code_challenge_methods_supported) {
        throw 'Keycloak discovery advertises unexpected issuer, endpoints or PKCE.'
    }
    $jwks = Read-LocalHttps $metadata.jwks_uri | ConvertFrom-Json
    if (-not $jwks.keys) { throw 'Keycloak JWKS does not contain signing keys.' }
    $null = Read-LocalHttps "$app/"
    $null = Read-LocalHttps "$app/health/ready"
    if ('worker' -notin @(Invoke-Compose ps --status running --services worker)) {
        throw 'Worker is not running. Check the worker logs and the provider keys in backend/.env.'
    }
    $login = [uri](Read-LocalHttps "$app/api/v1/auth/login" -Redirect)
    $query = [uri]::UnescapeDataString($login.Query)
    if ($login.GetLeftPart([UriPartial]::Path) -cne $metadata.authorization_endpoint -or
        $query -notmatch '([?&])code_challenge_method=S256(&|$)' -or
        $query -notmatch "([?&])redirect_uri=$([regex]::Escape($app + '/api/v1/auth/callback'))(&|$)") {
        throw 'FastAPI generated an unexpected authorization redirect.'
    }
    Write-Host "Ready at $app"
}

switch ($Action) {
    'init' { Initialize-LocalState }
    'start' {
        if (-not (Test-Path -LiteralPath (Join-Path $repo 'backend/.env') -PathType Leaf)) {
            throw 'Configure backend/.env using backend/.env.example before starting research.'
        }
        Initialize-LocalState
        Invoke-Compose config --quiet
        Invoke-Compose build migrate frontend
        Invoke-Compose up -d
        $deadline = (Get-Date).AddMinutes(3)
        while ($true) {
            try {
                Test-LocalStack
                break
            } catch {
                if ((Get-Date) -ge $deadline) {
                    throw
                }
                Start-Sleep -Seconds 3
            }
        }
    }
    'stop' { Invoke-Compose down }
    'status' { Invoke-Compose ps -a }
    'check' { Test-LocalStack }
    'restart-worker' { Invoke-Compose restart worker }
}
