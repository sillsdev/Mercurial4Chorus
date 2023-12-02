# fixutf8.py - Make Mercurial compatible with non-utf8 locales
#
# Copyright 2009 Stefan Rusek
# Copyright 2015 SIL International
#
# This software may be used and distributed according to the terms
# of the GNU General Public License, incorporated herein by reference.
#
# To load the extension, add it to your .hgrc file:
#
# [extension]
#   fixutf8 =
#
# This module needs no special configuration.

'''
Fix incompatibilities with non-utf8 locales

No special configuration is needed.
'''

#
# How it works:
#
#  There are 2 ways for strings to get into HG, either
# via that command line or filesystem filename. We want
# to make sure that both of those work.
#
#  We use the WIN32 GetCommandLineW() to get the unicode
# version of the command line. And we wrapp all the
# places where we send or get filenames from the os and
# make sure we send UCS-16 to windows and convert back
# to UTF8.
#
#  There are bugs in Python that make print() and
# sys.stdout.write() barf on unicode or utf8 when the
# output codepage is set to 65001 (UTF8). So we do all
# outputing via WriteFile() with the code page set to
# 65001. The trick is to save the existing codepage,
# and restore it before we return back to python.
#
#  The result is that all of our strings are UTF8 all
# the time, and never explicitly converted to anything
# else.
#
from __future__ import print_function
import builtins
from io import FileIO
import sys
import traceback
from mercurial import extensions, scmutil
import mercurial.ui as _ui
from mercurial import encoding, pycompat
# The version of Mercurial first released with chorus allowed branches to
# be named with just a number. We used branch names to handle non-simultaneous
# upgrades to new model versions. Some chorus clients handled this by using
# the model version as the branch name. Changing the branch name triggers our
# model version upgrade logic. To avoid unnecessarily simulating an upgrade
# we will wrap and disable the check which forbids creating number only branches
# since Mercurial has to support them for backward compatibility in any case.
# This number only branching needs to happen on all platforms so we execute it first.
# The remainder of this plug-in is needed only on windows and is not executed on Linux.
# We can't add an extension without breaking backward compatability so we have added the number branch here.
def allownumberbranches_uisetup():
	extensions.wrapfunction(scmutil, "checknewlabel", checklabelwrapper)

def checklabelwrapper(orig, repo, lbl, kind):
	try:
		int(lbl)
		pass #let number only branches through without complaint
	except ValueError:
		orig(repo, lbl, kind) #let Mercurial test all other branch names

# extsetup is only required on windows
def extsetup(ui):
	if sys.platform == 'win32':
		winextsetup(ui)

# uisetup on linux needs only to enable number only branches
def uisetup(ui):
	allownumberbranches_uisetup()
	# Handle the actual fixutf8 part of the extension if on windows
	if sys.platform == 'win32':
		winuisetup(ui)

if sys.platform == 'win32':
	import os, shutil

	from mercurial import demandimport

	demandimport.IGNORES.update(["win32helper", "osutil"])

	try:
		import win32helper
		from mercurial.pure import osutil as pureosutil
	except ImportError:
		sys.path.append(os.path.dirname(__file__))
		import win32helper
		from mercurial.pure import osutil as pureosutil

	stdout = sys.stdout

	from mercurial import windows, pathutil, util, dispatch, extensions, i18n, scmutil
	from mercurial.pure import osutil
	from mercurial.utils import procutil
	import mercurial.ui as _ui

	def mapconvert(convert, canconvert, doc, fallback=None):
		'''
		mapconvert(convert, canconvert, doc) ->
			(a -> a)

		Returns a function that converts arbitrary arguments
		using the specified conversion function.

		convert is a function to do actual convertions.
		canconvert returns true if the arg can be converted.
		doc is the doc string to attach to created function.

		The resulting function will return a converted list or
		tuple if passed a list or tuple.
		'''

		def _convert(arg):
			if canconvert(arg):
				# print("Converting %s with %s" % (arg, doc))
				try:
					return convert(arg)
				except UnicodeDecodeError:
					if(fallback):
						try:
							return fallback(arg)
						except UnicodeDecodeError:
							# print("twice failed to convert %s with %s" % (arg, doc))
							raise
					# print("failed to convert %s with %s" % (arg, doc))
					raise
			elif isinstance(arg, os.stat_result):
				# Do not convert stat_result results, as they are not tuples even though they act like tuples
				return arg
			elif isinstance(arg, tuple):
				return tuple(map(_convert, arg))
			elif isinstance(arg, list):
				# Mercurial 6.5.1 sometimes does "if os.listdir(somepath):" to check if a directory is empty
				# But a map object is always true even if the list it's mapping over is empty, so this creates false positives
				# So we return an empty list if the original was empty, otherwise we map over the original
				# if arg:
					return map(_convert, arg)
				# else:
				#     return []
			return arg

		_convert.__doc__ = doc
		return _convert

	tounicode1252 = mapconvert(
		lambda s: s.decode('cp1252', 'strict'),
		lambda s: isinstance(s, bytes),
		"Convert a CP1252 byte string to Unicode")

	tounicode = mapconvert(
		lambda s: s.decode('utf-8', 'strict'),
		lambda s: isinstance(s, bytes),
		"Convert a UTF-8 byte string to Unicode")

	fromunicode = mapconvert(
		lambda s: s.encode('utf-8', 'ignore'),
		lambda s: isinstance(s, str),
		"Convert a Unicode string to a UTF-8 byte string")

	fixbytes = mapconvert(
		lambda s: s.decode('utf-8', 'strict').encode('utf-8', 'ignore'),
		lambda s: isinstance(s, bytes),
		"Convert a CP-1252 byte string to UTF-8 bytes",
		fallback=lambda s: s.decode('cp1252', 'strict').encode('utf-8', 'ignore'))

	win32helper.fromunicode = fromunicode
	win32helper.tounicode = tounicode
	win32helper.tounicode1252 = tounicode1252
	win32helper.fixbytes = fixbytes

def test():
	if sys.platform != 'win32':
		return
	print(win32helper.getargs())
	print(sys.argv)

	uargs = ['P:\\hg-fixutf8\\fixutf8.py', 'thi\xc5\x9b', 'i\xc5\x9b',
			 '\xc4\x85', 't\xc4\x99\xc5\x9bt']
	for s in uargs:
		win32helper.rawprint(win32helper.hStdOut, s + "\n")

def utf8wrapper(orig, *args, **kargs):
	try:
		return fromunicode(orig(*tounicode(args), **kargs))
	except UnicodeDecodeError:
		try:
			return fromunicode(orig(*tounicode1252(args), **tounicode1252(kargs)))
		except Exception as e:
			# print("utf8wrapper exception while calling %s(%s), %s" % (orig.__name__, repr(args), str(e)))
			raise
	except Exception as e:
		# print("utf8wrapper exception while calling %s, %s" % (orig.__name__, repr(args), str(e)))
		raise

def byteswrapper(orig, *args, **kargs):
	return orig(*fromunicode(args), **kargs)

def normalizewrapper(orig, *args, **kargs):
	if any(type(arg) == str for arg in args):
		return utf8wrapper(orig, *args, **kargs)
	else:
		return orig(*args, **kargs)

def utf8resultwrapper(orig, *args, **kargs):
	# TODO: Can we get rid of this? It's only used to wrap realpath, and we might be able to fold this into realpathwrapper
	try:
		return fromunicode(tounicode(orig(*tounicode(args), **kargs)))
	except UnicodeDecodeError:
		return fromunicode(tounicode1252(orig(*tounicode1252(args), **tounicode1252(kargs))))

import stat

def winuisetup(ui):
	if sys.platform != 'win32' or not win32helper.consolehascp():
		return

	win32helper.uisetup(ui)

	try:
		from mercurial import encoding

		encoding.encoding = 'utf8'
	except ImportError:
		util._encoding = "utf-8"

	def localize(h):
		if hasattr(ui, '_buffers'):
			getbuffers = lambda ui: ui._buffers
		else:
			getbuffers = lambda ui: ui.buffers

		def f(orig, ui, *args, **kwds):
			if not getbuffers(ui):
				win32helper.rawprint(h, b''.join(args))
			else:
				orig(ui, *args, **kwds)

		return f

	extensions.wrapfunction(_ui.ui, "write", localize(win32helper.hStdOut))
	extensions.wrapfunction(_ui.ui, "write_err", localize(win32helper.hStdErr))

def winextsetup(ui):
	# oldlistdir = osutil.listdir

	# osutil.listdir = pureosutil.listdir # force pure listdir
	# extensions.wrapfunction(osutil, "listdir", utf8wrapper)
	import osutil
	pureosutil.listdir = osutil.listdir
	util.listdir = osutil.listdir
	util._wantedkinds = windows._wantedkinds

	# Replacement for osutil.statfiles which is returning wrong results in some cases
	def statfiles(files):
		"""Stat each file in files. Yield each stat, or None if a file
		does not exist or has a type we don't care about.

		Cluster and cache stat per directory to minimize number of OS stat calls."""
		dircache = {}  # dirname -> filename -> status | None if file does not exist
		getkind = stat.S_IFMT
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
						for n, k, s in util.listdir(dir, True)
						if getkind(s.st_mode) in util._wantedkinds
					}
				except (FileNotFoundError, NotADirectoryError):
					dmap = {}
				cache = dircache.setdefault(dir, dmap)
			yield cache.get(base, None)

	def normcase_utf8(path):
		try:
			return fromunicode(tounicode(path)).upper()
		except UnicodeDecodeError:
			try:
				return fromunicode(tounicode1252(path)).upper()
			except Exception as e:
				raise
		except Exception as e:
			raise

	windows.normcase = normcase_utf8
	util.normcase = normcase_utf8

	# only get the real command line args if we are passed a real ui object
	def disp_parse(orig, ui, args):
		if type(ui) == _ui.ui:
			args = win32helper.getUtf8NonConfigArgs()[-len(args):]
		return orig(ui, args)

	extensions.wrapfunction(dispatch, "_parse", disp_parse)

	class posixfile_utf8(FileIO):
		def __init__(self, name, mode='rb'):
			super(posixfile_utf8, self).__init__(tounicode(name), tounicode(mode))

	util.posixfile = posixfile_utf8

	# class ObjectWrapper(BaseClass):
	# 	def __init__(self, baseObject):
	# 		self.__class__ = type(baseObject.__class__.__name__,
	# 							(self.__class__, baseObject.__class__),
	# 							{})
	# 		self.__dict__ = baseObject.__dict__

	# 	def overriddenMethod(self):
	# 		...

	if util.atomictempfile:
		class atomictempfile_utf8(posixfile_utf8):
			"""file-like object that atomically updates a file

			All writes will be redirected to a temporary copy of the original
			file.  When rename is called, the copy is renamed to the original
			name, making the changes visible.
			"""

			def __init__(self, name, mode, createmode=None, checkambig=False):
				self.__name = name
				self.temp = util.mktempcopy(name, emptyok=(b'w' in mode),
											createmode=createmode)
				posixfile_utf8.__init__(self, self.temp, mode)

			def close(self):
				if not self.closed:
					posixfile_utf8.close(self)
					util.rename(self.temp, util.localpath(self.__name))

			def __del__(self):
				if not self.closed:
					try:
						os.unlink(self.temp)
					except: pass
					posixfile_utf8.close(self)

		util.atomictempfile = atomictempfile_utf8

	# wrap the os and path functions
	def wrapnames(mod, *names):
		for name in names:
			if hasattr(mod, name):
				extensions.wrapfunction(mod, name, utf8wrapper)

	def wrapbytes(mod, *names):
		for name in names:
			if hasattr(mod, name):
				extensions.wrapfunction(mod, name, byteswrapper)

	def wrapnormalize(mod, *names):
		for name in names:
			if hasattr(mod, name):
				extensions.wrapfunction(mod, name, normalizewrapper)

	def wrapresult(mod, *names):
		for name in names:
			if hasattr(mod, name):
				extensions.wrapfunction(mod, name, utf8resultwrapper)

	def realpathwrapper(orig, *args, **kargs):
		result = orig(*args, **kargs)
		if type(result) is bytes:
			suffix = os.path.sep.encode('utf-8') + b'.'
		else:
			suffix = os.path.sep + '.'
		if result.endswith(suffix):
			return result[:-len(suffix)]
		else:
			return result

	wrapnormalize(os.path, 'join')
	extensions.wrapfunction(os.path, 'realpath', realpathwrapper)
	wrapnames(os.path, 'normpath', 'normcase', 'islink', 'dirname', 'basename', 'realpath',
			  'isdir', 'isfile', 'exists', 'abspath', 'relpath', 'samefile', 'split')
	# wrapresult(os.path, 'realpath')
	import tempfile
	wrapnames(tempfile, 'mkdtemp', 'mkstemp')
	wrapnames(os, 'makedirs', 'lstat', 'unlink', 'chmod', 'stat',
			  'mkdir', 'rename', 'removedirs', 'setcwd', 'open',
			  'listdir', 'chdir', 'remove', 'access', 'rmdir', 'tempnam', 'utime' )
	wrapnames(shutil, 'copyfile', 'copymode', 'copystat')
	wrapbytes(util, 'pathto')
	util.statfiles = statfiles
	from mercurial import encoding, pycompat
	wrapnames(encoding, 'unitolocal', 'strtolocal')
	extensions.wrapfunction(os, 'getcwd', win32helper.getcwdwrapper)
	extensions.wrapfunction(os, 'getcwdb', win32helper.getcwdbwrapper)
	wrapnames(builtins, 'open') # Or (builtins, 'open') ??
	def sysstr_safeutf8(s):
		if isinstance(s, builtins.str):
			return s
		return s.decode('utf8', 'backslashreplace')

	pycompat.sysstr = sysstr_safeutf8

if __name__ == "__main__":
	test()

