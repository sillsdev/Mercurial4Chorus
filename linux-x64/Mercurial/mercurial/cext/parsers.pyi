from typing import (
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

version: int
versionerrortext: str

class DirstateItem:
    __doc__: str

    def __len__(self) -> int: ...
    def __getitem__(self, key: int) -> Union[bytes, int]: ...

# From dirs.c

class dirs:
    __doc__: str
    def __init__(self, source, skipchar: bytes): ...
    def __iter__(self) -> Iterator[bytes]: ...
    def addpath(self, path: bytes) -> None: ...
    def delpath(self, path: bytes) -> None: ...

# From manifest.c
class lazymanifest:
    def __init__(self, nodelen: int, data: bytes): ...
    def __iter__(self) -> Iterator[bytes]: ...

    def __len__(self) -> int: ...
    def __getitem__(self, item: bytes) -> Optional[Tuple[bytes, bytes]]: ...
    def __setitem__(self, key: bytes, value: Tuple[bytes, bytes]) -> None: ...
    def __delitem__(self, key: bytes) -> None: ...

    def iterkeys(self) -> Iterator[bytes]: ...
    def iterentries(self) -> Iterator[Tuple[bytes, bytes, bytes]]: ...
    def copy(self) -> lazymanifest: ...
    def filtercopy(self, matchfn: Callable[[bytes], bool]) -> lazymanifest: ...
    def diff(self, other: lazymanifest, clean: Optional[bool]) -> Dict[bytes, Tuple[bytes, Tuple]]: ...
    def text(self) -> bytes: ...

# From revlog.c

class index:
    __doc__: str

    nodemap: Dict[bytes, int]

    def ancestors(self, *args: int) -> Iterator[int]: ...
    def commonancestorsheads(self, *args: int) -> List[int]: ...
    def clearcaches(self) -> None: ...
    def get(self, value: bytes) -> Optional[int]: ...
    def get_rev(self, value: bytes) -> Optional[int]: ...
    def has_node(self, value: Union[int, bytes]) -> bool: ...
    def rev(self, node: bytes) -> int: ...
    def computephasesmapsets(self, root: Dict[int, Set[bytes]]) -> Tuple[int, Dict[int, Set[bytes]]]: ...
    def reachableroots2(self, minroot: int, heads: List[int], roots: List[int], includepath: bool) -> List[int]: ...
    def headrevs(self, filteredrevs: Optional[List[int]]) -> List[int]: ...
    def headrevsfiltered(self, filteredrevs: Optional[List[int]]) -> List[int]: ...
    def issnapshot(self, value: int) -> bool: ...
    def findsnapshots(self, cache: Dict[int, List[int]], start_rev: int) -> None: ...
    def deltachain(self, rev: int, stop: int, generaldelta: bool) -> Tuple[List[int], bool]: ...
    def slicechunktodensity(self, revs: List[int], targetdensity: float, mingapsize: int) -> List[List[int]]: ...
    def append(self, value: Tuple[int, int, int, int, int, int, int, bytes]) -> None: ...
    def partialmatch(self, node: bytes) -> bytes: ...
    def shortest(self, value: bytes) -> int: ...
    def stats(self) -> Dict[bytes, int]: ...

class nodetree:
    __doc__: str

    def insert(self, rev: int) -> None: ...
    def shortest(self, node: bytes) -> int: ...

# The IndexObject type here is defined in C, and there's no type for a buffer
# return, as of py3.11.  https://github.com/python/typing/issues/593
def parse_index2(data: object, inline: object, format: int = ...) -> Tuple[object, Optional[Tuple[int, object]]]: ...
