import argparse
import os
import zipfile

ap = argparse.ArgumentParser()
ap.add_argument("out", metavar="some.zip", type=str, nargs=1)
args = ap.parse_args()

reporoot = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..'))
# typically a standalone index
changelog = os.path.join(reporoot, '.hg', 'store', '00changelog.i')
# an inline revlog with only a few revisions
contributing = os.path.join(
    reporoot, '.hg', 'store', 'data', 'contrib', 'fuzz', 'mpatch.cc.i'
)

with zipfile.ZipFile(args.out[0], "w", zipfile.ZIP_STORED) as zf:
    if os.path.exists(changelog):
        with open(changelog, 'rb') as f:
            zf.writestr("00changelog.i", f.read())
    if os.path.exists(contributing):
        with open(contributing, 'rb') as f:
            zf.writestr("contributing.i", f.read())
