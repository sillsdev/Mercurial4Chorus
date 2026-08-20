# Where are the files?

The `linux-x64/Mercurial` directory is empty except for `mercurial.ini`.

**THIS IS ON PURPOSE.**

The `.github/workflows/nuget-ci-cd.yml` workflow downloads the Mercurial
source automatically, builds it for multiple Python versions (which
matters for the binary `.so` files since those need a different build
for each major Python version) and then merges them. If we have files
from an older Mercurial release in `linux-x64/Mercurial` then those
would be included in the package, which could lead to bugs. The only
file that should be included in the NuGet package is `Mercurial.ini`.
In fact, even this README.md file is deleted during the CI process
so that it will not be included in the final NuGet package.