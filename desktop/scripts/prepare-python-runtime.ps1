param(
  [string]$PythonVersion = '3.12',
  [string]$Destination = (Join-Path $PSScriptRoot '..\resources\python')
)

$ErrorActionPreference = 'Stop'
$destinationPath = [System.IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $destinationPath) {
  throw "Runtime destination already exists: $destinationPath"
}

$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
$stagingRoot = Join-Path $tempRoot ("agent-harness-python-" + [guid]::NewGuid().ToString())
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$oldPythonInstallDir = $env:UV_PYTHON_INSTALL_DIR
$locationPushed = $false
New-Item -ItemType Directory -Path $stagingRoot | Out-Null
try {
  $env:UV_PYTHON_INSTALL_DIR = Join-Path $stagingRoot 'uv-python'
  uv python install $PythonVersion
  if ($LASTEXITCODE -ne 0) { throw 'uv could not install the standalone Python runtime' }

  $python = Get-ChildItem -LiteralPath $env:UV_PYTHON_INSTALL_DIR -Filter python.exe -File -Recurse | Select-Object -First 1
  if (-not $python) { throw 'uv installed Python but no python.exe was found' }

  Push-Location $repoRoot
  $locationPushed = $true
  uv export --locked --package vertical-agent-harness --extra analysis --extra desktop --no-emit-project --format requirements-txt --output-file (Join-Path $stagingRoot 'requirements.txt') > $null
  if ($LASTEXITCODE -ne 0) { throw 'uv could not export locked desktop dependencies' }
  uv build --package vertical-agent-harness --wheel --out-dir $stagingRoot
  if ($LASTEXITCODE -ne 0) { throw 'uv could not build the harness wheel' }
  uv pip sync --system --break-system-packages --python $python.FullName (Join-Path $stagingRoot 'requirements.txt')
  if ($LASTEXITCODE -ne 0) { throw 'uv could not install locked desktop dependencies' }
  $wheel = Get-ChildItem -LiteralPath $stagingRoot -Filter 'vertical_agent_harness-*.whl' -File | Select-Object -First 1
  if (-not $wheel) { throw 'uv built no harness wheel' }
  uv pip install --system --break-system-packages --python $python.FullName --no-deps $wheel.FullName
  if ($LASTEXITCODE -ne 0) { throw 'uv could not install the harness wheel' }

  & $python.FullName -I -c "import vertical_harness.desktop, vertical_plugins.data_analysis, numpy, pandas, scipy, sklearn, statsmodels, lifelines, matplotlib, duckdb, openpyxl, pypdf, docx, pptx; print('runtime imports ok')"
  if ($LASTEXITCODE -ne 0) { throw 'the staged Python runtime failed its import check' }

  $destinationParent = Split-Path -Parent $destinationPath
  New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
  New-Item -ItemType Directory -Path $destinationPath | Out-Null
  Copy-Item -Path (Join-Path $python.Directory.FullName '*') -Destination $destinationPath -Recurse
  & (Join-Path $destinationPath 'python.exe') -I -c "import vertical_harness.desktop, vertical_plugins.data_analysis, numpy, pandas, scipy, sklearn, statsmodels, lifelines, matplotlib, duckdb, openpyxl, pypdf, docx, pptx; print('relocated runtime imports ok')"
  if ($LASTEXITCODE -ne 0) { throw 'the relocated Python runtime failed its import check' }
} finally {
  if ($locationPushed) { Pop-Location }
  if ($null -eq $oldPythonInstallDir) { Remove-Item Env:UV_PYTHON_INSTALL_DIR -ErrorAction SilentlyContinue }
  else { $env:UV_PYTHON_INSTALL_DIR = $oldPythonInstallDir }
  if ([System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($stagingRoot)).TrimEnd('\') -ne $tempRoot -or [System.IO.Path]::GetFileName($stagingRoot) -notmatch '^agent-harness-python-[0-9a-f-]{36}$') {
    throw "Refusing to remove unexpected staging path: $stagingRoot"
  }
  Remove-Item -LiteralPath $stagingRoot -Recurse -Force
}
