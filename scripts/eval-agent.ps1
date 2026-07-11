# Nexus agent evaluation harness
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host "Nexus Agent Eval" -ForegroundColor Cyan
Write-Host ""

$tests = @(
    @{ name = "greeting"; goal = "hello nexus"; expect = "Nexus" },
    @{ name = "capabilities"; goal = "what can you do?"; expect = "Files" },
    @{ name = "help"; goal = "help"; expect = "help with" },
    @{ name = "health"; goal = "check nexus health"; expect = "ollama" },
    @{ name = "list_py"; goal = "list python files in this project"; expect = "py" }
)

$passed = 0
$failed = 0

foreach ($t in $tests) {
    Write-Host "Test: $($t.name) ... " -NoNewline
    $out = py -c "from core.engine import NexusEngine; r=NexusEngine().run('$($t.goal)'); print((r.get('result') or '').lower())" 2>&1
    $text = ($out | Out-String).ToLower()
    if ($text -match $t.expect.ToLower()) {
        Write-Host "PASS" -ForegroundColor Green
        $passed++
    } else {
        Write-Host "FAIL" -ForegroundColor Red
        Write-Host "  Expected pattern: $($t.expect)" -ForegroundColor DarkGray
        Write-Host "  Got: $($text.Substring(0, [Math]::Min(120, $text.Length)))" -ForegroundColor DarkGray
        $failed++
    }
}

Write-Host ""
Write-Host "Results: $passed passed, $failed failed" -ForegroundColor $(if ($failed -eq 0) { "Green" } else { "Yellow" })
