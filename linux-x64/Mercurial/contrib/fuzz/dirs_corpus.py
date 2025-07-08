import argparse
import zipfile

ap = argparse.ArgumentParser()
ap.add_argument("out", metavar="some.zip", type=str, nargs=1)
args = ap.parse_args()

with zipfile.ZipFile(args.out[0], "w", zipfile.ZIP_STORED) as zf:
    zf.writestr(
        "greek-tree",
        "\n".join(
            [
                "iota",
                "A/mu",
                "A/B/lambda",
                "A/B/E/alpha",
                "A/B/E/beta",
                "A/D/gamma",
                "A/D/G/pi",
                "A/D/G/rho",
                "A/D/G/tau",
                "A/D/H/chi",
                "A/D/H/omega",
                "A/D/H/psi",
            ]
        ),
    )
