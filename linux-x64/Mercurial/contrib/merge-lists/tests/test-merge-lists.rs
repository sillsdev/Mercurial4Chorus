use similar::DiffableStr;
use std::ffi::OsStr;
use tempdir::TempDir;

fn run_test(arg: &str, input: &str) -> String {
    let mut cmd = assert_cmd::Command::cargo_bin("merge-lists").unwrap();
    let temp_dir = TempDir::new("test").unwrap();
    let base_path = temp_dir.path().join("base");
    let local_path = temp_dir.path().join("local");
    let other_path = temp_dir.path().join("other");

    let rest = input.strip_prefix("\nbase:\n").unwrap();
    let mut split = rest.split("\nlocal:\n");
    std::fs::write(&base_path, split.next().unwrap()).unwrap();
    let rest = split.next().unwrap();
    let mut split = rest.split("\nother:\n");
    std::fs::write(&local_path, split.next().unwrap()).unwrap();
    std::fs::write(&other_path, split.next().unwrap()).unwrap();
    cmd.args(&[
        OsStr::new(arg),
        local_path.as_os_str(),
        base_path.as_os_str(),
        other_path.as_os_str(),
    ])
    .assert()
    .success();

    let new_base_bytes = std::fs::read(&base_path).unwrap();
    let new_local_bytes = std::fs::read(&local_path).unwrap();
    let new_other_bytes = std::fs::read(&other_path).unwrap();
    // No newline before "base:" because of https://github.com/mitsuhiko/insta/issues/117
    format!(
        "base:\n{}\nlocal:\n{}\nother:\n{}",
        new_base_bytes.as_str().unwrap(),
        new_local_bytes.as_str().unwrap(),
        new_other_bytes.as_str().unwrap()
    )
}

#[test]
fn test_merge_lists_basic() {
    let output = run_test(
        "--python-imports",
        r"
base:
import lib1
import lib2

local:
import lib2
import lib3

other:
import lib3
import lib4
",
    );
    insta::assert_snapshot!(output, @r###"
    base:
    import lib3
    import lib4

    local:
    import lib3
    import lib4

    other:
    import lib3
    import lib4
    "###);
}

#[test]
fn test_merge_lists_from() {
    // Test some "from x import y" statements and some non-import conflicts
    // (unresolvable)
    let output = run_test(
        "--python-imports",
        r"
base:
from . import x

1+1

local:
from . import x
from a import b

2+2

other:
from a import c

3+3
",
    );
    insta::assert_snapshot!(output, @r###"
    base:
    from a import b
    from a import c

    1+1

    local:
    from a import b
    from a import c

    2+2

    other:
    from a import b
    from a import c

    3+3
    "###);
}

#[test]
fn test_merge_lists_not_sorted() {
    // Test that nothing is done if the elements in the conflicting hunks are
    // not sorted
    let output = run_test(
        "--python-imports",
        r"
base:
import x

1+1

local:
import a
import x

2+2

other:
import z
import y

3+3
",
    );
    insta::assert_snapshot!(output, @r###"
    base:
    import x

    1+1

    local:
    import a
    import x

    2+2

    other:
    import z
    import y

    3+3
    "###);
}

#[test]
fn test_custom_regex() {
    // Test merging of all lines (by matching anything)
    let output = run_test(
        "--pattern=.*",
        r"
base:
aardvark
baboon
camel

local:
aardvark
camel
eagle

other:
aardvark
camel
deer
",
    );
    insta::assert_snapshot!(output, @r###"
    base:
    aardvark
    camel
    deer
    eagle

    local:
    aardvark
    camel
    deer
    eagle

    other:
    aardvark
    camel
    deer
    eagle
    "###);
}
