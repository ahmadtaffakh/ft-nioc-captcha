param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$driveRoot = [IO.Path]::GetPathRoot($projectRoot)
$workPath = Join-Path $driveRoot "ft-captcha-pyinstaller-work"
$distPath = Join-Path $projectRoot "dist"
$packagePath = Join-Path $distPath "FT-Captcha-Portable"
$archivePath = Join-Path $distPath "FT-Captcha-Portable.zip"

Set-Location $projectRoot

if (-not $SkipTests) {
    $env:PYTHONDONTWRITEBYTECODE = "1"
    python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed."
    }
}

# Store the headed Chromium build inside Playwright so PyInstaller can collect it.
$env:PLAYWRIGHT_BROWSERS_PATH = "0"
python -m playwright install chromium --no-shell
if ($LASTEXITCODE -ne 0) {
    throw "Chromium installation failed."
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --workpath $workPath `
    --distpath $distPath `
    (Join-Path $projectRoot "FT-Captcha.spec")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

Copy-Item -LiteralPath (Join-Path $PSScriptRoot "Start-FT-Captcha.bat") -Destination $packagePath
Copy-Item -LiteralPath (Join-Path $projectRoot "README.md") -Destination $packagePath

$selfTest = Start-Process `
    -FilePath (Join-Path $packagePath "FT-Captcha.exe") `
    -ArgumentList "--self-test" `
    -WindowStyle Hidden `
    -Wait `
    -PassThru
if ($selfTest.ExitCode -ne 0) {
    throw "Portable self-test failed."
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

$packageSize = (Get-ChildItem -LiteralPath $packagePath -Recurse -File | Measure-Object Length -Sum).Sum
$archiveSize = (Get-Item -LiteralPath $archivePath).Length
Write-Host ("Portable folder: {0:N1} MB" -f ($packageSize / 1MB))
Write-Host ("Portable archive: {0:N1} MB" -f ($archiveSize / 1MB))
Write-Host "Output: $archivePath"
Write-Host "SHA-256: $archiveHash"
