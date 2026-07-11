# Compare llama3.2 vs qwen2.5:7b on Nexus eval prompts
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

$models = @("llama3.2", "qwen2.5:7b")
$tests = @(
    @{ name = "greeting"; goal = "hello nexus"; expect = "Nexus" },
    @{ name = "capabilities"; goal = "what can you do?"; expect = "Files" },
    @{ name = "json_action"; goal = 'list python files in this project'; expect = "py" }
)

Write-Host "Model A/B Eval" -ForegroundColor Cyan
Write-Host ""

foreach ($model in $models) {
    $pull = ollama list 2>&1 | Out-String
    if ($pull -notmatch $model.Replace(":", ":")) {
        Write-Host "Skipping $model (not installed)" -ForegroundColor Yellow
        continue
    }

    Write-Host "Model: $model" -ForegroundColor Cyan
    $passed = 0
    foreach ($t in $tests) {
        $py = @"
import sys
sys.path.insert(0, '.')
from pathlib import Path
import yaml
from core.engine import NexusEngine
cfg = Path('config/brain.yaml')
data = yaml.safe_load(cfg.read_text(encoding='utf-8'))
data['llm']['model'] = '$model'
tmp = Path('config/brain.eval.yaml')
import json
tmp.write_text(yaml.dump(data), encoding='utf-8')
e = NexusEngine(config_path=tmp)
r = e.run('$($t.goal)')
print((r.get('result') or '').lower())
"@
        $out = py -c $py 2>&1
        $text = ($out | Out-String).ToLower()
        $ok = $text -match $t.expect.ToLower()
        Write-Host ("  {0,-14} {1}" -f $t.name, $(if ($ok) { "PASS" } else { "FAIL" })) -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
        if ($ok) { $passed++ }
    }
    Write-Host "  Score: $passed / $($tests.Count)" -ForegroundColor $(if ($passed -eq $tests.Count) { "Green" } else { "Yellow" })
    Write-Host ""
}

if (Test-Path "config/brain.eval.yaml") { Remove-Item "config/brain.eval.yaml" -Force }
