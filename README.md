# Mercurial4Chorus

This repo contains the binaries (Windows as well as Linux 64-bit) and extensions for the
Mercurial version that Chorus uses. Mercurial is provided in the form of a nuget package,
[SIL.Chorus.Mercurial](https://www.nuget.org/packages/SIL.Chorus.Mercurial).

After installation of the nuget package the `Mercurial` and `MercurialExtensions` folders will be
copied to the solution's directory during the build. Alternatively, specify `Mercurial4ChorusDestDir`
to copy into instead of the solution's directory.

## Building

To create a pre-release nuget package:

```bash
dotnet pack /p:BuildCounter=1
```

Output will be found in `artifacts/package/release` directory

To release a nuget package, push a commit to `master` and the GitHub Actions workflow will
release the package. The `linux-x64` directory is out-of-date and no longer updated by hand;
instead, it is updated at packaging time by the GHA workflow. So you cannot create a release
by hand from the current state of the repo.

See [README-Windows.md](/README-Windows.md) for tips on updating the `win/Mercurial` folder.
