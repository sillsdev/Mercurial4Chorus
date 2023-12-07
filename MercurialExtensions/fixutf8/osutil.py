from __future__ import print_function
import os, sys
import stat as _stat
from mercurial import util

def _mode_to_kind(mode):
	if _stat.S_ISREG(mode): return _stat.S_IFREG
	if _stat.S_ISDIR(mode): return _stat.S_IFDIR
	if _stat.S_ISLNK(mode): return _stat.S_IFLNK
	if _stat.S_ISBLK(mode): return _stat.S_IFBLK
	if _stat.S_ISCHR(mode): return _stat.S_IFCHR
	if _stat.S_ISFIFO(mode): return _stat.S_IFIFO
	if _stat.S_ISSOCK(mode): return _stat.S_IFSOCK
	return mode

def listdir(path, stat=False, skip=None):
	'''listdir(path, stat=False) -> list_of_tuples

	Return a sorted list containing information about the entries
	in the directory.

	If stat is True, each element is a 3-tuple:

	  (name, type, stat object)

	Otherwise, each element is a 2-tuple:

	  (name, type)
	'''
	result = []
	prefix = (path if type(path) is str else path.decode('utf-8'))
	if not prefix.endswith(os.sep):
		prefix += os.sep
	names = [name.decode('utf-8') for name in os.listdir(path)]
	names.sort()
	for fn in names:
		st = os.lstat(prefix + fn)
		if fn == skip and _stat.S_ISDIR(st.st_mode):
			return []
		if stat:
			result.append((fn.encode('utf-8'), _mode_to_kind(st.st_mode), st))
		else:
			result.append((fn.encode('utf-8'), _mode_to_kind(st.st_mode)))
	return result

# Replacement for osutil.statfiles which is returning wrong results in some cases
def statfiles(files):
	"""Stat each file in files. Yield each stat, or None if a file
	does not exist or has a type we don't care about.

	Cluster and cache stat per directory to minimize number of OS stat calls."""
	dircache = {}  # dirname -> filename -> status | None if file does not exist
	getkind = _stat.S_IFMT
	for nf in files:
		nf = util.normcase(nf)
		dir, base = os.path.split(nf)
		if not dir:
			dir = b'.'
		cache = dircache.get(dir, None)
		if cache is None:
			try:
				dmap = {
					util.normcase(n): s
					for n, k, s in listdir(dir, True)
					if getkind(s.st_mode) in util._wantedkinds
				}
			except (FileNotFoundError, NotADirectoryError):
				dmap = {}
			cache = dircache.setdefault(dir, dmap)
		yield cache.get(base, None)
