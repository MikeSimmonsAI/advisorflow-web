# Set up independent SCI, Wholesale, and Atlantis Claude worktrees.
# Run on Windows in PowerShell. Requires Git and an existing local advisorflow-web clone.
param([string]$RepoPath='C:\Dev\advisorflow-web',[string]$DevRoot='C:\Dev')
$ErrorActionPreference='Stop'
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Install Git or add it to PATH.' }
if (-not (Test-Path -LiteralPath $RepoPath)) { throw "Repository missing: $RepoPath" }
$origin = (& git -C $RepoPath remote get-url origin)
if ($LASTEXITCODE -ne 0 -or $origin -notmatch '(?i)github\.com[:/]MikeSimmonsAI/advisorflow-web(\.git)?$') { throw "Unexpected Git origin: $origin" }
New-Item -ItemType Directory -Path $DevRoot -Force | Out-Null
$lanes=@(
  @{Name='SCI';Branch='workstream/sci';Dir='af-sci';Guide='sci'},
  @{Name='Wholesale';Branch='workstream/wholesale';Dir='af-wholesale';Guide='wholesale'},
  @{Name='Atlantis';Branch='workstream/atlantis';Dir='af-atlantis';Guide='atlantis'}
)
foreach($l in $lanes) {
  $r="refs/heads/$($l.Branch):refs/remotes/origin/$($l.Branch)"
  & git -C $RepoPath fetch origin $r
  if ($LASTEXITCODE -ne 0) { throw "Unable to fetch $($l.Branch)" }
}
foreach($l in $lanes) {
  $path=Join-Path $DevRoot $l.Dir
  if (Test-Path -LiteralPath $path) {
    if (-not (Test-Path -LiteralPath (Join-Path $path '.git'))) { throw "$path already exists and is not a worktree" }
    $found=(& git -C $path branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $found -ne $l.Branch) { throw "$path already exists on branch '$found', not $($l.Branch)" }
    Write-Host "Already configured: $path"
  } else {
    & git -C $RepoPath show-ref --verify --quiet "refs/heads/$($l.Branch)"
    if ($LASTEXITCODE -eq 0) {
      & git -C $RepoPath worktree add $path $l.Branch
    } else {
      & git -C $RepoPath worktree add --track -b $l.Branch $path "origin/$($l.Branch)"
    }
    if ($LASTEXITCODE -ne 0) { throw "Failed to create $path" }
    Write-Host "Created: $path"
  }
  if (-not (Test-Path -LiteralPath (Join-Path $path 'CLAUDE.md'))) { throw "Missing Claude guide: $path\CLAUDE.md" }
  if (-not (Test-Path -LiteralPath (Join-Path $path ".claude\workstreams\$($l.Guide).md"))) { throw "Missing project guide for $($l.Name)" }
}
Write-Host 'All three local Claude worktrees are ready. No production data or deployments changed.'
foreach ($l in $lanes) { Write-Host "Launch in its own terminal: cd '$(Join-Path $DevRoot $l.Dir)'; claude" }
