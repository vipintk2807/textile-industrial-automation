# Publishing to GitHub (Windows Command Prompt)

Target: **account `vipintk2807`** (profile name *Vipin T K*), repository **`textile-industrial-automation`**
→ `https://github.com/vipintk2807/textile-industrial-automation`.

You use several GitHub accounts, so **never rely on your global Git identity or default SSH key.** Everything below is
repository-local and checks before it acts. Nothing here asks for, or should ever contain, a password, token or private key.

## 1. Extract and open
```bat
cd /d D:\Projects
tar -xf textile-industrial-automation.zip
cd textile-industrial-automation
```
(Or "Extract All" in Explorer.) Confirm you are in the folder that contains `pyproject.toml` and `README.md`:
```bat
dir pyproject.toml README.md
```

## 2. Make sure the project works before publishing
```bat
scripts\setup.bat
scripts\test.bat
scripts\quality.bat
```
Fix anything reported, then `scripts\freeze.bat` to record tested dependency versions (`requirements-lock.txt`).

## 3. Check what your computer would use by default (read-only)
```bat
git --version
git config --global user.name
git config --global user.email
type %USERPROFILE%\.ssh\config
ssh-add -l
```
Look for the alias `github-vipintk2807` in `.ssh\config`. A correct entry looks like this (your key file name may differ; **do not paste key contents anywhere**):
```
Host github-vipintk2807
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_vipintk2807
    IdentitiesOnly yes
```
Verify the alias reaches the right account (it should greet `vipintk2807`):
```bat
ssh -T git@github-vipintk2807
```
If it greets a different username, or says the alias is unknown, **stop**: fix `.ssh\config` first. Do not push.

## 4. Create the empty repository on GitHub
In the browser, signed in as **vipintk2807**: *New repository* → name `textile-industrial-automation` → **do not** add a README, .gitignore or licence (they exist locally) → Create.

## 5. Initialise Git with a repository-local identity
```bat
git init -b main
git config --local user.name "Vipin T K"
git config --local user.email "YOUR-vipintk2807-ACCOUNT-EMAIL"
scripts\check_git_identity.bat
```
Use the e-mail address (or the `...@users.noreply.github.com` address) attached to the **vipintk2807** account. `--local` affects only this folder.

## 6. Check what will be committed
```bat
git add -A
git status
```
Confirm that **none** of these appear: `.venv`, `*.db`, `logs`, `config\devices.toml`, `.env`, any of the seven planning documents
(`master_prompt.md`, `project_explanation.md`, `technical_specification.md`, `hardware_communication_specification.md`,
`app_workflow.md`, `app_theme.md`, `testing_requirements.md`). `.gitignore` already excludes them; if one shows, run
`git rm --cached <file>` and re-check.

## 7. Commit, add the remote with the account-specific alias, verify, push
```bat
git commit -m "Initial release: read-only simulator-first monitoring foundation"
git remote add origin git@github-vipintk2807:vipintk2807/textile-industrial-automation.git
git remote -v
git branch --show-current
```
`git remote -v` must show `git@github-vipintk2807:vipintk2807/textile-industrial-automation.git` and the branch must be `main`. Then:
```bat
git push -u origin main
```
Afterwards open the repository page and confirm the owner is `vipintk2807` and the file list matches step 6.

## If a push goes to the wrong account
Do not force anything. Run `git remote -v`, correct it with `git remote set-url origin git@github-vipintk2807:vipintk2807/textile-industrial-automation.git`,
and re-check `ssh -T git@github-vipintk2807`. If a commit was authored with the wrong identity before pushing, fix it with
`git commit --amend --reset-author` (only if not yet pushed).

## Before making the repository public
* Re-read `docs/VERIFICATION.md` and state in the repository description that the simulator is the only implemented adapter.
* Run `pip install pip-audit` then `pip-audit` in the activated environment.
* Decide the licence (MIT is included; change `LICENSE` and `pyproject.toml` together if you prefer another).
