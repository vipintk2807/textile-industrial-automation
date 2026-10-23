@echo off
rem Shows which GitHub identity this repository would use. Changes nothing, prints no secrets.
setlocal
cd /d "%~dp0.."
echo --- Is this a git repository?
git rev-parse --is-inside-work-tree 2>nul || ( echo Not a git repository yet. See docs\PUBLISHING.md & exit /b 1 )
echo --- Repository-local identity (should be Vipin T K for this project)
git config --local user.name
git config --local user.email
echo --- Remotes
git remote -v
echo --- Current branch
git branch --show-current
echo --- Files that would be committed (check nothing private is listed)
git status --short
endlocal
