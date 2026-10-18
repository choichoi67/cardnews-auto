param(
  [Parameter(Position=0)]
  [ValidateSet('collect','list','approve','status','build','openai-setup','telegram-setup','telegram-send','drive-setup','clean')]
  [string]$Action = 'collect',
  [Parameter(Position=1)]
  [int]$Index,
  [switch]$Preview,
  [switch]$NoUpload
)

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$arguments = @($Action)
if ($Action -eq 'approve') { $arguments += $Index }
if ($Preview) { $arguments += '-Preview' }
if ($NoUpload) { $arguments += '-NoUpload' }
& "$PSScriptRoot\run.ps1" @arguments
