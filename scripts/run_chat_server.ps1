# PowerShell wrapper to launch the chat webhook locally with uvicorn.
#
# After this script is running:
#   1. In another terminal: `ngrok http 8000`
#   2. Copy the https URL ngrok prints, append "/webhook"
#   3. Paste into Twilio: Console -> Messaging -> Try it out ->
#      Send a WhatsApp message -> Sandbox settings ->
#      "When a message comes in" = https://<ngrok-id>.ngrok.io/webhook (POST)
#
# Env: reads .env via src.config (Anthropic + Twilio credentials).
# Logs: stderr is colored; a daily file lands in logs/agent_<date>.log.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$env:PYTHONIOENCODING = "utf-8"

$Port = if ($env:CHAT_PORT) { $env:CHAT_PORT } else { "8000" }
$Host = if ($env:CHAT_HOST) { $env:CHAT_HOST } else { "0.0.0.0" }

Write-Host "Starting chat webhook on http://${Host}:${Port}" -ForegroundColor Cyan
Write-Host "Twilio webhook URL: <ngrok-https-url>/webhook" -ForegroundColor DarkGray

& "$ProjectRoot\.venv\Scripts\python.exe" -m uvicorn src.chat.server:app `
    --host $Host --port $Port --log-level info
exit $LASTEXITCODE
