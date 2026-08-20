#!/usr/bin/env python3
#
# byteify-strings.py - transform string literals to be Python 3 safe
#
# Copyright 2015 Gregory Szorc <gregory.szorc@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.


import argparse
import contextlib
import errno
import os
import sys
import tempfile
import token
import tokenize


def adjusttokenpos(t, ofs):
    """Adjust start/end column of the given token"""
    return t._replace(
        start=(t.start[0], t.start[1] + ofs), end=(t.end[0], t.end[1] + ofs)
    )


def replacetokens(tokens, opts):
    """Transform a stream of tokens from raw to Python 3.

    Returns a generator of possibly rewritten tokens.

    The input token list may be mutated as part of processing. However,
    its changes do not necessarily match the output token stream.
    """
    sysstrtokens = set()

    # The following utility functions access the tokens list and i index of
    # the for i, t enumerate(tokens) loop below
    def _isop(j, *o):
        """Assert that tokens[j] is an OP with one of the given values"""
        try:
            return tokens[j].type == token.OP and tokens[j].string in o
        except IndexError:
            return False

    def _findargnofcall(n):
        """Find arg n of a call expression (start at 0)

        Returns index of the first token of that argument, or None if
        there is not that many arguments.

        Assumes that token[i + 1] is '('.

        """
        nested = 0
        for j in range(i + 2, len(tokens)):
            if _isop(j, ')', ']', '}'):
                # end of call, tuple, subscription or dict / set
                nested -= 1
                if nested < 0:
                    return None
            elif n == 0:
                # this is the starting position of arg
                return j
            elif _isop(j, '(', '[', '{'):
                nested += 1
            elif _isop(j, ',') and nested == 0:
                n -= 1

        return None

    def _ensuresysstr(j):
        """Make sure the token at j is a system string

        Remember the given token so the string transformer won't add
        the byte prefix.

        Ignores tokens that are not strings. Assumes bounds checking has
        already been done.

        """
        k = j
        currtoken = tokens[k]
        while currtoken.type in (token.STRING, token.NEWLINE, tokenize.NL):
            k += 1
            if currtoken.type == token.STRING and currtoken.string.startswith(
                ("'", '"')
            ):
                sysstrtokens.add(currtoken)
            try:
                currtoken = tokens[k]
            except IndexError:
                break

    def _isitemaccess(j):
        """Assert the next tokens form an item access on `tokens[j]` and that
        `tokens[j]` is a name.
        """
        try:
            return (
                tokens[j].type == token.NAME
                and _isop(j + 1, '[')
                and tokens[j + 2].type == token.STRING
                and _isop(j + 3, ']')
            )
        except IndexError:
            return False

    def _ismethodcall(j, *methodnames):
        """Assert the next tokens form a call to `methodname` with a string
        as first argument on `tokens[j]` and that `tokens[j]` is a name.
        """
        try:
            return (
                tokens[j].type == token.NAME
                and _isop(j + 1, '.')
                and tokens[j + 2].type == token.NAME
                and tokens[j + 2].string in methodnames
                and _isop(j + 3, '(')
                and tokens[j + 4].type == token.STRING
            )
        except IndexError:
            return False

    coldelta = 0  # column increment for new opening parens
    coloffset = -1  # column offset for the current line (-1: TBD)
    parens = [(0, 0, 0, -1)]  # stack of (line, end-column, column-offset, type)
    ignorenextline = False  # don't transform the next line
    insideignoreblock = False  # don't transform until turned off
    for i, t in enumerate(tokens):
        # Compute the column offset for the current line, such that
        # the current line will be aligned to the last opening paren
        # as before.
        if coloffset < 0:
            lastparen = parens[-1]
            if t.start[1] == lastparen[1]:
                coloffset = lastparen[2]
            elif t.start[1] + 1 == lastparen[1] and lastparen[3] not in (
                token.NEWLINE,
                tokenize.NL,
            ):
                # fix misaligned indent of s/util.Abort/error.Abort/
                coloffset = lastparen[2] + (lastparen[1] - t.start[1])
            else:
                coloffset = 0

        # Reset per-line attributes at EOL.
        if t.type in (token.NEWLINE, tokenize.NL):
            yield adjusttokenpos(t, coloffset)
            coldelta = 0
            coloffset = -1
            if not insideignoreblock:
                ignorenextline = (
                    tokens[i - 1].type == token.COMMENT
                    and tokens[i - 1].string == "# no-py3-transform"
                )
            continue

        if t.type == token.COMMENT:
            if t.string == "# py3-transform: off":
                insideignoreblock = True
            if t.string == "# py3-transform: on":
                insideignoreblock = False

        if ignorenextline or insideignoreblock:
            yield adjusttokenpos(t, coloffset)
            continue

        # Remember the last paren position.
        if _isop(i, '(', '[', '{'):
            parens.append(t.end + (coloffset + coldelta, tokens[i + 1].type))
        elif _isop(i, ')', ']', '}'):
            parens.pop()

        # Convert most string literals to byte literals. String literals
        # in Python 2 are bytes. String literals in Python 3 are unicode.
        # Most strings in Mercurial are bytes and unicode strings are rare.
        # Rather than rewrite all string literals to use ``b''`` to indicate
        # byte strings, we apply this token transformer to insert the ``b``
        # prefix nearly everywhere.
        if t.type == token.STRING and t not in sysstrtokens:
            s = t.string

            # Preserve docstrings as string literals. This is inconsistent
            # with regular unprefixed strings. However, the
            # "from __future__" parsing (which allows a module docstring to
            # exist before it) doesn't properly handle the docstring if it
            # is b''' prefixed, leading to a SyntaxError. We leave all
            # docstrings as unprefixed to avoid this. This means Mercurial
            # components touching docstrings need to handle unicode,
            # unfortunately.
            if s[0:3] in ("'''", '"""'):
                # If it's assigned to something, it's not a docstring
                if not _isop(i - 1, '='):
                    yield adjusttokenpos(t, coloffset)
                    continue

            # If the first character isn't a quote, it is likely a string
            # prefixing character (such as 'b', 'u', or 'r'. Ignore.
            if s[0] not in ("'", '"'):
                yield adjusttokenpos(t, coloffset)
                continue

            # String literal. Prefix to make a b'' string.
            yield adjusttokenpos(t._replace(string='b%s' % t.string), coloffset)
            coldelta += 1
            continue

        # This looks like a function call.
        if t.type == token.NAME and _isop(i + 1, '('):
            fn = t.string

            # *attr() builtins don't accept byte strings to 2nd argument.
            if fn in (
                'getattr',
                'setattr',
                'hasattr',
                'safehasattr',
                'wrapfunction',
                'wrapclass',
                'addattr',
            ):
                arg1idx = _findargnofcall(1)
                if arg1idx is not None:
                    _ensuresysstr(arg1idx)

            # .encode() and .decode() on str/bytes/unicode don't accept
            # byte strings on Python 3.
            elif fn in ('encode', 'decode') and _isop(i - 1, '.'):
                for argn in range(2):
                    argidx = _findargnofcall(argn)
                    if argidx is not None:
                        _ensuresysstr(argidx)

            # It changes iteritems/values to items/values as they are not
            # present in Python 3 world.
            elif opts['dictiter'] and fn in ('iteritems', 'itervalues'):
                yield adjusttokenpos(t._replace(string=fn[4:]), coloffset)
                continue

        if t.type == token.NAME and t.string in opts['treat-as-kwargs']:
            if _isitemaccess(i):
                _ensuresysstr(i + 2)
            if _ismethodcall(i, 'get', 'pop', 'setdefault', 'popitem'):
                _ensuresysstr(i + 4)

        # Looks like "if __name__ == '__main__'".
        if (
            t.type == token.NAME
            and t.string == '__name__'
            and _isop(i + 1, '==')
        ):
            _ensuresysstr(i + 2)

        # Emit unmodified token.
        yield adjusttokenpos(t, coloffset)


def process(fin, fout, opts):
    tokens = tokenize.tokenize(fin.readline)
    tokens = replacetokens(list(tokens), opts)
    fout.write(tokenize.untokenize(tokens))


def tryunlink(fname):
    try:
        os.unlink(fname)
    except OSError as err:
        if err.errno != errno.ENOENT:
            raise


@contextlib.contextmanager
def editinplace(fname):
    n = os.path.basename(fname)
    d = os.path.dirname(fname)
    fp = tempfile.NamedTemporaryFile(
        prefix='.%s-' % n, suffix='~', dir=d, delete=False
    )
    try:
        yield fp
        fp.close()
        if os.name == 'nt':
            tryunlink(fname)
        os.rename(fp.name, fname)
    finally:
        fp.close()
        tryunlink(fp.name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        '--version', action='version', version='Byteify strings 1.0'
    )
    ap.add_argument(
        '-i',
        '--inplace',
        action='store_true',
        default=False,
        help='edit files in place',
    )
    ap.add_argument(
        '--dictiter',
        action='store_true',
        default=False,
        help='rewrite iteritems() and itervalues()',
    ),
    ap.add_argument(
        '--treat-as-kwargs',
        nargs="+",
        default=[],
        help="ignore kwargs-like objects",
    ),
    ap.add_argument('files', metavar='FILE', nargs='+', help='source file')
    args = ap.parse_args()
    opts = {
        'dictiter': args.dictiter,
        'treat-as-kwargs': set(args.treat_as_kwargs),
    }
    for fname in args.files:
        fname = os.path.realpath(fname)
        if args.inplace:
            with editinplace(fname) as fout:
                with open(fname, 'rb') as fin:
                    process(fin, fout, opts)
        else:
            with open(fname, 'rb') as fin:
                fout = sys.stdout.buffer
                process(fin, fout, opts)


if __name__ == '__main__':
    if sys.version_info[0:2] < (3, 7):
        print('This script must be run under Python 3.7+')
        sys.exit(3)
    main()
