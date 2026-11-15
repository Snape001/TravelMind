param(
    [Parameter(Mandatory)]
    [ValidateSet('Setup', 'Start', 'Status', 'Import', 'Job', 'Stop', 'Read', 'ZhihuLogin', 'ZhihuCollect')]
    [string]$Action,
    [string]$Root = (Join-Path $env:LOCALAPPDATA 'TravelMind\social-research'),
    [ValidateRange(1024, 65535)]
    [int]$Port = 18764,
    [guid]$BatchId = [guid]::Empty,
    [ValidateSet('xiaohongshu', 'bilibili', 'weibo', 'douyin', 'zhihu')]
    [string]$Platform = 'xiaohongshu',
    [string]$Keyword,
    [string]$CollectedAt,
    [ValidateSet('mediacrawler', 'normalized')]
    [string]$InputFormat = 'mediacrawler',
    [ValidateRange(1, 15)]
    [int]$Limit = 15,
    [ValidateRange(0, 10000)]
    [int]$Offset = 0,
    [string]$JsonlPath,
    [switch]$InstallBrowser,
    [ValidateSet('search', 'answers', 'owned')]
    [string]$ZhihuMode = 'search',
    [string]$SourceUrl
)

$ErrorActionPreference = 'Stop'
$upstream = 'https://github.com/NanmiCoder/MediaCrawler.git'
$commit = '306ab4ca26c0e44fb87ede0e40e98748752815a6'
$rootPath = [System.IO.Path]::GetFullPath($Root)
$sourcePath = Join-Path $rootPath 'MediaCrawler'
$dataPath = Join-Path $rootPath 'data'
$venvPath = Join-Path $rootPath 'sidecar-venv'
$backend = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'backend'

function Invoke-Checked {
    param([string]$Command, [string[]]$ArgumentList)
    & $Command @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Command exited with code $LASTEXITCODE"
    }
}

if ($Action -eq 'Setup') {
    New-Item -ItemType Directory -Path $rootPath, (Join-Path $dataPath 'inbox') -Force | Out-Null
    if (!(Test-Path -LiteralPath $sourcePath)) {
        Invoke-Checked git @('init', $sourcePath)
        Invoke-Checked git @('-C', $sourcePath, 'remote', 'add', 'origin', $upstream)
    }
    $origin = Invoke-Checked git @('-C', $sourcePath, 'remote', 'get-url', 'origin')
    if ($origin -ne $upstream) { throw 'Unexpected MediaCrawler upstream' }
    $head = & git -C $sourcePath rev-parse --verify -q HEAD
    if ($head -and $head -ne $commit) { throw "Unexpected MediaCrawler commit: $head" }
    if (!$head) {
        Invoke-Checked git @('-C', $sourcePath, 'fetch', '--depth', '1', 'origin', $commit)
        Invoke-Checked git @('-C', $sourcePath, 'checkout', '--detach', $commit)
    }
    if ((Invoke-Checked git @('-C', $sourcePath, 'status', '--porcelain', '--untracked-files=no'))) {
        throw 'MediaCrawler checkout has tracked changes'
    }
    $env:UV_CACHE_DIR = Join-Path $rootPath 'uv-cache'
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $sourcePath '.venv'
    Invoke-Checked uv @('sync', '--project', $sourcePath, '--locked', '--no-dev', '--python', '3.11')
    $env:UV_PROJECT_ENVIRONMENT = $venvPath
    Invoke-Checked uv @('sync', '--project', $PSScriptRoot, '--locked', '--no-dev', '--python', '3.11')
    if ($InstallBrowser) {
        $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $rootPath 'browsers'
        Invoke-Checked (Join-Path $sourcePath '.venv\Scripts\python.exe') @(
            '-m', 'playwright', 'install', 'chromium'
        )
    }
    Write-Output "MediaCrawler $commit"
    Write-Output "Data directory $dataPath"
    return
}

if ($Action -eq 'ZhihuLogin') {
    $programDirs = @(
        [Environment]::GetFolderPath('ProgramFiles'),
        [Environment]::GetFolderPath('ProgramFilesX86'),
        [Environment]::GetFolderPath('LocalApplicationData')
    ) | Where-Object { $_ } | Select-Object -Unique
    $browser = @(
        foreach ($directory in $programDirs) {
            Join-Path $directory 'Microsoft\Edge\Application\msedge.exe'
            Join-Path $directory 'Google\Chrome\Application\chrome.exe'
        }
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (!$browser) { throw 'Install Microsoft Edge or Google Chrome to log in manually.' }
    $localRoot = [Environment]::GetFolderPath('LocalApplicationData')
    if (!$localRoot) { throw 'The current Windows account has no local app data directory.' }
    $profile = Join-Path $localRoot 'TravelMind\social-research\browser_data\zhihu'
    New-Item -Path $profile -ItemType Directory -Force | Out-Null
    Start-Process -FilePath $browser -ArgumentList @(
        ('--user-data-dir="{0}"' -f $profile), 'https://www.zhihu.com/signin'
    )
    return
}

if (!$env:SOCIAL_RESEARCH_TOKEN) { throw 'Set SOCIAL_RESEARCH_TOKEN in this shell.' }
if ($Action -eq 'Start') {
    $env:SOCIAL_DATA_PATH = $dataPath
    Invoke-Checked (Join-Path $venvPath 'Scripts\python.exe') @(
        '-m', 'uvicorn', '--app-dir', $backend,
        'travelmind.research.social.service:create_app_from_environment',
        '--factory', '--host', '127.0.0.1', '--port', $Port, '--workers', '1', '--no-access-log'
    )
    return
}

$baseUri = "http://127.0.0.1:$Port/v1"
$headers = @{Authorization = "Bearer $env:SOCIAL_RESEARCH_TOKEN"}
if ($Action -eq 'Status') { Invoke-RestMethod "$baseUri/status" -Headers $headers; return }
if ($Action -in @('Import', 'Job', 'Stop') -and $BatchId -eq [guid]::Empty) {
    throw 'Provide BatchId.'
}
if ($Action -eq 'Job') { Invoke-RestMethod "$baseUri/jobs/$BatchId" -Headers $headers; return }
if ($Action -eq 'Stop') {
    Invoke-RestMethod "$baseUri/jobs/$BatchId" -Headers $headers -Method Delete
    return
}
if (!$Keyword) { throw 'Provide Keyword.' }
if ($Action -eq 'ZhihuCollect') {
    if (!$env:ZHIHU_ACCESS_SECRET) { throw 'Set ZHIHU_ACCESS_SECRET locally in this shell.' }
    if ($ZhihuMode -ne 'search' -and !$SourceUrl) { throw 'Provide SourceUrl.' }
    $null = Invoke-RestMethod "$baseUri/status" -Headers $headers
    $cliArgs = @(
        '-m', 'travelmind.research.social.zhihu_api',
        '--data', $dataPath, "--keyword=$Keyword", '--mode', $ZhihuMode
    )
    if ($SourceUrl) { $cliArgs += @('--url', $SourceUrl) }
    Push-Location $backend
    try {
        $body = Invoke-Checked (Join-Path $venvPath 'Scripts\python.exe') $cliArgs
    } finally {
        Pop-Location
    }
    $arguments = @{
        Uri = "$baseUri/jobs"
        Method = 'Post'
        Headers = $headers
        ContentType = 'application/json; charset=utf-8'
        Body = [Text.Encoding]::UTF8.GetBytes($body)
    }
    Invoke-RestMethod @arguments
    return
}
if ($Action -eq 'Import') {
    if (!$CollectedAt) { throw 'Provide the actual CollectedAt in ISO 8601 with a time zone.' }
    $body = @{
        batch_id = "$BatchId"
        platform = $Platform
        keyword = $Keyword
        collected_at = $CollectedAt
        format = $InputFormat
    } | ConvertTo-Json -Compress
    $arguments = @{
        Uri = "$baseUri/jobs"
        Method = 'Post'
        Headers = $headers
        ContentType = 'application/json; charset=utf-8'
        Body = [Text.Encoding]::UTF8.GetBytes($body)
    }
    Invoke-RestMethod @arguments
    return
}

$query = ('platform={0}&keyword={1}&limit={2}&offset={3}' -f
    $Platform, [Uri]::EscapeDataString($Keyword), $Limit, $Offset)
if ($JsonlPath) {
    $arguments = @{
        Uri = "$baseUri/evidence?$query&format=jsonl"
        Headers = $headers
        OutFile = $JsonlPath
        UseBasicParsing = $true
    }
    Invoke-WebRequest @arguments
} else {
    Invoke-RestMethod "$baseUri/evidence?$query" -Headers $headers
}
