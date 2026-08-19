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

To release a nuget package:

```bash
dotnet pack /p:PreRelease=.
dotnet nuget push artifacts/package/release/*.nupkg --source https://api.nuget.org/v3/index.json --api-key INSERT_NUGET_API_KEY_HERE
```

See [README-Windows.md](/README-Windows.md) for tips on updating the `win/Mercurial` folder.
The `linux-x64/Mercurial` folder is updated by GitHub Actions CI.
