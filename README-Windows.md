# Updating the Windows build

> [!NOTE]
> This documentation was created by Claude. I (Robin Munn) have been able to verify some of
> it, but not all of it. For example, some of the prerequisites were already installed on my
> Windows machine, and I could not check whether their installation instructions were complete.
> If you find anything incorrect or unclear in these instructions, please open an issue.

`win/Mercurial` is not built by CI. It is a subset of a [TortoiseHg][thg] build, produced on a
Windows machine by `build-windows-payload.py` and committed by hand. TortoiseHg is used rather
than Mercurial's own Windows installer because its py2exe build puts the whole pure-Python tree
into one `lib/library.zip`, which keeps the installer's per-directory GUID bookkeeping down to six
files instead of a hundred. See `build-windows-payload.py`'s docstring for the details.

[thg]: https://foss.heptapod.net/mercurial/tortoisehg/thg

## Prerequisites

One-time setup on a 64-bit Windows machine. `winget` commands are given for convenience; install
by hand if you prefer.

1. **Python 3.9, 64-bit.** This is the interpreter you launch the script with, and it becomes the
   build interpreter: it decides the payload's architecture, so a 32-bit one silently produces an
   x86 payload. 3.9.13 is the last version of 3.9 Python shipped a Windows installer for, and is
   the one to use.

   ```powershell
   winget install Python.Python.3.9
   ```

   **Do not `pip install mercurial` into it.** TortoiseHg's build refuses to run when `mercurial`
   is importable from that interpreter's `site-packages`, because it would shadow the copy being
   bundled, and aborts with `Error: ... overrides included package 'mercurial'`.

2. **`make` plus a POSIX userland.** Mercurial's `make local` target calls `env`, and its Makefile
   also uses `rm`, `cp`, `test` and `find`, so a bare `make.exe` is not enough.

   ```powershell
   winget install MSYS2.MSYS2
   C:\msys64\usr\bin\pacman -S --noconfirm make coreutils
   ```

   Then put `C:\msys64\usr\bin` on `PATH`. (Git for Windows already provides `env` and friends in
   `C:\Program Files\Git\usr\bin` but has no `make`, so adding just `make` on top of that works
   too.)

3. **Visual Studio Build Tools**, with the C++ workload — MSVC and the Windows SDK. Mercurial's C
   extensions are compiled during the build, and the packaging code shells out to `vswhere.exe`,
   `vcvars*.bat` and `dumpbin.exe`.

   ```powershell
   winget install Microsoft.VisualStudio.2022.BuildTools
   ```

   Then add *Desktop development with C++* in the Visual Studio Installer.

4. **Mercurial itself**, as a standalone program on `PATH` — the build clones five repositories
   and runs `hg purge` and `hg archive`.

   ```powershell
   winget install Mercurial.Mercurial
   ```

5. **.NET SDK**, for the installer-GUID step. Skip if you always pass `--no-regen-guids`.

   ```powershell
   winget install Microsoft.DotNet.SDK.8
   ```

6. **A TortoiseHg checkout beside this one**, which is where `--thg-source` defaults to:

   ```powershell
   hg clone https://foss.heptapod.net/mercurial/tortoisehg/thg ..\thg
   ```

7. **Network access.** The build clones Mercurial, evolve, thg-shellext and thg-winbuild into
   `..\thg\dependencies`, downloads gettext, and pip-installs PyQt5, py2exe and the rest from
   TortoiseHg's hash-pinned requirements.

Not required, despite what TortoiseHg's own packaging documentation says: HTML Help Workshop,
`docutils`, Rust, PyOxidizer, Inno Setup or WiX. The script skips the two documentation builds
that would need the first two, because the payload does not ship documentation.

## Building

```powershell
py -3.9 build-windows-payload.py --tag 7.0.1
```

`--tag` is the TortoiseHg tag to build. `--hg-tag` and `--evolve-rev` say which Mercurial and
evolve to bundle; they default to what TortoiseHg 7.0.1 shipped, so **both need setting for any
other tag**:

```powershell
py -3.9 build-windows-payload.py --tag 7.2.2 --hg-tag 7.2.2 --evolve-rev <changeset>
```

The reliable source for a release's evolve changeset is the `extension-versions.txt` inside that
release's official MSI. Expect the build to take tens of minutes; `--no-regen-guids` skips the
only step needing the .NET SDK, and `--from-stage DIR` skips the build entirely and assembles the
payload from an existing staging tree, which is much faster when iterating on the selection rules.

## After the build

1. Smoke-test the result:

   ```powershell
   win\Mercurial\hg.exe version
   ```

2. Read the summary the script prints. `0 added, 0 removed` at the same tag is what you want; any
   added file at a new tag deserves a look, since a new TortoiseHg GUI file would show up there.

3. Stage it, remembering the hidden GUID files:

   ```powershell
   git add -A win/Mercurial
   git status
   ```

   `lib/library.zip`, `hg.exe` and the `.pyd` files will show as modified **even when nothing has
   changed**, because the build is not reproducible — marshalled bytecode orders `set` and
   `frozenset` constants nondeterministically. Judge the diff by the file list, not by which blobs
   moved.

4. If the Mercurial version changed, update `MercurialVersion` and `PackageReleaseNotes` in
   `SIL.Chorus.Mercurial.csproj`, the `mercurial-version` matrix in
   `.github/workflows/nuget-ci-cd.yml`, and the `DEFAULT_HG_TAG` / `DEFAULT_EVOLVE_REV` defaults
   in the script.

5. Run the Chorus test suite on Windows against the new payload. It is the only real check that
   the vendored `fixutf8` extension still matches the Mercurial being shipped.

6. Commit the payload together with the regenerated `.guidsForInstaller.xml` files. Those record
   the installer's per-file component GUIDs and must stay stable, including for files a later
   Mercurial no longer ships, so never drop or hand-edit them.