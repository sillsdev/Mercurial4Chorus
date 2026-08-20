import argparse
import os
import zipfile

ap = argparse.ArgumentParser()
ap.add_argument("out", metavar="some.zip", type=str, nargs=1)
args = ap.parse_args()

reporoot = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..'))
dirstate = os.path.join(reporoot, '.hg', 'dirstate')

with zipfile.ZipFile(args.out[0], "w", zipfile.ZIP_STORED) as zf:
    if os.path.exists(dirstate):
        with open(dirstate, 'rb') as f:
            zf.writestr("dirstate", f.read())
