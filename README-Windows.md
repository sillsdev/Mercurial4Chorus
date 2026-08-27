# Updating the Windows build

> [!NOTE]
> This documentation was created by Claude. I (Robin Munn) have been able to verify some of
> it, but not all of it. For example, some of the prerequisites were already installed on my
> Windows machine, and I could not check whether their installation instructions were complete.
> If you find anything incorrect or unclear in these instructions, please open an issue.

`win/Mercurial` is not built by CI. It is a subset of a [Mercurial][hg] Windows build, produced on
a Windows machine by `build-windows-payload.py` and committed by hand.

The script drives Mercurial's own packaging code — the same
`hgpackaging.pyoxidizer.create_pyoxidizer_install_layout()` that its Inno Setup and WiX installers
are built from — so the staging tree it selects from is by construction the tree upstream ships.
See `build-windows-payload.py`'s docstring for the details, including the cost: PyOxidizer's
`hg.exe` resolves modules from an index of concrete paths, so `lib/` expands to a directory per
package — 347 of them in Mercurial's own 7.0.1 install layout, against the six a `library.zip`
layout needed, and the Chorus installer records one `.guidsForInstaller.xml` per directory. The
script trims that back; see [Trimming](#trimming) below.

[hg]: https://foss.heptapod.net/mercurial/mercurial-devel

## Prerequisites

One-time setup on a 64-bit Windows machine. `winget` commands are given for convenience; install
by hand if you prefer.

1. **Python 3.** Any reasonably recent Python 3 will do: it only runs `hgpackaging`, which is
   ordinary Python 3 code. It is *not* the interpreter that ends up in the payload — PyOxidizer
   downloads and embeds its own CPython 3.9, as `rust/hgcli/pyoxidizer.bzl` asks for
   (`default_python_distribution(python_version = "3.9")`). That is what keeps the committed
   `cpython-39` bytecode in `MercurialExtensions/fixutf8` valid.

   ```powershell
   winget install Python.Python.3.12
   ```

2. **Rust and PyOxidizer.** `hg.exe` is a Rust program (`rust/hgcli`) with CPython linked into it,
   and `pyoxidizer` is what builds it.

   ```powershell
   winget install Rustlang.Rustup
   ```

   **Use the PyOxidizer version Mercurial pins**, not whatever is newest. PyOxidizer was
   discontinued in 2024 and its Starlark dialect moved between releases, so the `.bzl` file is
   only known to work with the version upstream tests against. That version is recorded as
   `$PYOXIDIZER_URL` in `contrib/install-windows-dependencies.ps1`, and at 7.0.1 it is 0.17.0,
   installed from an MSI:

   ```powershell
   curl.exe -L -o PyOxidizer.msi https://github.com/indygreg/PyOxidizer/releases/download/pyoxidizer%2F0.17/PyOxidizer-0.17.0-x64.msi
   msiexec /i PyOxidizer.msi
   ```

   Re-read `$PYOXIDIZER_URL` whenever the Mercurial tag moves — it is the only record of which
   version the `.bzl` in that tag expects. Running that whole script instead is also an option,
   but it installs a great deal more besides and hardcodes `c:\hgdev` as its install prefix.

3. **Visual Studio Build Tools**, with the C++ workload — MSVC and the Windows SDK. Mercurial's C
   extensions are compiled during the build, and the packaging code shells out to `vswhere.exe` to
   find `vcruntime140.dll`.

   ```powershell
   winget install Microsoft.VisualStudio.2022.BuildTools
   ```

   Then add *Desktop development with C++* in the Visual Studio Installer.

4. **Mercurial itself**, as a standalone program on `PATH`. The script uses it to read and update
   the checkout, and Mercurial's `setup.py` uses it to derive the version string it bakes into
   `mercurial/__version__.py`.

   ```powershell
   winget install Mercurial.Mercurial
   ```

5. **.NET SDK**, for the installer-GUID step. Skip if you always pass `--no-regen-guids`.

   ```powershell
   winget install Microsoft.DotNet.SDK.8
   ```

6. **A Mercurial checkout beside this one**, which is where `--hg-source` defaults to:

   ```powershell
   hg clone https://foss.heptapod.net/mercurial/mercurial-devel ..\hg
   ```

7. **Network access.** The build downloads gettext and pip-installs Mercurial itself plus
   everything in `contrib/packaging/requirements-windows-py3.txt` into the embedded interpreter.

Not required: `make` or a POSIX userland, HTML Help Workshop, `docutils`, PyQt5, py2exe, Inno
Setup, WiX, or a TortoiseHg checkout. The script stubs out the one documentation build that would
need `docutils`, because the payload does not ship documentation.

## Building

```powershell
py -3 build-windows-payload.py --tag 7.0.1
```

`--tag` updates the checkout before building. Without it the checkout is built as it stands, and
the script warns if that is not `DEFAULT_HG_TAG` — the tag this payload is meant to be — so a
stale `..\hg` cannot quietly change what gets built.

`--target-triple` defaults to `x86_64-pc-windows-msvc`; `i686-pc-windows-msvc` is the other
accepted value. Unlike the previous py2exe-based build, the architecture does **not** come from
the interpreter you launch the script with.

Expect the build to take tens of minutes. `--no-regen-guids` skips the only step needing the .NET
SDK, and `--from-stage DIR` skips the build entirely and assembles the payload from an existing
install layout — point it at a Mercurial MSI unpacked with `msiexec /a` to check the selection
rules in seconds instead of minutes.

## Trimming

Mercurial's Windows install layout carries a great deal that Chorus cannot reach:
`rust/hgcli/pyoxidizer.bzl` pip-installs all of `contrib/packaging/requirements-windows-py3.txt`
into the embedded interpreter "for convenience", which is mostly Mercurial's own test and release
tooling. **The script trims that by default.** Two flags control it:

| flag | effect |
| --- | --- |
| *(none)* | Trim third-party code with no importer anywhere in the payload, the copies of `locale/` and `templates/` under `lib/mercurial/` that Mercurial reads from the top level instead, installed-package metadata, and `contrib/`. |
| `--no-trim` | Ship the install layout as staged. Use it to reproduce the untrimmed tree when working out whether a problem is the trimming's fault. |
| `--trim-hgext` | *Additionally* drop the `hgext` extensions Chorus never enables, and the two packages only they import (`pygments`, imported only by `hgext/highlight`; `pygit2`, only by `hgext/git`). |
| `--trim-sources` | *Additionally* drop the `.py` files under `lib/`, keeping the bytecode. Independent of the other two. |

Measured by running `--from-stage` over the official Mercurial 7.0.1 x64 MSI. The nupkg column is
deflate calibrated against a real `dotnet pack`, so it is good to a few percent rather than exact:

| mode | files | directories | raw | nupkg |
| --- | ---: | ---: | ---: | ---: |
| `--no-trim` | 3277 | 347 | 99.8 MB | 37.3 MB |
| default | 1520 | 99 | 75.8 MB | 28.7 MB |
| `--trim-sources` | 837 | 65 | 63.3 MB | 25.3 MB |
| `--trim-hgext` | 675 | 58 | 60.2 MB | 23.6 MB |
| `--trim-hgext --trim-sources` | 395 | 40 | 54.0 MB | 21.9 MB |
| *the old TortoiseHg payload* | 99 | 6 | 46.3 MB | 23.4 MB |

The directory count matters as much as the megabytes, because it is the number of
`.guidsForInstaller.xml` files that have to be maintained for the life of the product.

The line between the two sets is deliberate. The default set is code with **no importer at all**
in the shipped `mercurial/` and `hgext/` trees, or whose only imports are inside a `try/except`
(that exception is `_curses`, imported that way by `color.py`, `crecord.py` and `histedit.py`).
`--trim-hgext` removes live code paths instead: nothing in this package enables those extensions,
but a config file outside it could, and then they would be missing rather than merely unused. That
is why it is opt-in.

Chorus's own `mercurial.ini` enables `eol`, `hgext.graphlog` and `convert`, plus the vendored
`fixutf8`; all four survive every mode, as does `lib/mercurial/helptext`, which `help.py` reads
through `importlib` and which `hg help <topic>` therefore needs. If you add to `TRIM_HGEXT`, check
the extension against that list first.

`--trim-sources` is opt-in for a different reason: it is not about reachability but about
diagnosis. `hg.exe` never needs the source — its resource index carries a separate path for each
module's source and its bytecode, and the bytecode is PEP 552 unchecked-hash, so nothing validates
one against the other. What is lost is the source line in a traceback: a Mercurial crash still
names the file, line number and function, but the line itself comes out blank. Chorus surfaces
hg's stderr, so that is a real if modest cost. A `.py` is only ever removed when its bytecode is
actually present, so the flag cannot make a module unimportable.

**Adding a DLL to any trim list needs a reason from its import table, not from its name.**
`libffi-7.dll` was once trimmed alongside `cffi` on the strength of the name; it is in fact the
only dependency of `lib/_ctypes.pyd`, and since `mercurial/win32.py` imports `ctypes` at module
scope, that payload could not run a single command. The build now reads the PE import table of
every surviving binary and refuses to finish if one of them needs a file that was removed.

## After the build

1. Smoke-test the result:

   ```powershell
   win\Mercurial\hg.exe version
   ```

2. Read the summary the script prints — both the trimming table and the added/removed list.
   `0 added, 0 removed` at the same tag is what you want; any added file at a new tag deserves a
   look. A file that appears because a new Mercurial started shipping it is expected; one that
   appears because a trim rule stopped matching is not.

3. Stage it, remembering the hidden GUID files:

   ```powershell
   git add -A win/Mercurial
   git status
   ```

   `hg.exe` and the files under `lib/` will show as modified **even when nothing has changed**,
   because the build is not reproducible. Judge the diff by the file list, not by which blobs
   moved.

4. If the Mercurial version changed, update `MercurialVersion` and `PackageReleaseNotes` in
   `SIL.Chorus.Mercurial.csproj`, the `mercurial-version` matrix in
   `.github/workflows/nuget-ci-cd.yml`, and `DEFAULT_HG_TAG` in the script.

5. Run the Chorus test suite on Windows against the new payload. It is the only real check that
   the vendored `fixutf8` extension still matches the Mercurial being shipped.

6. Commit the payload together with the regenerated `.guidsForInstaller.xml` files. Those record
   the installer's per-file component GUIDs and must stay stable, including for files a later
   Mercurial no longer ships, so never drop or hand-edit them.
