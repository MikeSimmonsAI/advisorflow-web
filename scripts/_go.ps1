Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
Remove-Item "scripts\_z.ps1" -Force -ErrorAction SilentlyContinue
$gi = Get-Content ".gitignore" -Raw
if ($gi -notmatch "deploy_commit_msg") {
  Add-Content ".gitignore" "`n.deploy_commit_msg.txt"
  Write-Output "gitignored .deploy_commit_msg.txt"
}
& ".\.launch_deploy.ps1"
