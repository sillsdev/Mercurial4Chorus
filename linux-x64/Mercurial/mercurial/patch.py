tabsplitter = re.compile(r'(\t+|[^\t]+)')
        isexec)) tuple. Data is None if file is missing/deleted.
        if self.opener.islink(fname):
            return (self.opener.readlink(fname), (True, False))

            isexec = self.opener.lstat(fname).st_mode & 0100 != 0
        try:
            return (self.opener.read(fname), (False, isexec))
        except IOError, e:
            if e.errno != errno.ENOENT:
                raise
            return None, None
            self.opener.setflags(fname, islink, isexec)
                self.opener.setflags(fname, False, True)
        self.opener.unlinkpath(fname, ignoremissing=True)
        return self.opener.lexists(fname)
            return None, None, None
            return None, None
        if self.copysource is None:
            data, mode = backend.getfile(self.fname)
        else:
            data, mode = store.getfile(self.copysource)[:2]
        if data is not None:
            self.exists = self.copysource is None or backend.exists(self.fname)
        else:
                    # FIXME: failing getfile has never been handled here
                    assert data is not None
                data, mode = backend.getfile(path)
                if data is None:
                    pass
    if patcher:
        return _externalpatch(ui, repo, patcher, patchname, strip,
                              files, similarity)
    return internalpatch(ui, repo, patchname, strip, files, eolmode,
                         similarity)
def diffallopts(ui, opts=None, untrusted=False, section='diff'):
    '''return diffopts with all features supported and parsed'''
    return difffeatureopts(ui, opts=opts, untrusted=untrusted, section=section,
                           git=True, whitespace=True, formatchanging=True)

diffopts = diffallopts

def difffeatureopts(ui, opts=None, untrusted=False, section='diff', git=False,
                    whitespace=False, formatchanging=False):
    '''return diffopts with only opted-in features parsed

    Features:
    - git: git-style diffs
    - whitespace: whitespace options like ignoreblanklines and ignorews
    - formatchanging: options that will likely break or cause correctness issues
      with most diff parsers
    '''
    def get(key, name=None, getter=ui.configbool, forceplain=None):
        if opts:
            v = opts.get(key)
            if v:
                return v
        if forceplain is not None and ui.plain():
            return forceplain
        return getter(section, name or key, None, untrusted=untrusted)

    # core options, expected to be understood by every diff parser
    buildopts = {
        'nodates': get('nodates'),
        'showfunc': get('show_function', 'showfunc'),
        'context': get('unified', getter=ui.config),
    }

    if git:
        buildopts['git'] = get('git')
    if whitespace:
        buildopts['ignorews'] = get('ignore_all_space', 'ignorews')
        buildopts['ignorewsamount'] = get('ignore_space_change',
                                          'ignorewsamount')
        buildopts['ignoreblanklines'] = get('ignore_blank_lines',
                                            'ignoreblanklines')
    if formatchanging:
        buildopts['text'] = opts and opts.get('text')
        buildopts['nobinary'] = get('nobinary')
        buildopts['noprefix'] = get('noprefix', forceplain=False)

    return mdiff.diffopts(**buildopts)
    revs = [hexfunc(node) for node in [ctx1.node(), ctx2.node()] if node]
            diffline = False
                # highlight tabs and trailing whitespace, but only in
                # changed lines
                diffline = True

                    if diffline:
                        for token in tabsplitter.findall(stripline):
                            if '\t' == token[0]:
                                yield (token, 'diff.tab')
                            else:
                                yield (token, label)
                    else:
                        yield (stripline, label)
    def addindexmeta(meta, oindex, nindex):
        meta.append('index %s..%s\n' % (oindex, nindex))
    if opts.noprefix:
        aprefix = bprefix = ''
    else:
        aprefix = 'a/'
        bprefix = 'b/'

            line = 'diff --git %s%s %s%s\n' % (aprefix, a, bprefix, b)
    date2 = util.datestr(ctx2.date())
    modifiedset, addedset, removedset = set(modified), set(added), set(removed)
    # Fix up modified and added, since merged-in additions appear as
    # modifications during merges
    for f in modifiedset.copy():
        if f not in ctx1:
            addedset.add(f)
            modifiedset.remove(f)
        binarydiff = False
        if f not in addedset:
        if f not in removedset:
            if f in addedset:
                        omode = gitmode[ctx1.flags(a)]
                        if a in removedset and a not in gone:
                if util.binary(to) or util.binary(tn):
                        binarydiff = True
            elif f in removedset:
                    if ((f in copy and copy[f] in addedset
                        (f in copyto and copyto[f] in addedset
                        continue
                                      gitmode[ctx1.flags(f)])
                            binarydiff = True
                oflag = ctx1.flags(f)
                        binarydiff = True
        if opts.git or revs:
            header.insert(0, diffline(join(a), join(b), revs))
        if binarydiff and not opts.nobinary:
            text = mdiff.b85diff(to, tn)
            if text and opts.git:
                addindexmeta(header, gitindex(to), gitindex(tn))
        else:
            text = mdiff.unidiff(to, date1,
                                 tn, date2,
                                 join(a), join(b), opts=opts)
        if header and (text or len(header) > 1):
            yield ''.join(header)
        if text:
            yield text