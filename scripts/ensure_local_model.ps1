param(
    [string]$Model = $env:AGRI_OLLAMA_MODEL,
    [string]$BaseUrl = $env:AGRI_OLLAMA_BASE_URL,
    [int]$StartupTimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"
if (-not $Model) { $Model = "qwen2.5:3b" }
if (-not $BaseUrl) { $BaseUrl = "http://127.0.0.1:11434" }
$BaseUrl = $BaseUrl.TrimEnd("/")

$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollama) {
    $ollamaExe = $ollama.Source
}
else {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"),
        (Join-Path $env:ProgramFiles "Ollama\ollama.exe")
    )
    $ollamaExe = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

if (-not $ollamaExe) {
    Write-Error "Ollama executable was not found."
    exit 2
}

function Get-OllamaTags {
    try {
        return Invoke-RestMethod -Uri "$BaseUrl/api/tags" -TimeoutSec 3
    }
    catch {
        return $null
    }
}

$tags = Get-OllamaTags
if (-not $tags) {
    Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 750
        $tags = Get-OllamaTags
    } until ($tags -or (Get-Date) -ge $deadline)
}

if (-not $tags) {
    Write-Error "Ollama API did not become ready at $BaseUrl."
    exit 3
}

$installed = @($tags.models | ForEach-Object {
    if ($_.model) { $_.model } elseif ($_.name) { $_.name }
})

if ($installed -notcontains $Model) {
    Write-Output "MODEL_PULL_START=$Model"
    & $ollamaExe pull $Model
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Model pull failed: $Model"
        exit 4
    }
}

$payload = @{
    model = $Model
    messages = @(
        @{ role = "system"; content = "只根据用户提供的证据，用一句中文回答。" }
        @{ role = "user"; content = "证据：夏季累计降水 448.97 mm。" }
    )
    stream = $false
    keep_alive = "10m"
    options = @{ temperature = 0; seed = 51; num_predict = 64 }
} | ConvertTo-Json -Depth 8

$reply = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/chat" `
    -ContentType "application/json; charset=utf-8" -Body $payload -TimeoutSec 120
if (-not $reply.message.content) {
    Write-Error "Ollama returned an empty smoke-test response."
    exit 5
}

Write-Output "OLLAMA_READY=1"
Write-Output "OLLAMA_EXE=$ollamaExe"
Write-Output "OLLAMA_BASE_URL=$BaseUrl"
Write-Output "OLLAMA_MODEL=$Model"
Write-Output "OLLAMA_SMOKE=$($reply.message.content -replace "`r|`n", " ")"

