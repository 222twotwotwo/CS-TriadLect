Set-StrictMode -Version Latest
Set-Location $PSScriptRoot

python -m http.server 8778 --bind 127.0.0.1 --directory .
