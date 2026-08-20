#ifndef CONTRIB_FUZZ_FUZZUTIL_H
#define CONTRIB_FUZZ_FUZZUTIL_H
#include <iostream>
#include <memory>
#include <stdint.h>

/* Try and use std::optional, but failing that assume we'll have a
 * workable https://abseil.io/ install on the include path to get
 * their backport of std::optional. */
#ifdef __has_include
#if __has_include(<optional>) && __cplusplus >= 201703L
#include <optional>
#define CONTRIB_FUZZ_HAVE_STD_OPTIONAL
#endif
#endif
#ifdef CONTRIB_FUZZ_HAVE_STD_OPTIONAL
namespace contrib
{
using std::nullopt;
using std::optional;
} /* namespace contrib */
#else
#include "third_party/absl/types/optional.h"
namespace contrib
{
using absl::nullopt;
using absl::optional;
} /* namespace contrib */
#endif

/* set DEBUG to 1 for a few debugging prints, or 2 for a lot */
#define DEBUG 0
#define LOG(level)                                                             \
	if (level <= DEBUG)                                                    \
	std::cout

#endif /* CONTRIB_FUZZ_FUZZUTIL_H */
