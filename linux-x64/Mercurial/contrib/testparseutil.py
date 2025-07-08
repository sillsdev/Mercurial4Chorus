# testparseutil.py - utilities to parse test script for check tools
#
#  Copyright 2018 FUJIWARA Katsunori <foozy@lares.dti.ne.jp> and others
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.


import abc
import builtins
import re

####################
# for Python3 compatibility (almost comes from mercurial/pycompat.py)


def identity(a):
    return a


def _rapply(f, xs):
    if xs is None:
        # assume None means non-value of optional data
        return xs
    if isinstance(xs, (list, set, tuple)):
        return type(xs)(_rapply(f, x) for x in xs)
    if isinstance(xs, dict):
        return type(xs)((_rapply(f, k), _rapply(f, v)) for k, v in xs.items())
    return f(xs)


def rapply(f, xs):
    if f is identity:
        # fast path mainly for py2
        return xs
    return _rapply(f, xs)


def bytestr(s):
    # tiny version of pycompat.bytestr
    return s.encode('latin1')


def sysstr(s):
    if isinstance(s, builtins.str):
        return s
    return s.decode('latin-1')


def opentext(f):
    return open(f)


def b2s(x):
    # convert BYTES elements in "x" to SYSSTR recursively
    return rapply(sysstr, x)


def writeout(data):
    # write "data" in BYTES into stdout
    sys.stdout.write(data)


def writeerr(data):
    # write "data" in BYTES into stderr
    sys.stderr.write(data)


####################


class embeddedmatcher:  # pytype: disable=ignored-metaclass
    """Base class to detect embedded code fragments in *.t test script"""

    __metaclass__ = abc.ABCMeta

    def __init__(self, desc):
        self.desc = desc

    @abc.abstractmethod
    def startsat(self, line):
        """Examine whether embedded code starts at line

        This can return arbitrary object, and it is used as 'ctx' for
        subsequent method invocations.
        """

    @abc.abstractmethod
    def endsat(self, ctx, line):
        """Examine whether embedded code ends at line"""

    @abc.abstractmethod
    def isinside(self, ctx, line):
        """Examine whether line is inside embedded code, if not yet endsat"""

    @abc.abstractmethod
    def ignores(self, ctx):
        """Examine whether detected embedded code should be ignored"""

    @abc.abstractmethod
    def filename(self, ctx):
        """Return filename of embedded code

        If filename isn't specified for embedded code explicitly, this
        returns None.
        """

    @abc.abstractmethod
    def codeatstart(self, ctx, line):
        """Return actual code at the start line of embedded code

        This might return None, if the start line doesn't contain
        actual code.
        """

    @abc.abstractmethod
    def codeatend(self, ctx, line):
        """Return actual code at the end line of embedded code

        This might return None, if the end line doesn't contain actual
        code.
        """

    @abc.abstractmethod
    def codeinside(self, ctx, line):
        """Return actual code at line inside embedded code"""


def embedded(basefile, lines, errors, matchers):
    """pick embedded code fragments up from given lines

    This is common parsing logic, which examines specified matchers on
    given lines.

    :basefile: a name of a file, from which lines to be parsed come.
    :lines: to be parsed (might be a value returned by "open(basefile)")
    :errors: an array, into which messages for detected error are stored
    :matchers: an array of embeddedmatcher objects

    This function yields '(filename, starts, ends, code)' tuple.

    :filename: a name of embedded code, if it is explicitly specified
               (e.g.  "foobar" of "cat >> foobar <<EOF").
               Otherwise, this is None
    :starts: line number (1-origin), at which embedded code starts (inclusive)
    :ends: line number (1-origin), at which embedded code ends (exclusive)
    :code: extracted embedded code, which is single-stringified

    >>> class ambigmatcher:
    ...     # mock matcher class to examine implementation of
    ...     # "ambiguous matching" corner case
    ...     def __init__(self, desc, matchfunc):
    ...         self.desc = desc
    ...         self.matchfunc = matchfunc
    ...     def startsat(self, line):
    ...         return self.matchfunc(line)
    >>> ambig1 = ambigmatcher('ambiguous #1',
    ...                       lambda l: l.startswith('  $ cat '))
    >>> ambig2 = ambigmatcher('ambiguous #2',
    ...                       lambda l: l.endswith('<< EOF\\n'))
    >>> lines = ['  $ cat > foo.py << EOF\\n']
    >>> errors = []
    >>> matchers = [ambig1, ambig2]
    >>> list(t for t in embedded('<dummy>', lines, errors, matchers))
    []
    >>> b2s(errors)
    ['<dummy>:1: ambiguous line for "ambiguous #1", "ambiguous #2"']

    """
    matcher = None
    ctx = filename = code = startline = None  # for pyflakes

    for lineno, line in enumerate(lines, 1):
        if not line.endswith('\n'):
            line += '\n'  # to normalize EOF line
        if matcher:  # now, inside embedded code
            if matcher.endsat(ctx, line):
                codeatend = matcher.codeatend(ctx, line)
                if codeatend is not None:
                    code.append(codeatend)
                if not matcher.ignores(ctx):
                    yield (filename, startline, lineno, ''.join(code))
                matcher = None
                # DO NOT "continue", because line might start next fragment
            elif not matcher.isinside(ctx, line):
                # this is an error of basefile
                # (if matchers are implemented correctly)
                errors.append(
                    '%s:%d: unexpected line for "%s"'
                    % (basefile, lineno, matcher.desc)
                )
                # stop extracting embedded code by current 'matcher',
                # because appearance of unexpected line might mean
                # that expected end-of-embedded-code line might never
                # appear
                matcher = None
                # DO NOT "continue", because line might start next fragment
            else:
                code.append(matcher.codeinside(ctx, line))
                continue

        # examine whether current line starts embedded code or not
        assert not matcher

        matched = []
        for m in matchers:
            ctx = m.startsat(line)
            if ctx:
                matched.append((m, ctx))
        if matched:
            if len(matched) > 1:
                # this is an error of matchers, maybe
                errors.append(
                    '%s:%d: ambiguous line for %s'
                    % (
                        basefile,
                        lineno,
                        ', '.join(['"%s"' % m.desc for m, c in matched]),
                    )
                )
                # omit extracting embedded code, because choosing
                # arbitrary matcher from matched ones might fail to
                # detect the end of embedded code as expected.
                continue
            matcher, ctx = matched[0]
            filename = matcher.filename(ctx)
            code = []
            codeatstart = matcher.codeatstart(ctx, line)
            if codeatstart is not None:
                code.append(codeatstart)
                startline = lineno
            else:
                startline = lineno + 1

    if matcher:
        # examine whether EOF ends embedded code, because embedded
        # code isn't yet ended explicitly
        if matcher.endsat(ctx, '\n'):
            codeatend = matcher.codeatend(ctx, '\n')
            if codeatend is not None:
                code.append(codeatend)
            if not matcher.ignores(ctx):
                yield (filename, startline, lineno + 1, ''.join(code))
        else:
            # this is an error of basefile
            # (if matchers are implemented correctly)
            errors.append(
                '%s:%d: unexpected end of file for "%s"'
                % (basefile, lineno, matcher.desc)
            )


# heredoc limit mark to ignore embedded code at check-code.py or so
heredocignorelimit = 'NO_CHECK_EOF'

# the pattern to match against cases below, and to return a limit mark
# string as 'lname' group
#
# - << LIMITMARK
# - << "LIMITMARK"
# - << 'LIMITMARK'
heredoclimitpat = r'\s*<<\s*(?P<lquote>["\']?)(?P<limit>\w+)(?P=lquote)'


class fileheredocmatcher(embeddedmatcher):
    """Detect "cat > FILE << LIMIT" style embedded code

    >>> matcher = fileheredocmatcher('heredoc .py file', r'[^<]+\\.py')
    >>> b2s(matcher.startsat('  $ cat > file.py << EOF\\n'))
    ('file.py', '  > EOF\\n')
    >>> b2s(matcher.startsat('  $ cat   >>file.py   <<EOF\\n'))
    ('file.py', '  > EOF\\n')
    >>> b2s(matcher.startsat('  $ cat>  \\x27any file.py\\x27<<  "EOF"\\n'))
    ('any file.py', '  > EOF\\n')
    >>> b2s(matcher.startsat("  $ cat > file.py << 'ANYLIMIT'\\n"))
    ('file.py', '  > ANYLIMIT\\n')
    >>> b2s(matcher.startsat('  $ cat<<ANYLIMIT>"file.py"\\n'))
    ('file.py', '  > ANYLIMIT\\n')
    >>> start = '  $ cat > file.py << EOF\\n'
    >>> ctx = matcher.startsat(start)
    >>> matcher.codeatstart(ctx, start)
    >>> b2s(matcher.filename(ctx))
    'file.py'
    >>> matcher.ignores(ctx)
    False
    >>> inside = '  > foo = 1\\n'
    >>> matcher.endsat(ctx, inside)
    False
    >>> matcher.isinside(ctx, inside)
    True
    >>> b2s(matcher.codeinside(ctx, inside))
    'foo = 1\\n'
    >>> end = '  > EOF\\n'
    >>> matcher.endsat(ctx, end)
    True
    >>> matcher.codeatend(ctx, end)
    >>> matcher.endsat(ctx, '  > EOFEOF\\n')
    False
    >>> ctx = matcher.startsat('  $ cat > file.py << NO_CHECK_EOF\\n')
    >>> matcher.ignores(ctx)
    True
    """

    _prefix = '  > '

    def __init__(self, desc, namepat):
        super().__init__(desc)

        # build the pattern to match against cases below (and ">>"
        # variants), and to return a target filename string as 'name'
        # group
        #
        # - > NAMEPAT
        # - > "NAMEPAT"
        # - > 'NAMEPAT'
        namepat = (
            r'\s*>>?\s*(?P<nquote>["\']?)(?P<name>%s)(?P=nquote)' % namepat
        )
        self._fileres = [
            # "cat > NAME << LIMIT" case
            re.compile(r' {2}\$ \s*cat' + namepat + heredoclimitpat),
            # "cat << LIMIT > NAME" case
            re.compile(r' {2}\$ \s*cat' + heredoclimitpat + namepat),
        ]

    def startsat(self, line):
        # ctx is (filename, END-LINE-OF-EMBEDDED-CODE) tuple
        for filere in self._fileres:
            matched = filere.match(line)
            if matched:
                return (
                    matched.group('name'),
                    '  > %s\n' % matched.group('limit'),
                )

    def endsat(self, ctx, line):
        return ctx[1] == line

    def isinside(self, ctx, line):
        return line.startswith(self._prefix)

    def ignores(self, ctx):
        return '  > %s\n' % heredocignorelimit == ctx[1]

    def filename(self, ctx):
        return ctx[0]

    def codeatstart(self, ctx, line):
        return None  # no embedded code at start line

    def codeatend(self, ctx, line):
        return None  # no embedded code at end line

    def codeinside(self, ctx, line):
        return line[len(self._prefix) :]  # strip prefix


####
# for embedded python script


class pydoctestmatcher(embeddedmatcher):
    """Detect ">>> code" style embedded python code

    >>> matcher = pydoctestmatcher()
    >>> startline = '  >>> foo = 1\\n'
    >>> matcher.startsat(startline)
    True
    >>> matcher.startsat('  ... foo = 1\\n')
    False
    >>> ctx = matcher.startsat(startline)
    >>> matcher.filename(ctx)
    >>> matcher.ignores(ctx)
    False
    >>> b2s(matcher.codeatstart(ctx, startline))
    'foo = 1\\n'
    >>> inside = '  >>> foo = 1\\n'
    >>> matcher.endsat(ctx, inside)
    False
    >>> matcher.isinside(ctx, inside)
    True
    >>> b2s(matcher.codeinside(ctx, inside))
    'foo = 1\\n'
    >>> inside = '  ... foo = 1\\n'
    >>> matcher.endsat(ctx, inside)
    False
    >>> matcher.isinside(ctx, inside)
    True
    >>> b2s(matcher.codeinside(ctx, inside))
    'foo = 1\\n'
    >>> inside = '  expected output\\n'
    >>> matcher.endsat(ctx, inside)
    False
    >>> matcher.isinside(ctx, inside)
    True
    >>> b2s(matcher.codeinside(ctx, inside))
    '\\n'
    >>> inside = '  \\n'
    >>> matcher.endsat(ctx, inside)
    False
    >>> matcher.isinside(ctx, inside)
    True
    >>> b2s(matcher.codeinside(ctx, inside))
    '\\n'
    >>> end = '  $ foo bar\\n'
    >>> matcher.endsat(ctx, end)
    True
    >>> matcher.codeatend(ctx, end)
    >>> end = '\\n'
    >>> matcher.endsat(ctx, end)
    True
    >>> matcher.codeatend(ctx, end)
    """

    _prefix = '  >>> '
    _prefixre = re.compile(r' {2}(>>>|\.\.\.) ')

    # If a line matches against not _prefixre but _outputre, that line
    # is "an expected output line" (= not a part of code fragment).
    #
    # Strictly speaking, a line matching against "(#if|#else|#endif)"
    # is also treated similarly in "inline python code" semantics by
    # run-tests.py. But "directive line inside inline python code"
    # should be rejected by Mercurial reviewers. Therefore, this
    # regexp does not matche against such directive lines.
    _outputre = re.compile(r' {2}$| {2}[^$]')

    def __init__(self):
        super().__init__("doctest style python code")

    def startsat(self, line):
        # ctx is "True"
        return line.startswith(self._prefix)

    def endsat(self, ctx, line):
        return not (self._prefixre.match(line) or self._outputre.match(line))

    def isinside(self, ctx, line):
        return True  # always true, if not yet ended

    def ignores(self, ctx):
        return False  # should be checked always

    def filename(self, ctx):
        return None  # no filename

    def codeatstart(self, ctx, line):
        return line[len(self._prefix) :]  # strip prefix '  >>> '/'  ... '

    def codeatend(self, ctx, line):
        return None  # no embedded code at end line

    def codeinside(self, ctx, line):
        if self._prefixre.match(line):
            return line[len(self._prefix) :]  # strip prefix '  >>> '/'  ... '
        return '\n'  # an expected output line is treated as an empty line


class pyheredocmatcher(embeddedmatcher):
    """Detect "python << LIMIT" style embedded python code

    >>> matcher = pyheredocmatcher()
    >>> b2s(matcher.startsat('  $ python << EOF\\n'))
    '  > EOF\\n'
    >>> b2s(matcher.startsat('  $ $PYTHON   <<EOF\\n'))
    '  > EOF\\n'
    >>> b2s(matcher.startsat('  $ "$PYTHON"<<  "EOF"\\n'))
    '  > EOF\\n'
    >>> b2s(matcher.startsat("  $ $PYTHON << 'ANYLIMIT'\\n"))
    '  > ANYLIMIT\\n'
    >>> matcher.startsat('  $ "$PYTHON" < EOF\\n')
    >>> start = '  $ python << EOF\\n'
    >>> ctx = matcher.startsat(start)
    >>> matcher.codeatstart(ctx, start)
    >>> matcher.filename(ctx)
    >>> matcher.ignores(ctx)
    False
    >>> inside = '  > foo = 1\\n'
    >>> matcher.endsat(ctx, inside)
    False
    >>> matcher.isinside(ctx, inside)
    True
    >>> b2s(matcher.codeinside(ctx, inside))
    'foo = 1\\n'
    >>> end = '  > EOF\\n'
    >>> matcher.endsat(ctx, end)
    True
    >>> matcher.codeatend(ctx, end)
    >>> matcher.endsat(ctx, '  > EOFEOF\\n')
    False
    >>> ctx = matcher.startsat('  $ python << NO_CHECK_EOF\\n')
    >>> matcher.ignores(ctx)
    True
    """

    _prefix = '  > '

    _startre = re.compile(
        r' {2}\$ (\$PYTHON|"\$PYTHON"|python).*' + heredoclimitpat
    )

    def __init__(self):
        super().__init__("heredoc python invocation")

    def startsat(self, line):
        # ctx is END-LINE-OF-EMBEDDED-CODE
        matched = self._startre.match(line)
        if matched:
            return '  > %s\n' % matched.group('limit')

    def endsat(self, ctx, line):
        return ctx == line

    def isinside(self, ctx, line):
        return line.startswith(self._prefix)

    def ignores(self, ctx):
        return '  > %s\n' % heredocignorelimit == ctx

    def filename(self, ctx):
        return None  # no filename

    def codeatstart(self, ctx, line):
        return None  # no embedded code at start line

    def codeatend(self, ctx, line):
        return None  # no embedded code at end line

    def codeinside(self, ctx, line):
        return line[len(self._prefix) :]  # strip prefix


_pymatchers = [
    pydoctestmatcher(),
    pyheredocmatcher(),
    # use '[^<]+' instead of '\S+', in order to match against
    # paths including whitespaces
    fileheredocmatcher('heredoc .py file', r'[^<]+\.py'),
]


def pyembedded(basefile, lines, errors):
    return embedded(basefile, lines, errors, _pymatchers)


####
# for embedded shell script

_shmatchers = [
    # use '[^<]+' instead of '\S+', in order to match against
    # paths including whitespaces
    fileheredocmatcher('heredoc .sh file', r'[^<]+\.sh'),
]


def shembedded(basefile, lines, errors):
    return embedded(basefile, lines, errors, _shmatchers)


####
# for embedded hgrc configuration

_hgrcmatchers = [
    # use '[^<]+' instead of '\S+', in order to match against
    # paths including whitespaces
    fileheredocmatcher(
        'heredoc hgrc file', r'(([^/<]+/)+hgrc|\$HGRCPATH|\${HGRCPATH})'
    ),
]


def hgrcembedded(basefile, lines, errors):
    return embedded(basefile, lines, errors, _hgrcmatchers)


####

if __name__ == "__main__":
    import optparse
    import sys

    def showembedded(basefile, lines, embeddedfunc, opts):
        errors = []
        for name, starts, ends, code in embeddedfunc(basefile, lines, errors):
            if not name:
                name = '<anonymous>'
            writeout("%s:%d: %s starts\n" % (basefile, starts, name))
            if opts.verbose and code:
                writeout("  |%s\n" % "\n  |".join(l for l in code.splitlines()))
            writeout("%s:%d: %s ends\n" % (basefile, ends, name))
        for e in errors:
            writeerr("%s\n" % e)
        return len(errors)

    def applyembedded(args, embeddedfunc, opts):
        ret = 0
        if args:
            for f in args:
                with opentext(f) as fp:
                    if showembedded(f, fp, embeddedfunc, opts):
                        ret = 1
        else:
            lines = [l for l in sys.stdin.readlines()]
            if showembedded('<stdin>', lines, embeddedfunc, opts):
                ret = 1
        return ret

    commands = {}

    def command(name, desc):
        def wrap(func):
            commands[name] = (desc, func)

        return wrap

    @command("pyembedded", "detect embedded python script")
    def pyembeddedcmd(args, opts):
        return applyembedded(args, pyembedded, opts)

    @command("shembedded", "detect embedded shell script")
    def shembeddedcmd(args, opts):
        return applyembedded(args, shembedded, opts)

    @command("hgrcembedded", "detect embedded hgrc configuration")
    def hgrcembeddedcmd(args, opts):
        return applyembedded(args, hgrcembedded, opts)

    availablecommands = "\n".join(
        ["  - %s: %s" % (key, value[0]) for key, value in commands.items()]
    )

    parser = optparse.OptionParser(
        """%prog COMMAND [file ...]

Pick up embedded code fragments from given file(s) or stdin, and list
up start/end lines of them in standard compiler format
("FILENAME:LINENO:").

Available commands are:
"""
        + availablecommands
        + """
"""
    )
    parser.add_option(
        "-v",
        "--verbose",
        help="enable additional output (e.g. actual code)",
        action="store_true",
    )
    (opts, args) = parser.parse_args()

    if not args or args[0] not in commands:
        parser.print_help()
        sys.exit(255)

    sys.exit(commands[args[0]][1](args[1:], opts))
