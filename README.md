# Mercurial4Chorus

This repo contains the binaries (Windows as well as Linux 64-bit) and extensions for the
Mercurial version that Chorus uses. Mercurial is provided in the form of a nuget package,
[SIL.Chorus.Mercurial](https://www.nuget.org/packages/SIL.Chorus.Mercurial).

After installation of the nuget package the `Mercurial` and `MercurialExtensions` folders will be
copied to the solution's directory during the build. Alternatively, specify `Mercurial4ChorusDestDir`
to copy into instead of the solution's directory.

## Building

To build a package locally, for testing:

```bash
dotnet pack /p:BuildCounter=1
```

Output lands in the `artifacts/package/release` directory.

**The Linux half of a locally built package does not work.** `linux-x64/Mercurial` is committed as
pure Python with no compiled extension modules; those are built once per Python version by the
GitHub Actions workflow and overlaid at packaging time. A local `dotnet pack` is useful for
testing the Windows payload and the MSBuild plumbing, and for nothing else.

To release, push a commit to `master`: the workflow builds the Linux extensions, packs, and
publishes to nuget.org. There is no hand-release path.

Two checks run in CI and are worth running before you commit a change to the build scripts:
`test-build-windows-payload.py`, which needs nothing but Python, and `check-fixutf8-bytecode.py`,
which must run under CPython 3.9 — see [README-Windows.md](README-Windows.md), which also covers
updating the `win/Mercurial` folder.

The text on the package's nuget.org page comes from [README-nuget.md](README-nuget.md), not from
this file.
