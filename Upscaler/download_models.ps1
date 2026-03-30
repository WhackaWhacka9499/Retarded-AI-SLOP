#!/usr/bin/env pwsh
# Download all ncnn custom models for VTF Upscaler
# Sources: upscayl/custom-models (GitHub) + TNTwise/Universal-NCNN-Upscaler (GitHub releases)

$dest = "c:\Users\Alexander Jarvis\Desktop\Upscaler\custom_models"

# Upscayl custom models (direct raw.githubusercontent.com URLs)
$base = "https://raw.githubusercontent.com/upscayl/custom-models/main/models"
$upscaylModels = @(
    "4xHFA2k",
    "4xLSDIR",
    "4xLSDIRCompactC3",
    "4xLSDIRplusC",
    "4xNomos8kSC",
    "4x_NMKD-Siax_200k",
    "4x_NMKD-Superscale-SP_178000_G",
    "RealESRGAN_General_WDN_x4_v3",
    "RealESRGAN_General_x4_v3",
    "uniscale_restore",
    "unknown-2.0.1"
)

$jobs = @()
foreach ($model in $upscaylModels) {
    foreach ($ext in @(".bin", ".param")) {
        $url = "$base/$model$ext"
        $out = Join-Path $dest "$model$ext"
        if (Test-Path $out) {
            Write-Host "  SKIP $model$ext (exists)" -ForegroundColor DarkGray
            continue
        }
        Write-Host "  GET  $model$ext" -ForegroundColor Cyan
        $jobs += Start-Job -ScriptBlock {
            param($u, $o)
            [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
            (New-Object System.Net.WebClient).DownloadFile($u, $o)
        } -ArgumentList $url, $out
    }
}

# TNTwise ClearReality4x (zip release - unique model not in Upscayl)
$clearRealityZip = Join-Path $env:TEMP "ClearReality4x.zip"
$clearRealityUrl = "https://github.com/TNTwise/Universal-NCNN-Upscaler/releases/download/Realistic/ClearReality4x.zip"
if (-not (Test-Path (Join-Path $dest "ClearReality4x.bin"))) {
    Write-Host "  GET  ClearReality4x.zip" -ForegroundColor Cyan
    $jobs += Start-Job -ScriptBlock {
        param($u, $z, $d)
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
        (New-Object System.Net.WebClient).DownloadFile($u, $z)
        Expand-Archive -Path $z -DestinationPath $d -Force
        Remove-Item $z -Force
    } -ArgumentList $clearRealityUrl, $clearRealityZip, $dest
} else {
    Write-Host "  SKIP ClearReality4x (exists)" -ForegroundColor DarkGray
}

# TNTwise 4xLSDIRPlus (not in Upscayl - has different params)
$lsdirPlusZip = Join-Path $env:TEMP "4xLSDIRPlus.zip"
$lsdirPlusUrl = "https://github.com/TNTwise/Universal-NCNN-Upscaler/releases/download/Realistic/4xLSDIRPlus.zip"
if (-not (Test-Path (Join-Path $dest "4xLSDIRPlus.bin"))) {
    Write-Host "  GET  4xLSDIRPlus.zip" -ForegroundColor Cyan
    $jobs += Start-Job -ScriptBlock {
        param($u, $z, $d)
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
        (New-Object System.Net.WebClient).DownloadFile($u, $z)
        Expand-Archive -Path $z -DestinationPath $d -Force
        Remove-Item $z -Force
    } -ArgumentList $lsdirPlusUrl, $lsdirPlusZip, $dest
} else {
    Write-Host "  SKIP 4xLSDIRPlus (exists)" -ForegroundColor DarkGray
}

# TNTwise 4xLSDIRPlusN (denoising variant)
$lsdirPlusNZip = Join-Path $env:TEMP "4xLSDIRPlusN.zip"
$lsdirPlusNUrl = "https://github.com/TNTwise/Universal-NCNN-Upscaler/releases/download/Realistic/4xLSDIRPlusN.zip"
if (-not (Test-Path (Join-Path $dest "4xLSDIRPlusN.bin"))) {
    Write-Host "  GET  4xLSDIRPlusN.zip" -ForegroundColor Cyan
    $jobs += Start-Job -ScriptBlock {
        param($u, $z, $d)
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
        (New-Object System.Net.WebClient).DownloadFile($u, $z)
        Expand-Archive -Path $z -DestinationPath $d -Force
        Remove-Item $z -Force
    } -ArgumentList $lsdirPlusNUrl, $lsdirPlusNZip, $dest
} else {
    Write-Host "  SKIP 4xLSDIRPlusN (exists)" -ForegroundColor DarkGray
}

# TNTwise 4xLSDIRPlusR (restoration variant)
$lsdirPlusRZip = Join-Path $env:TEMP "4xLSDIRPlusR.zip"
$lsdirPlusRUrl = "https://github.com/TNTwise/Universal-NCNN-Upscaler/releases/download/Realistic/4xLSDIRPlusR.zip"
if (-not (Test-Path (Join-Path $dest "4xLSDIRPlusR.bin"))) {
    Write-Host "  GET  4xLSDIRPlusR.zip" -ForegroundColor Cyan
    $jobs += Start-Job -ScriptBlock {
        param($u, $z, $d)
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
        (New-Object System.Net.WebClient).DownloadFile($u, $z)
        Expand-Archive -Path $z -DestinationPath $d -Force
        Remove-Item $z -Force
    } -ArgumentList $lsdirPlusRUrl, $lsdirPlusRZip, $dest
} else {
    Write-Host "  SKIP 4xLSDIRPlusR (exists)" -ForegroundColor DarkGray
}

Write-Host "`n  Downloading $($jobs.Count) files in parallel..." -ForegroundColor Yellow
$jobs | Wait-Job | Out-Null

$failed = @()
foreach ($j in $jobs) {
    if ($j.State -eq 'Failed') {
        $failed += $j
        Write-Host "  FAIL $($j.Name): $($j | Receive-Job 2>&1)" -ForegroundColor Red
    }
    Remove-Job $j -Force
}

# Count results
$modelFiles = Get-ChildItem -Path $dest -Filter "*.param" | Where-Object {
    (Test-Path ($_.FullName -replace '\.param$', '.bin'))
}
Write-Host "`n=== Done! $($modelFiles.Count) custom models installed ===" -ForegroundColor Green
foreach ($m in $modelFiles | Sort-Object Name) {
    $binSize = (Get-Item ($m.FullName -replace '\.param$', '.bin')).Length / 1MB
    Write-Host "  ⭐ $($m.BaseName) ($([math]::Round($binSize,1)) MB)" -ForegroundColor Green
}
