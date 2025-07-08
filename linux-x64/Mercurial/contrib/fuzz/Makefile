CC ?= clang
CXX ?= clang++

# By default, use our own standalone_fuzz_target_runner.
# This runner does no fuzzing, but simply executes the inputs
# provided via parameters.
# Run e.g. "make all LIB_FUZZING_ENGINE=/path/to/libFuzzer.a"
# to link the fuzzer(s) against a real fuzzing engine.
#
# OSS-Fuzz will define its own value for LIB_FUZZING_ENGINE.
LIB_FUZZING_ENGINE ?= standalone_fuzz_target_runner.o

# Default to Python 3.
#
# Windows ships Python 3 as `python.exe`, which may not be on PATH.  py.exe is.
ifeq ($(OS),Windows_NT)
PYTHON?=py -3
else
PYTHON?=python3
endif

PYTHON_CONFIG ?= $$OUT/sanpy/bin/python-config
PYTHON_CONFIG_FLAGS ?= --ldflags --embed

CXXFLAGS += -Wno-deprecated-register

all: standalone_fuzz_target_runner.o oss-fuzz

standalone_fuzz_target_runner.o: standalone_fuzz_target_runner.cc

$$OUT/%_fuzzer_seed_corpus.zip: %_corpus.py
	$(PYTHON) $< $@

pyutil.o: pyutil.cc pyutil.h
	$(CXX) $(CXXFLAGS) -g -O1 \
	  `$(PYTHON_CONFIG) --cflags` \
	  -I../../mercurial -c -o pyutil.o pyutil.cc

bdiff-oss-fuzz.o: ../../mercurial/bdiff.c
	$(CC) $(CFLAGS) -c -o bdiff-oss-fuzz.o ../../mercurial/bdiff.c

bdiff_fuzzer: bdiff.cc bdiff-oss-fuzz.o
	$(CXX) $(CXXFLAGS) -std=c++17 -I../../mercurial bdiff.cc \
	  bdiff-oss-fuzz.o $(LIB_FUZZING_ENGINE) -o \
	  $$OUT/bdiff_fuzzer

mpatch.o: ../../mercurial/mpatch.c
	$(CC) -g -O1 -fsanitize=fuzzer-no-link,address -c -o mpatch.o \
	  ../../mercurial/mpatch.c

mpatch-oss-fuzz.o: ../../mercurial/mpatch.c
	$(CC) $(CFLAGS) -c -o mpatch-oss-fuzz.o ../../mercurial/mpatch.c

mpatch_fuzzer: mpatch.cc mpatch-oss-fuzz.o $$OUT/mpatch_fuzzer_seed_corpus.zip
	$(CXX) $(CXXFLAGS) -std=c++17 -I../../mercurial mpatch.cc \
	  mpatch-oss-fuzz.o $(LIB_FUZZING_ENGINE) -o \
	  $$OUT/mpatch_fuzzer

fuzz-x%.o: ../../mercurial/thirdparty/xdiff/x%.c ../../mercurial/thirdparty/xdiff/*.h
	$(CC) $(CFLAGS) -c \
	  -o $@ \
	  $<

xdiff_fuzzer: xdiff.cc fuzz-xdiffi.o fuzz-xprepare.o fuzz-xutils.o 
	$(CXX) $(CXXFLAGS) -std=c++17 -I../../mercurial xdiff.cc \
	  fuzz-xdiffi.o fuzz-xprepare.o fuzz-xutils.o \
	  $(LIB_FUZZING_ENGINE) -o $$OUT/xdiff_fuzzer

parsers-%.o: ../../mercurial/cext/%.c
	$(CC) -I../../mercurial `$(PYTHON_CONFIG) --cflags` $(CFLAGS) -c \
	  -o $@ $<

PARSERS_OBJS=parsers-manifest.o parsers-charencode.o parsers-parsers.o parsers-dirs.o parsers-pathencode.o parsers-revlog.o

dirs_fuzzer: dirs.cc pyutil.o $(PARSERS_OBJS) $$OUT/dirs_fuzzer_seed_corpus.zip
	$(CXX) $(CXXFLAGS) `$(PYTHON_CONFIG) --cflags` \
	  -Wno-register -Wno-macro-redefined \
	  -I../../mercurial dirs.cc \
	  pyutil.o $(PARSERS_OBJS) \
	  $(LIB_FUZZING_ENGINE) `$(PYTHON_CONFIG) $(PYTHON_CONFIG_FLAGS)` \
	  -o $$OUT/dirs_fuzzer

fncache_fuzzer: fncache.cc 
	$(CXX) $(CXXFLAGS) `$(PYTHON_CONFIG) --cflags` \
	  -Wno-register -Wno-macro-redefined \
	  -I../../mercurial fncache.cc \
	  pyutil.o $(PARSERS_OBJS) \
	  $(LIB_FUZZING_ENGINE) `$(PYTHON_CONFIG) $(PYTHON_CONFIG_FLAGS)` \
	  -o $$OUT/fncache_fuzzer

jsonescapeu8fast_fuzzer: jsonescapeu8fast.cc pyutil.o $(PARSERS_OBJS)
	$(CXX) $(CXXFLAGS) `$(PYTHON_CONFIG) --cflags` \
	  -Wno-register -Wno-macro-redefined \
	  -I../../mercurial jsonescapeu8fast.cc \
	  pyutil.o $(PARSERS_OBJS) \
	  $(LIB_FUZZING_ENGINE) `$(PYTHON_CONFIG) $(PYTHON_CONFIG_FLAGS)` \
	  -o $$OUT/jsonescapeu8fast_fuzzer

manifest_fuzzer: manifest.cc pyutil.o $(PARSERS_OBJS) $$OUT/manifest_fuzzer_seed_corpus.zip
	$(CXX) $(CXXFLAGS) `$(PYTHON_CONFIG) --cflags` \
	  -Wno-register -Wno-macro-redefined \
	  -I../../mercurial manifest.cc \
	  pyutil.o $(PARSERS_OBJS) \
	  $(LIB_FUZZING_ENGINE) `$(PYTHON_CONFIG) $(PYTHON_CONFIG_FLAGS)` \
	  -o $$OUT/manifest_fuzzer

revlog_fuzzer: revlog.cc pyutil.o $(PARSERS_OBJS) $$OUT/revlog_fuzzer_seed_corpus.zip
	$(CXX) $(CXXFLAGS) `$(PYTHON_CONFIG) --cflags` \
	  -Wno-register -Wno-macro-redefined \
	  -I../../mercurial revlog.cc \
	  pyutil.o $(PARSERS_OBJS) \
	  $(LIB_FUZZING_ENGINE) `$(PYTHON_CONFIG) $(PYTHON_CONFIG_FLAGS)` \
	  -o $$OUT/revlog_fuzzer

dirstate_fuzzer: dirstate.cc pyutil.o $(PARSERS_OBJS) $$OUT/dirstate_fuzzer_seed_corpus.zip
	$(CXX) $(CXXFLAGS) `$(PYTHON_CONFIG) --cflags` \
	  -Wno-register -Wno-macro-redefined \
	  -I../../mercurial dirstate.cc \
	  pyutil.o $(PARSERS_OBJS) \
	  $(LIB_FUZZING_ENGINE) `$(PYTHON_CONFIG) $(PYTHON_CONFIG_FLAGS)` \
	  -o $$OUT/dirstate_fuzzer

fm1readmarkers_fuzzer: fm1readmarkers.cc pyutil.o $(PARSERS_OBJS) $$OUT/fm1readmarkers_fuzzer_seed_corpus.zip
	$(CXX) $(CXXFLAGS) `$(PYTHON_CONFIG) --cflags` \
	  -Wno-register -Wno-macro-redefined \
	  -I../../mercurial fm1readmarkers.cc \
	  pyutil.o $(PARSERS_OBJS) \
	  $(LIB_FUZZING_ENGINE) `$(PYTHON_CONFIG) $(PYTHON_CONFIG_FLAGS)` \
	  -o $$OUT/fm1readmarkers_fuzzer

clean:
	$(RM) *.o *_fuzzer \
	  bdiff \
	  mpatch \
	  xdiff

oss-fuzz: bdiff_fuzzer mpatch_fuzzer xdiff_fuzzer dirs_fuzzer fncache_fuzzer jsonescapeu8fast_fuzzer manifest_fuzzer revlog_fuzzer dirstate_fuzzer fm1readmarkers_fuzzer

.PHONY: all clean oss-fuzz
