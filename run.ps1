param(
  [Parameter(Position=0)]
  [ValidateSet('collect','list','approve','status','build','openai-setup','telegram-setup','telegram-send','drive-setup','clean')]
  [string]$Action = 'status',
  [Parameter(Position=1)]
  [int]$Index,
  [switch]$Preview,
  [switch]$NoUpload
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$Python = (Get-Command py -ErrorAction SilentlyContinue).Source
$PythonArgs = @('-3.11')
if (-not $Python) {
  $Python = (Get-Command python -ErrorAction SilentlyContinue).Source
  $PythonArgs = @()
}
if (-not $Python) {
  $BundledPython = 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
  if (Test-Path -LiteralPath $BundledPython) {
    $Python = $BundledPython
    $PythonArgs = @()
  } else {
    throw 'Python 3.11 이상이 필요합니다. https://python.org 에서 설치해 주세요.'
  }
}
if (-not (Test-Path .venv)) {
  & $Python @PythonArgs -m venv .venv
  .\.venv\Scripts\python.exe -m pip install -e .
}
if (Test-Path .env) {
  Get-Content .env | Where-Object { $_ -match '^[^#].+=' } | ForEach-Object {
    $name, $value = $_ -split '=', 2
    Set-Item -Path "Env:$name" -Value $value
  }
}
$arguments = @($Action)
if ($Action -eq 'approve') {
  if ($Index -lt 1) { throw '승인할 후보 번호가 필요합니다. 예: .\run.ps1 approve 3' }
  $arguments += "$Index"
}
if ($Preview) { $arguments += "--preview" }
if ($NoUpload) { $arguments += "--no-upload" }
& .\.venv\Scripts\cardnews-auto.exe @arguments
