# downloads.py - Code for downloading dependencies.
#
# Copyright 2019 Gregory Szorc <gregory.szorc@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

# no-check-code because Python 3 native.

import gzip
import hashlib
import pathlib
import typing
import urllib.request


DOWNLOADS = {
    'gettext': {
        'url': 'https://www.mercurial-scm.org/release/windows/gettext-0.14.4-bin.zip',
        'size': 1606131,
        'sha256': '60b9ef26bc5cceef036f0424e542106cf158352b2677f43a01affd6d82a1d641',
        'version': '0.14.4',
    },
    'gettext-dep': {
        'url': 'https://www.mercurial-scm.org/release/windows/gettext-0.14.4-dep.zip',
        'size': 715086,
        'sha256': '411f94974492fd2ecf52590cb05b1023530aec67e64154a88b1e4ebcd9c28588',
    },
}


def hash_path(p: pathlib.Path):
    h = hashlib.sha256()

    with p.open('rb') as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break

            h.update(chunk)

    return h.hexdigest()


class IntegrityError(Exception):
    """Represents an integrity error when downloading a URL."""


def secure_download_stream(url, size, sha256):
    """Securely download a URL to a stream of chunks.

    If the integrity of the download fails, an IntegrityError is
    raised.
    """
    h = hashlib.sha256()
    length = 0

    with urllib.request.urlopen(url) as fh:
        if (
            not url.endswith('.gz')
            and fh.info().get('Content-Encoding') == 'gzip'
        ):
            fh = gzip.GzipFile(fileobj=fh)

        while True:
            chunk = fh.read(65536)
            if not chunk:
                break

            h.update(chunk)
            length += len(chunk)

            yield chunk

    digest = h.hexdigest()

    if length != size:
        raise IntegrityError(
            'size mismatch on %s: wanted %d; got %d' % (url, size, length)
        )

    if digest != sha256:
        raise IntegrityError(
            'sha256 mismatch on %s: wanted %s; got %s' % (url, sha256, digest)
        )


def download_to_path(url: str, path: pathlib.Path, size: int, sha256: str):
    """Download a URL to a filesystem path, possibly with verification."""

    # We download to a temporary file and rename at the end so there's
    # no chance of the final file being partially written or containing
    # bad data.
    print('downloading %s to %s' % (url, path))

    if path.exists():
        good = True

        if path.stat().st_size != size:
            print('existing file size is wrong; removing')
            good = False

        if good:
            if hash_path(path) != sha256:
                print('existing file hash is wrong; removing')
                good = False

        if good:
            print('%s exists and passes integrity checks' % path)
            return

        path.unlink()

    tmp = path.with_name('%s.tmp' % path.name)

    try:
        with tmp.open('wb') as fh:
            for chunk in secure_download_stream(url, size, sha256):
                fh.write(chunk)
    except IntegrityError:
        tmp.unlink()
        raise

    tmp.rename(path)
    print('successfully downloaded %s' % url)


def download_entry(
    name: str, dest_path: pathlib.Path, local_name=None
) -> typing.Tuple[pathlib.Path, typing.Dict[str, typing.Union[str, int]]]:
    entry = DOWNLOADS[name]

    url = entry['url']

    local_name = local_name or url[url.rindex('/') + 1 :]

    local_path = dest_path / local_name
    download_to_path(url, local_path, entry['size'], entry['sha256'])

    return local_path, entry
