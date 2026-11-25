param(
    [string]$ModelPath = "$env:LOCALAPPDATA\ft-nioc-uploader\captcha-model\captcha_model.ftmodel",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedModel = (Resolve-Path -LiteralPath $ModelPath).Path
if ([IO.Path]::GetFileName($resolvedModel) -ne "captcha_model.ftmodel") {
    throw "ModelPath must point to captcha_model.ftmodel."
}
$driveRoot = [IO.Path]::GetPathRoot($projectRoot)
$workPath = Join-Path $projectRoot "build\ft-office-pyinstaller-work"
$distPath = Join-Path $projectRoot "dist"
$packagePath = Join-Path $distPath "FT-Office-Lite"
$archivePath = Join-Path $distPath "FT-Office-Lite.zip"

Set-Location $projectRoot

if (-not $SkipTests) {
    $env:PYTHONDONTWRITEBYTECODE = "1"
    python -m unittest `
        tests.test_captcha_inference `
        tests.test_captcha_lab `
        tests.test_ft_auto_uploader `
        tests.test_ft_browser `
        tests.test_ft_settings `
        tests.test_ft_pargar `
        -v
    if ($LASTEXITCODE -ne 0) {
        throw "Office tests failed."
    }
}

$env:FT_OFFICE_MODEL_PATH = $resolvedModel
python -m PyInstaller `
    --noconfirm `
    --clean `
    --workpath $workPath `
    --distpath $distPath `
    (Join-Path $projectRoot "FT-Office.spec")
if ($LASTEXITCODE -ne 0) {
    throw "Office build failed."
}

Copy-Item -LiteralPath (Join-Path $PSScriptRoot "Start-FT-Office.bat") -Destination $packagePath
Copy-Item -LiteralPath (Join-Path $projectRoot "README-OFFICE.md") -Destination $packagePath

$forbiddenNames = @(
    "tensorflow",
    "keras",
    "numpy",
    ".local-browsers",
    "chrome.exe"
)
$forbidden = Get-ChildItem -LiteralPath $packagePath -Recurse -Force |
    Where-Object { $forbiddenNames -contains $_.Name.ToLowerInvariant() }
if ($forbidden) {
    $found = ($forbidden | ForEach-Object FullName) -join "`r`n"
    throw "Forbidden heavy dependencies were found:`r`n$found"
}

$selfTest = Start-Process `
    -FilePath (Join-Path $packagePath "FT-Office.exe") `
    -ArgumentList "--self-test" `
    -WindowStyle Hidden `
    -Wait `
    -PassThru
if ($selfTest.ExitCode -ne 0) {
    throw "Office self-test failed."
}

if (Test-Path -LiteralPath $archivePath) {
    [IO.File]::Delete($archivePath)
}
Compress-Archive -LiteralPath $packagePath -DestinationPath $archivePath -CompressionLevel Optimal

$checksumPath = "$archivePath.sha256"
$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText(
    $checksumPath,
    "$archiveHash  $([IO.Path]::GetFileName($archivePath))`r`n",
    $utf8WithoutBom
)

$packageSize = (Get-ChildItem -LiteralPath $packagePath -Recurse -File |
    Measure-Object Length -Sum).Sum
$archiveSize = (Get-Item -LiteralPath $archivePath).Length
Write-Host ("Office folder: {0:N1} MB" -f ($packageSize / 1MB))
Write-Host ("Office archive: {0:N1} MB" -f ($archiveSize / 1MB))
Write-Host "Output: $archivePath"
Write-Host "SHA-256: $archiveHash"
