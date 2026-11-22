param(
  [string]$Url = "http://localhost:8000/kiosk/",
  [switch]$Edge
)

$browser = if ($Edge) {
  "$env:ProgramFiles(x86)\Microsoft\Edge\Application\msedge.exe"
} else {
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
}

if (-not (Test-Path $browser)) {
  throw "Browser executable not found at $browser. Install Chrome or run with -Edge."
}

Start-Process $browser -ArgumentList @(
  "--kiosk",
  "--app=$Url",
  "--no-first-run",
  "--disable-pinch",
  "--overscroll-history-navigation=0",
  "--autoplay-policy=no-user-gesture-required",
  "--disable-session-crashed-bubble",
  "--check-for-update-interval=604800"
)
