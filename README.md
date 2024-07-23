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
msbuild /p:BuildCounter=1 build/SIL.Chorus.Mercurial.proj
```

To release a nuget package:

```bash
msbuild /p:PreRelease=. build/SIL.Chorus.Mercurial.proj
```
