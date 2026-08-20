use clap::{ArgGroup, Parser};
use itertools::Itertools;
use regex::bytes::Regex;
use similar::ChangeTag;
use std::cmp::{max, min, Ordering};
use std::collections::HashSet;
use std::ffi::OsString;
use std::ops::Range;
use std::path::PathBuf;

fn find_unchanged_ranges(
    old_bytes: &[u8],
    new_bytes: &[u8],
) -> Vec<(Range<usize>, Range<usize>)> {
    let diff = similar::TextDiff::configure()
        .algorithm(similar::Algorithm::Patience)
        .diff_lines(old_bytes, new_bytes);
    let mut new_unchanged_ranges = vec![];
    let mut old_index = 0;
    let mut new_index = 0;
    for diff in diff.iter_all_changes() {
        match diff.tag() {
            ChangeTag::Equal => {
                new_unchanged_ranges.push((
                    old_index..old_index + diff.value().len(),
                    new_index..new_index + diff.value().len(),
                ));
                old_index += diff.value().len();
                new_index += diff.value().len();
            }
            ChangeTag::Delete => {
                old_index += diff.value().len();
            }
            ChangeTag::Insert => {
                new_index += diff.value().len();
            }
        }
    }
    new_unchanged_ranges
}

/// Returns a list of all the lines in the input (including trailing newlines),
/// but only if they all match the regex and they are sorted.
fn get_lines<'input>(
    input: &'input [u8],
    regex: &Regex,
) -> Option<Vec<&'input [u8]>> {
    let lines = input.split_inclusive(|x| *x == b'\n').collect_vec();
    let mut previous_line = "".as_bytes();
    for line in &lines {
        if *line < previous_line {
            return None;
        }
        if !regex.is_match(line) {
            return None;
        }
        previous_line = line;
    }
    Some(lines)
}

fn resolve_conflict(
    base_slice: &[u8],
    local_slice: &[u8],
    other_slice: &[u8],
    regex: &Regex,
) -> Option<Vec<u8>> {
    let base_lines = get_lines(base_slice, regex)?;
    let local_lines = get_lines(local_slice, regex)?;
    let other_lines = get_lines(other_slice, regex)?;
    let base_lines_set: HashSet<_> = base_lines.iter().copied().collect();
    let local_lines_set: HashSet<_> = local_lines.iter().copied().collect();
    let other_lines_set: HashSet<_> = other_lines.iter().copied().collect();
    let mut result = local_lines_set;
    for to_add in other_lines_set.difference(&base_lines_set) {
        result.insert(to_add);
    }
    for to_remove in base_lines_set.difference(&other_lines_set) {
        result.remove(to_remove);
    }
    Some(result.into_iter().sorted().collect_vec().concat())
}

fn resolve(
    base_bytes: &[u8],
    local_bytes: &[u8],
    other_bytes: &[u8],
    regex: &Regex,
) -> (Vec<u8>, Vec<u8>, Vec<u8>) {
    // Find unchanged ranges between the base and the two sides. We do that by
    // initially considering the whole base unchanged. Then we compare each
    // side with the base and intersect the unchanged ranges we find with
    // what we had before.
    let unchanged_ranges = vec![UnchangedRange {
        base_range: 0..base_bytes.len(),
        offsets: vec![],
    }];
    let unchanged_ranges = intersect_regions(
        unchanged_ranges,
        &find_unchanged_ranges(base_bytes, local_bytes),
    );
    let mut unchanged_ranges = intersect_regions(
        unchanged_ranges,
        &find_unchanged_ranges(base_bytes, other_bytes),
    );
    // Add an empty UnchangedRange at the end to make it easier to find change
    // ranges. That way there's a changed range before each UnchangedRange.
    unchanged_ranges.push(UnchangedRange {
        base_range: base_bytes.len()..base_bytes.len(),
        offsets: vec![
            local_bytes.len().wrapping_sub(base_bytes.len()) as isize,
            other_bytes.len().wrapping_sub(base_bytes.len()) as isize,
        ],
    });

    let mut new_base_bytes: Vec<u8> = vec![];
    let mut new_local_bytes: Vec<u8> = vec![];
    let mut new_other_bytes: Vec<u8> = vec![];
    let mut previous = UnchangedRange {
        base_range: 0..0,
        offsets: vec![0, 0],
    };
    for current in unchanged_ranges {
        let base_slice =
            &base_bytes[previous.base_range.end..current.base_range.start];
        let local_slice = &local_bytes[previous.end(0)..current.start(0)];
        let other_slice = &other_bytes[previous.end(1)..current.start(1)];
        if let Some(resolution) =
            resolve_conflict(base_slice, local_slice, other_slice, regex)
        {
            new_base_bytes.extend(&resolution);
            new_local_bytes.extend(&resolution);
            new_other_bytes.extend(&resolution);
        } else {
            new_base_bytes.extend(base_slice);
            new_local_bytes.extend(local_slice);
            new_other_bytes.extend(other_slice);
        }
        new_base_bytes.extend(&base_bytes[current.base_range.clone()]);
        new_local_bytes.extend(&local_bytes[current.start(0)..current.end(0)]);
        new_other_bytes.extend(&other_bytes[current.start(1)..current.end(1)]);
        previous = current;
    }

    (new_base_bytes, new_local_bytes, new_other_bytes)
}

/// A tool that performs a 3-way merge, resolving conflicts in sorted lists and
/// leaving other conflicts unchanged. This is useful with Mercurial's support
/// for partial merge tools (configured in `[partial-merge-tools]`).
#[derive(Parser, Debug)]
#[clap(version, about, long_about = None)]
#[clap(group(ArgGroup::new("match").required(true).args(&["pattern", "python-imports"])))]
struct Args {
    /// Path to the file's content in the "local" side
    local: OsString,

    /// Path to the file's content in the base
    base: OsString,

    /// Path to the file's content in the "other" side
    other: OsString,

    /// Regular expression to use
    #[clap(long, short)]
    pattern: Option<String>,

    /// Use built-in regular expression for Python imports
    #[clap(long)]
    python_imports: bool,
}

fn get_regex(args: &Args) -> Regex {
    let pattern = if args.python_imports {
        r"import \w+(\.\w+)*( +#.*)?\n|from (\w+(\.\w+)* import \w+( as \w+)?(, \w+( as \w+)?)*( +#.*)?)"
    } else if let Some(pattern) = &args.pattern {
        pattern
    } else {
        ".*"
    };
    let pattern = format!(r"{}\r?\n?", pattern);
    regex::bytes::Regex::new(&pattern).unwrap()
}

fn main() {
    let args: Args = Args::parse();

    let base_path = PathBuf::from(&args.base);
    let local_path = PathBuf::from(&args.local);
    let other_path = PathBuf::from(&args.other);

    let base_bytes = std::fs::read(&base_path).unwrap();
    let local_bytes = std::fs::read(&local_path).unwrap();
    let other_bytes = std::fs::read(&other_path).unwrap();

    let regex = get_regex(&args);
    let (new_base_bytes, new_local_bytes, new_other_bytes) =
        resolve(&base_bytes, &local_bytes, &other_bytes, &regex);

    // Write out the result if anything changed
    if new_base_bytes != base_bytes {
        std::fs::write(&base_path, new_base_bytes).unwrap();
    }
    if new_local_bytes != local_bytes {
        std::fs::write(&local_path, new_local_bytes).unwrap();
    }
    if new_other_bytes != other_bytes {
        std::fs::write(&other_path, new_other_bytes).unwrap();
    }
}

fn checked_add(base: usize, offset: isize) -> usize {
    if offset < 0 {
        base.checked_sub(offset.checked_abs().unwrap() as usize)
            .unwrap()
    } else {
        base.checked_add(offset as usize).unwrap()
    }
}

// The remainder of the file is copied from
// https://github.com/martinvonz/jj/blob/main/lib/src/diff.rs

#[derive(Clone, PartialEq, Eq, Debug)]
struct UnchangedRange {
    base_range: Range<usize>,
    offsets: Vec<isize>,
}

impl UnchangedRange {
    fn start(&self, side: usize) -> usize {
        checked_add(self.base_range.start, self.offsets[side])
    }

    fn end(&self, side: usize) -> usize {
        checked_add(self.base_range.end, self.offsets[side])
    }
}

impl PartialOrd for UnchangedRange {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for UnchangedRange {
    fn cmp(&self, other: &Self) -> Ordering {
        self.base_range
            .start
            .cmp(&other.base_range.start)
            .then_with(|| self.base_range.end.cmp(&other.base_range.end))
    }
}

/// Takes the current regions and intersects it with the new unchanged ranges
/// from a 2-way diff. The result is a map of unchanged regions with one more
/// offset in the map's values.
fn intersect_regions(
    current_ranges: Vec<UnchangedRange>,
    new_unchanged_ranges: &[(Range<usize>, Range<usize>)],
) -> Vec<UnchangedRange> {
    let mut result = vec![];
    let mut current_ranges_iter = current_ranges.into_iter().peekable();
    for (new_base_range, other_range) in new_unchanged_ranges.iter() {
        assert_eq!(new_base_range.len(), other_range.len());
        while let Some(UnchangedRange {
            base_range,
            offsets,
        }) = current_ranges_iter.peek()
        {
            // No need to look further if we're past the new range.
            if base_range.start >= new_base_range.end {
                break;
            }
            // Discard any current unchanged regions that don't match between
            // the base and the new input.
            if base_range.end <= new_base_range.start {
                current_ranges_iter.next();
                continue;
            }
            let new_start = max(base_range.start, new_base_range.start);
            let new_end = min(base_range.end, new_base_range.end);
            let mut new_offsets = offsets.clone();
            new_offsets
                .push(other_range.start.wrapping_sub(new_base_range.start)
                    as isize);
            result.push(UnchangedRange {
                base_range: new_start..new_end,
                offsets: new_offsets,
            });
            if base_range.end >= new_base_range.end {
                // Break without consuming the item; there may be other new
                // ranges that overlap with it.
                break;
            }
            current_ranges_iter.next();
        }
    }
    result
}
