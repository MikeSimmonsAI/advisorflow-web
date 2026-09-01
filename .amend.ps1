Set-Location -LiteralPath $PSScriptRoot
git commit --amend -F "$PSScriptRoot\.deploy_msg.txt" | Out-Null
git log --oneline -1
