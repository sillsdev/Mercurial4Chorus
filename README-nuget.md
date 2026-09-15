# SIL.Chorus.Mercurial

The [Mercurial](https://www.mercurial-scm.org/) distributed version control system, packaged for
[Chorus](https://github.com/sillsdev/chorus) and LibChorus, together with the vendored `fixutf8`
extension that keeps non-ASCII filenames working on Windows.

The package version is the Mercurial version it contains, followed by a build number: `7.0.1.123`
is Mercurial 7.0.1.

## What it does to your build

Referencing the package copies two folders into your solution directory as part of every build:

- `Mercurial/` — the `hg` binaries for the platform you are building on, Windows or Linux x64.
- `MercurialExtensions/` — the `fixutf8` extension, wired into the copied
  `Mercurial/mercurial.ini` with the absolute path it needs, which is only knowable at build time.

To put them somewhere other than `$(SolutionDir)`, set `Mercurial4ChorusDestDir`:

```xml
<PropertyGroup>
  <Mercurial4ChorusDestDir>$(MSBuildProjectDirectory)\bin</Mercurial4ChorusDestDir>
</PropertyGroup>
```

## You may not need to reference this directly

Chorus and LibChorus depend on this package and expose it transitively, so depending on either of
those is enough to get Mercurial copied into your build.

## Source and issues

<https://github.com/sillsdev/Mercurial4Chorus>