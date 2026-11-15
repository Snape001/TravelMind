# Run from any working directory with Windows PowerShell 5.1 or PowerShell 7.
[CmdletBinding()]
param(
    [ValidateSet('init', 'start', 'stop', 'status', 'check', 'restart-worker', 'upgrade-auth')]
    [string]$Action = 'start',
    [string]$MkcertPath = 'mkcert',
    [string]$SocialCachePath
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
$composeProject = 'travelmind-local-oidc'
$composeArgs = @(
    'compose', '-p', $composeProject,
    '--env-file', $envFile, '-f', (Join-Path $PSScriptRoot 'compose.yaml')
)
if ($SocialCachePath) {
    $cache = Get-Item -LiteralPath $SocialCachePath
    if ($cache.PSIsContainer) { throw 'SocialCachePath must point to the authorized SQLite file.' }
    $env:SOCIAL_RESEARCH_HOST_CACHE = $cache.FullName
    $composeArgs += @('-f', (Join-Path $PSScriptRoot 'compose.social.yaml'))
}

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

function Invoke-RealmAdmin {
    param([string[]]$Arguments, [object]$Body = $null)

    $command = @('exec', '-T', 'keycloak', '/opt/keycloak/bin/kcadm.sh') +
        $Arguments + @('--config', $adminConfig)
    if ($null -eq $Body) {
        & docker @composeArgs @command
    } else {
        $json = ConvertTo-Json -InputObject $Body -Depth 20 -Compress
        $json | & docker @composeArgs @command -f -
    }
    if ($LASTEXITCODE -ne 0) { throw "Keycloak Admin CLI failed on '$($Arguments[0])'." }
}

function Update-LocalRealm {
    $template = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'realm.json') -Raw | ConvertFrom-Json
    $realm = $template.realm
    $client = $template.clients[0]
    $flows = Invoke-RealmAdmin @('get', 'authentication/flows', '-r', $realm, '--fields', 'alias') |
        ConvertFrom-Json
    if ($template.resetCredentialsFlow -notin @($flows | ForEach-Object { $_.alias })) {
        Invoke-RealmAdmin @('create', 'authentication/flows/reset%20credentials/copy', '-r', $realm) @{
            newName = $template.resetCredentialsFlow
        } | Out-Null
    }
    $executions = Invoke-RealmAdmin @('get', "authentication/flows/$($template.resetCredentialsFlow)/executions", '-r', $realm) |
        ConvertFrom-Json
    $sendEmail = @($executions | Where-Object { $_.PSObject.Properties['providerId'] -and $_.providerId -eq 'reset-credential-email' })
    if ($sendEmail.Count -ne 1) { throw 'Keycloak reset flow must contain a single Send Reset Email execution.' }
    $execution = $sendEmail[0]
    if ($execution.PSObject.Properties['authenticationConfig'] -and $execution.authenticationConfig) {
        Invoke-RealmAdmin @('update', "authentication/config/$($execution.authenticationConfig)", '-r', $realm, '-s', 'config.force-login=true') | Out-Null
    } else {
        $argsForConfig = @('create', "authentication/executions/$($execution.id)/config", '-r', $realm)
        Invoke-RealmAdmin $argsForConfig $template.authenticatorConfig[0] | Out-Null
    }
    foreach ($action in @('VERIFY_EMAIL', 'UPDATE_PROFILE', 'UPDATE_PASSWORD', 'UPDATE_EMAIL')) {
        Invoke-RealmAdmin @('update', "authentication/required-actions/$action", '-r', $realm, '-s', 'enabled=true') | Out-Null
    }

    $properties = @(
        'registrationAllowed', 'registrationEmailAsUsername', 'loginWithEmailAllowed',
        'duplicateEmailsAllowed', 'verifyEmail', 'resetPasswordAllowed', 'passwordPolicy',
        'bruteForceProtected', 'failureFactor', 'waitIncrementSeconds', 'maxFailureWaitSeconds',
        'ssoSessionIdleTimeout', 'ssoSessionMaxLifespan', 'clientSessionIdleTimeout',
        'clientSessionMaxLifespan', 'actionTokenGeneratedByUserLifespan', 'smtpServer',
        'resetCredentialsFlow'
    )
    $currentRealm = Invoke-RealmAdmin @('get', "realms/$realm") | ConvertFrom-Json
    $realmPatch = @{ attributes = @{} }
    if ($currentRealm.PSObject.Properties['attributes'] -and $currentRealm.attributes) {
        foreach ($property in $currentRealm.attributes.PSObject.Properties) {
            $realmPatch.attributes[$property.Name] = $property.Value
        }
    }
    foreach ($property in $template.attributes.PSObject.Properties) {
        $realmPatch.attributes[$property.Name] = $property.Value
    }
    foreach ($property in $properties) { $realmPatch[$property] = $template.$property }
    Invoke-RealmAdmin @('update', "realms/$realm", '-n') $realmPatch | Out-Null

    $clients = @(Invoke-RealmAdmin @('get', 'clients', '-r', $realm, '-q', "clientId=$($client.clientId)") |
        ConvertFrom-Json | Where-Object { $_.clientId -ceq $client.clientId })
    if ($clients.Count -ne 1) { throw 'Could not uniquely identify the existing TravelMind OIDC client.' }
    $clientPatch = @{ attributes = @{}; frontchannelLogout = $false }
    if ($clients[0].PSObject.Properties['attributes'] -and $clients[0].attributes) {
        foreach ($attribute in $clients[0].attributes.PSObject.Properties) {
            $clientPatch.attributes[$attribute.Name] = $attribute.Value
        }
    }
    foreach ($attribute in $client.attributes.PSObject.Properties) {
        $clientPatch.attributes[$attribute.Name] = $attribute.Value
    }
    Invoke-RealmAdmin @('update', "clients/$($clients[0].id)", '-r', $realm, '-n') $clientPatch | Out-Null
}

function Upgrade-LocalIdentity {
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
        throw 'Run stack.ps1 start to import a new local Realm.'
    }
    $existing = @(Invoke-Compose ps -a --services keycloak)
    if ('keycloak' -notin $existing) {
        & docker volume inspect "${composeProject}_keycloak" --format '{{.Name}}' 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Run stack.ps1 start to import a new local Realm.' }
    }

    $nonce = [Guid]::NewGuid().ToString('N')
    $username = "travelmind-upgrade-$nonce"
    $adminConfig = "/tmp/$username.config"
    $env:TM_KEYCLOAK_UPGRADE_USERNAME = $username
    $env:TM_KEYCLOAK_UPGRADE_PASSWORD = New-LocalSecret
    try {
        if ('keycloak' -in $existing) { Invoke-Compose stop keycloak }
        try {
            $bootstrap = @('run', '--rm', '--no-deps', '-e', 'TM_KEYCLOAK_UPGRADE_PASSWORD', 'keycloak',
                'bootstrap-admin', 'user', '--username', $username, '--password:env', 'TM_KEYCLOAK_UPGRADE_PASSWORD', '--no-prompt')
            Invoke-Compose @bootstrap
        } finally {
            Invoke-Compose up -d keycloak mailpit
        }
        $deadline = (Get-Date).AddMinutes(2)
        $authenticate = '/opt/keycloak/bin/kcadm.sh config credentials --config "/tmp/$TM_KEYCLOAK_UPGRADE_USERNAME.config" --server http://127.0.0.1:8080 --realm master --user "$TM_KEYCLOAK_UPGRADE_USERNAME" --password "$TM_KEYCLOAK_UPGRADE_PASSWORD"'
        while ($true) {
            & docker @composeArgs exec -T -e TM_KEYCLOAK_UPGRADE_PASSWORD -e TM_KEYCLOAK_UPGRADE_USERNAME keycloak sh -ec $authenticate 2>$null
            if ($LASTEXITCODE -eq 0) { break }
            if ((Get-Date) -ge $deadline) { throw 'Could not authenticate to the private Keycloak Admin API.' }
            Start-Sleep -Seconds 2
        }
        try {
            Update-LocalRealm
            Write-Host 'Upgraded the local Realm and client in place.'
        } finally {
            try {
                $users = @(Invoke-RealmAdmin @('get', 'users', '-r', 'master', '-q', "username=$username", '--fields', 'id,username') |
                    ConvertFrom-Json | Where-Object { $_.username -ceq $username })
                if ($users.Count -ne 1) { throw 'Could not identify the temporary upgrade administrator.' }
                Invoke-RealmAdmin @('delete', "users/$($users[0].id)", '-r', 'master') | Out-Null
            } finally {
                Invoke-Compose exec -T keycloak rm -f $adminConfig
            }
        }
    } finally {
        Remove-Item Env:TM_KEYCLOAK_UPGRADE_USERNAME, Env:TM_KEYCLOAK_UPGRADE_PASSWORD -ErrorAction SilentlyContinue
    }
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
    if ($LASTEXITCODE -ne 0) { throw "HTTPS check failed for $($target.GetLeftPart([UriPartial]::Path))." }
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
    $registration = [uri](Read-LocalHttps "$app/api/v1/auth/register" -Redirect)
    if ($registration.GetLeftPart([UriPartial]::Path) -cne $metadata.authorization_endpoint -or
        $registration.Query -notmatch '([?&])prompt=create(&|$)') {
        throw 'FastAPI generated an unexpected registration redirect.'
    }
    try {
        $null = Read-LocalHttps $registration.AbsoluteUri
    } catch {
        throw 'Keycloak rejected registration. For an existing development Realm, run stack.ps1 upgrade-auth.'
    }
    & curl.exe --silent --show-error --fail --noproxy '*' --output NUL --max-time 4 http://127.0.0.1:18025/api/v1/messages
    if ($LASTEXITCODE -ne 0) { throw 'The local Mailpit inbox is unavailable.' }
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
    'upgrade-auth' { Upgrade-LocalIdentity }
}
