[CmdletBinding()]
param(
    [string]$Python,
    [switch]$SkipDependencyInstall,
    [switch]$RunTests,
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "Le bundle HSCoach Windows doit etre construit sur Windows."
}

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$sourceRoot = Join-Path $repoRoot "src"
$entryPoint = Join-Path $sourceRoot "hscoach\gui\__main__.py"
$distRoot = Join-Path $repoRoot "dist"
$bundleRoot = Join-Path $distRoot "HSCoach"
$workRoot = Join-Path $repoRoot "build\pyinstaller"

function Resolve-PythonExecutable {
    param([string]$RequestedPython)

    if ($RequestedPython) {
        $command = Get-Command $RequestedPython -ErrorAction Stop
        return $command.Source
    }

    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        return $venvPython
    }

    $command = Get-Command "python.exe" -ErrorAction Stop
    return $command.Source
}

function Assert-ChildPath {
    param(
        [string]$Candidate,
        [string]$Parent
    )

    $candidatePath = [IO.Path]::GetFullPath($Candidate)
    $parentPath = [IO.Path]::GetFullPath($Parent).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $prefix = $parentPath + [IO.Path]::DirectorySeparatorChar
    if (-not $candidatePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Chemin de build hors du depot refuse : $candidatePath"
    }
}

function Invoke-Python {
    param([string[]]$Arguments)

    & $script:pythonExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "La commande Python a echoue avec le code $LASTEXITCODE."
    }
}

function Copy-LicenseFiles {
    param([string]$Destination)

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null

    $pythonLicense = & $script:pythonExe -c "import pathlib, sys; print(pathlib.Path(sys.base_prefix) / 'LICENSE.txt')"
    if ($LASTEXITCODE -ne 0) {
        throw "Impossible de localiser la licence de Python."
    }
    if (Test-Path -LiteralPath $pythonLicense -PathType Leaf) {
        Copy-Item -LiteralPath $pythonLicense -Destination (Join-Path $Destination "Python-LICENSE.txt") -Force
    } else {
        Write-Warning "La licence de l'interpreteur Python n'a pas ete trouvee : $pythonLicense"
    }

    $sitePackages = & $script:pythonExe -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $sitePackages -PathType Container)) {
        throw "Impossible de localiser les metadonnees des dependances Python."
    }

    $distributionDirectories = Get-ChildItem -LiteralPath $sitePackages -Directory -Filter "*.dist-info" | Sort-Object Name
    foreach ($distributionDirectory in $distributionDirectories) {
        $licenseFiles = Get-ChildItem -LiteralPath $distributionDirectory.FullName -Recurse -File | Where-Object {
            $_.Name -match "^(LICENSE|LICENCE|COPYING|NOTICE)(\.|$)" -or
            $_.Name -match "^LicenseRef-"
        }
        foreach ($licenseFile in $licenseFiles) {
            $trimCharacters = [char[]]@(
                [IO.Path]::DirectorySeparatorChar,
                [IO.Path]::AltDirectorySeparatorChar
            )
            $relativePath = $licenseFile.FullName.Substring($distributionDirectory.FullName.Length).TrimStart($trimCharacters)
            $targetPath = Join-Path (Join-Path $Destination $distributionDirectory.Name) $relativePath
            $targetDirectory = Split-Path -Parent $targetPath
            New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
            Copy-Item -LiteralPath $licenseFile.FullName -Destination $targetPath -Force
        }
    }
}

Assert-ChildPath -Candidate $distRoot -Parent $repoRoot
Assert-ChildPath -Candidate $workRoot -Parent $repoRoot

if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) {
    throw "Point d'entree GUI introuvable : $entryPoint"
}

$script:pythonExe = Resolve-PythonExecutable -RequestedPython $Python
Write-Host "Python : $script:pythonExe"

Invoke-Python -Arguments @(
    "-c",
    "import struct, sys; assert sys.version_info >= (3, 11); assert struct.calcsize('P') == 8, 'Python 64 bits requis'"
)

Push-Location $repoRoot
try {
    if (-not $SkipDependencyInstall) {
        $extras = if ($RunTests) { ".[dev,gui,build]" } else { ".[gui,build]" }
        Invoke-Python -Arguments @(
            "-m", "pip", "install", "--disable-pip-version-check",
            "-c", (Join-Path $repoRoot "requirements\release.txt"), "-e", $extras
        )
    }

    Invoke-Python -Arguments @("-m", "pip", "check")
    Invoke-Python -Arguments @(
        "-c",
        "import PyInstaller, PySide6, hearthstone, hslog, hsreplay, hscoach; print('Dependances de build disponibles.')"
    )

    if ($RunTests) {
        $previousTestQtPlatform = $env:QT_QPA_PLATFORM
        $env:QT_QPA_PLATFORM = "offscreen"
        try {
            Invoke-Python -Arguments @("-m", "pytest")
            Invoke-Python -Arguments @("-m", "ruff", "check", ".")
            Invoke-Python -Arguments @("-m", "ruff", "format", "--check", ".")
        } finally {
            if ($null -eq $previousTestQtPlatform) {
                Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
            } else {
                $env:QT_QPA_PLATFORM = $previousTestQtPlatform
            }
        }
    }

    New-Item -ItemType Directory -Path $workRoot -Force | Out-Null

    $pyInstallerArguments = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--disable-windowed-traceback",
        "--noupx",
        "--name", "HSCoach",
        "--contents-directory", "_internal",
        "--paths", $sourceRoot,
        "--distpath", $distRoot,
        "--workpath", $workRoot,
        "--specpath", $workRoot,
        "--copy-metadata", "hearthstone",
        "--copy-metadata", "hslog",
        "--copy-metadata", "hsreplay",
        "--recursive-copy-metadata", "PySide6",
        "--exclude-module", "PyQt5",
        "--exclude-module", "PyQt6",
        "--exclude-module", "PySide2",
        $entryPoint
    )
    Invoke-Python -Arguments $pyInstallerArguments

    $executable = Join-Path $bundleRoot "HSCoach.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "Le build n'a pas produit l'executable attendu : $executable"
    }

    Copy-Item -LiteralPath (Join-Path $repoRoot "LICENSE") -Destination (Join-Path $bundleRoot "LICENSE") -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot "README.md") -Destination (Join-Path $bundleRoot "README.md") -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot "docs\THIRD_PARTY_NOTICES.md") -Destination (Join-Path $bundleRoot "THIRD_PARTY_NOTICES.md") -Force
    $licenseDestination = Join-Path $bundleRoot "licenses"
    New-Item -ItemType Directory -Path $licenseDestination -Force | Out-Null
    $requiredLicenseHashes = [ordered]@{
        "GPL-3.0-only.txt" = "8CEB4B9EE5ADEDDE47B31E975C1D90C73AD27B6B165A1DCD80C7C545EB65B903"
        "LGPL-3.0-only.txt" = "DA7EABB7BAFDF7D3AE5E9F223AA5BDC1EECE45AC569DC21B3B037520B4464768"
    }
    foreach ($licenseEntry in $requiredLicenseHashes.GetEnumerator()) {
        $licenseSource = Join-Path (Join-Path $repoRoot "licenses") $licenseEntry.Key
        if (-not (Test-Path -LiteralPath $licenseSource -PathType Leaf)) {
            throw "Texte de licence requis introuvable : $licenseSource"
        }
        $licenseHash = (Get-FileHash -LiteralPath $licenseSource -Algorithm SHA256).Hash
        if ($licenseHash -ne $licenseEntry.Value) {
            throw "Empreinte inattendue pour le texte de licence : $licenseSource"
        }
        Copy-Item -LiteralPath $licenseSource -Destination (Join-Path $licenseDestination $licenseEntry.Key) -Force
    }
    Copy-Item -LiteralPath (Join-Path $repoRoot "licenses\README.md") -Destination (Join-Path $licenseDestination "README.md") -Force
    Copy-LicenseFiles -Destination $licenseDestination

    $forbiddenArtifacts = Get-ChildItem -LiteralPath $bundleRoot -Recurse -File | Where-Object {
        $_.Extension -in @(".hsreplay") -or
        $_.Name -in @("game_analysis.json", "game_llm.json", "game_summary.md", "cards.json")
    }
    if ($forbiddenArtifacts) {
        $paths = ($forbiddenArtifacts.FullName -join [Environment]::NewLine)
        throw "Le bundle contient des donnees interdites :$([Environment]::NewLine)$paths"
    }

    if (-not $SkipSmokeTest) {
        $previousQtPlatform = $env:QT_QPA_PLATFORM
        $env:QT_QPA_PLATFORM = "offscreen"
        try {
            $smokeProcess = Start-Process `
                -FilePath $executable `
                -ArgumentList @("--smoke-test") `
                -Wait `
                -PassThru `
                -WindowStyle Hidden
            if ($smokeProcess.ExitCode -ne 0) {
                throw "Le smoke test de HSCoach.exe a echoue avec le code $($smokeProcess.ExitCode)."
            }
        } finally {
            if ($null -eq $previousQtPlatform) {
                Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
            } else {
                $env:QT_QPA_PLATFORM = $previousQtPlatform
            }
        }
    }

    $hash = Get-FileHash -LiteralPath $executable -Algorithm SHA256
    Write-Host "Build Windows termine : $executable"
    Write-Host "SHA-256 : $($hash.Hash)"
} finally {
    Pop-Location
}
