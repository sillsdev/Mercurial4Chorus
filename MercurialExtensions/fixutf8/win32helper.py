#
# Unicode WIN32 api calls
#
# Copyright 2015 Jason Naylor
#
# This software may be used and distributed according to the terms
# of the GNU General Public License, incorporated herein by reference.
#
# Portions of this file were originally licensed as follows:
#
#  Copyright (C) 2010-2011  The IPython Development Team
#
#  Distributed under the terms of the 3-Clause BSD
#

from __future__ import print_function
import sys
from ctypes import *
from ctypes.wintypes import *

# stdlib
import os, sys, threading
import ctypes, msvcrt

usecpmap = True
mapcp = None

# Using ctypes we can call the unicode versions of win32 api calls that
# python does not call.
if sys.platform == "win32" and windll:
	LPDWORD = POINTER(DWORD)
	LPHANDLE = POINTER(HANDLE)
	ULONG_PTR = POINTER(ULONG)

	class SECURITY_ATTRIBUTES(ctypes.Structure):
		_fields_ = [("nLength", DWORD),
					("lpSecurityDescriptor", LPVOID),
					("bInheritHandle", BOOL)]

	LPSECURITY_ATTRIBUTES = POINTER(SECURITY_ATTRIBUTES)

	class STARTUPINFO(ctypes.Structure):
		_fields_ = [("cb", DWORD),
					("lpReserved", LPCWSTR),
					("lpDesktop", LPCWSTR),
					("lpTitle", LPCWSTR),
					("dwX", DWORD),
					("dwY", DWORD),
					("dwXSize", DWORD),
					("dwYSize", DWORD),
					("dwXCountChars", DWORD),
					("dwYCountChars", DWORD),
					("dwFillAttribute", DWORD),
					("dwFlags", DWORD),
					("wShowWindow", WORD),
					("cbReserved2", WORD),
					("lpReserved2", LPVOID),
					("hStdInput", HANDLE),
					("hStdOutput", HANDLE),
					("hStdError", HANDLE)]

	LPSTARTUPINFO = POINTER(STARTUPINFO)

	LPWSTR = c_wchar_p
	LPCWSTR = c_wchar_p
	LPCSTR = c_char_p
	INT = c_int
	UINT = c_uint
	BOOL = INT
	DWORD = UINT
	HANDLE = c_void_p

	# Win32 API functions needed
	GetLastError = ctypes.windll.kernel32.GetLastError
	GetLastError.argtypes = []
	GetLastError.restype = DWORD

	WriteFile = ctypes.windll.kernel32.WriteFile
	WriteFile.argtypes = [HANDLE, LPVOID, DWORD, LPDWORD, LPVOID]
	WriteFile.restype = BOOL

	CommandLineToArgvW = ctypes.windll.shell32.CommandLineToArgvW
	CommandLineToArgvW.argtypes = [LPCWSTR, POINTER(ctypes.c_int)]
	CommandLineToArgvW.restype = POINTER(LPCWSTR)

	prototype = WINFUNCTYPE(LPCWSTR)
	GetCommandLine = prototype(("GetCommandLineW", windll.kernel32))

	prototype = WINFUNCTYPE(POINTER(LPCWSTR), LPCWSTR, POINTER(INT))
	CommandLineToArgv = prototype(("CommandLineToArgvW", windll.shell32))

	prototype = WINFUNCTYPE(BOOL, UINT)
	SetConsoleOutputCP = prototype(("SetConsoleOutputCP", windll.kernel32))

	prototype = WINFUNCTYPE(UINT)
	GetConsoleOutputCP = prototype(("GetConsoleOutputCP", windll.kernel32))

	prototype = WINFUNCTYPE(INT)
	GetLastError = prototype(("GetLastError", windll.kernel32))

	prototype = WINFUNCTYPE(HANDLE, DWORD)
	GetStdHandle = prototype(("GetStdHandle", windll.kernel32))

	prototype = WINFUNCTYPE(BOOL, HANDLE, LPCSTR, DWORD,
			POINTER(DWORD), DWORD)
	WriteFile = prototype(("WriteFile", windll.kernel32))

	prototype = WINFUNCTYPE(DWORD, DWORD, LPWSTR)
	GetCurrentDirectory = prototype(("GetCurrentDirectoryW", windll.kernel32))

	hStdOut = GetStdHandle(0xFFFFfff5)
	hStdErr = GetStdHandle(0xFFFFfff4)

	def getcwdwrapper(orig):
		chars = GetCurrentDirectory(0, None) + 1
		p = create_unicode_buffer(chars)
		if 0 == GetCurrentDirectory(chars, p):
			err = GetLastError()
			if err < 0:
				raise pywintypes.error(err, "GetCurrentDirectory",
						win32api.FormatMessage(err))
		return p.value

	def getcwdbwrapper(orig):
		return fromunicode(getcwdwrapper(orig))

	def InternalWriteFile(h, s):
		limit = 0x4000
		l = len(s)
		start = 0
		while start < l:
			end = start + limit
			buffer = s[start:end]
			c = DWORD(0)
			if not WriteFile(h, buffer, len(buffer), byref(c), 0):
				err = GetLastError()
				if err < 0:
					raise pywintypes.error(err, "WriteFile",
							win32api.FormatMessage(err))
				start = start + c.value + 1
			else:
				start = start + len(buffer)

	def consolehascp():
		return 0 != GetConsoleOutputCP()

	def rawprint(h, s):
		InternalWriteFile(h, s)

	def getUtf8NonConfigArgs():
		'''
		getargs() -> [args]

		Returns an array of utf8 encoded arguments passed on the command line.

		Skips any --config argument pairs since this is used in a method where
		those arguments are already removed
		'''
		c = INT(0)
		pargv = CommandLineToArgv(GetCommandLine(), byref(c))
		cleanArguments = []
		iterator = iter(range(1, c.value))
		for i in iterator:
			cleanArguments.append(fromunicode(pargv[i]))
		return cleanArguments
else:
	win32rawprint = False
	win32getargs = False
	hStdOut = 0
	hStdErr = 0

def uisetup(ui):
	global usecpmap, mapcp
	usecpmap = ui.config(b'fixutf8', b'usecpmap', usecpmap)
	if usecpmap:
		import cpmap
		mapcp = cpmap.reduce
