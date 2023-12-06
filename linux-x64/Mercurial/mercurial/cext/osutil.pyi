from typing import (
    AnyStr,
    IO,
    List,
    Optional,
    Sequence,
)

version: int

class stat:
    st_dev: int
    st_mode: int
    st_nlink: int
    st_size: int
    st_mtime: int
    st_ctime: int

def listdir(path: bytes, st: bool, skip: Optional[bool]) -> List[stat]: ...
def posixfile(name: AnyStr, mode: bytes, buffering: int) -> IO: ...
def statfiles(names: Sequence[bytes]) -> List[stat]: ...
def setprocname(name: bytes) -> None: ...
def getfstype(path: bytes) -> bytes: ...
def getfsmountpoint(path: bytes) -> bytes: ...
def unblocksignal(sig: int) -> None: ...
def isgui() -> bool: ...
