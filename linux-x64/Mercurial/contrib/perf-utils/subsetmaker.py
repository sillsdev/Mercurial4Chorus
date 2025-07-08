"""revset to select sample of repository

Hopefully this is useful to create interesting discovery cases.
"""

import collections
import random

from mercurial.i18n import _

from mercurial import (
    registrar,
    revset,
    revsetlang,
    smartset,
)

import sortedcontainers

SortedSet = sortedcontainers.SortedSet

revsetpredicate = registrar.revsetpredicate()


@revsetpredicate(b'subsetspec("<spec>")')
def subsetmarkerspec(repo, subset, x):
    """use a shorthand spec as used by search-discovery-case

    Supported format are:

    - "scratch-count-seed": not scratch(all(), count, "seed")
    - "randomantichain-seed": ::randomantichain(all(), "seed")
    - "rev-REV": "::REV"
    """
    args = revsetlang.getargs(
        x, 0, 1, _(b'subsetspec("spec") required an argument')
    )

    spec = revsetlang.getstring(args[0], _(b"spec should be a string"))
    case = spec.split(b'-')
    t = case[0]
    if t == b'scratch':
        spec_revset = b'not scratch(all(), %s, "%s")' % (case[1], case[2])
    elif t == b'randomantichain':
        spec_revset = b'::randomantichain(all(), "%s")' % case[1]
    elif t == b'rev':
        spec_revset = b'::%d' % case[1]
    else:
        assert False, spec

    selected = repo.revs(spec_revset)

    return selected & subset


@revsetpredicate(b'scratch(REVS, <count>, [seed])')
def scratch(repo, subset, x):
    """randomly remove <count> revision from the repository top

    This subset is created by recursively picking changeset starting from the
    heads. It can be summarized using the following algorithm::

        selected = set()
        for i in range(<count>):
            unselected = repo.revs("not <selected>")
            candidates = repo.revs("heads(<unselected>)")
            pick = random.choice(candidates)
            selected.add(pick)
    """
    m = _(b"scratch expects revisions, count argument and an optional seed")
    args = revsetlang.getargs(x, 2, 3, m)
    if len(args) == 2:
        x, n = args
        rand = random
    elif len(args) == 3:
        x, n, seed = args
        seed = revsetlang.getinteger(seed, _(b"seed should be a number"))
        rand = random.Random(seed)
    else:
        assert False

    n = revsetlang.getinteger(n, _(b"scratch expects a number"))

    selected = set()
    heads = SortedSet()
    children_count = collections.defaultdict(int)
    parents = repo.changelog._uncheckedparentrevs

    baseset = revset.getset(repo, smartset.fullreposet(repo), x)
    baseset.sort()
    for r in baseset:
        heads.add(r)

        p1, p2 = parents(r)
        if p1 >= 0:
            heads.discard(p1)
            children_count[p1] += 1
        if p2 >= 0:
            heads.discard(p2)
            children_count[p2] += 1

    for h in heads:
        assert children_count[h] == 0

    selected = set()
    for x in range(n):
        if not heads:
            break
        pick = rand.choice(heads)
        heads.remove(pick)
        assert pick not in selected
        selected.add(pick)
        p1, p2 = parents(pick)
        if p1 in children_count:
            assert p1 in children_count
            children_count[p1] -= 1
            assert children_count[p1] >= 0
            if children_count[p1] == 0:
                assert p1 not in selected, (r, p1)
                heads.add(p1)
        if p2 in children_count:
            assert p2 in children_count
            children_count[p2] -= 1
            assert children_count[p2] >= 0
            if children_count[p2] == 0:
                assert p2 not in selected, (r, p2)
                heads.add(p2)

    return smartset.baseset(selected) & subset


@revsetpredicate(b'randomantichain(REVS, [seed])')
def antichain(repo, subset, x):
    """Pick a random anti-chain in the repository

    A antichain is a set of changeset where there isn't any element that is
    either a descendant or ancestors of any other element in the set. In other
    word, all the elements are independant. It can be summarized with the
    following algorithm::

    selected = set()
    unselected = repo.revs('all()')
    while unselected:
        pick = random.choice(unselected)
        selected.add(pick)
        unselected -= repo.revs('::<pick> + <pick>::')
    """

    args = revsetlang.getargs(
        x, 1, 2, _(b"randomantichain expects revisions and an optional seed")
    )
    if len(args) == 1:
        (x,) = args
        rand = random
    elif len(args) == 2:
        x, seed = args
        seed = revsetlang.getinteger(seed, _(b"seed should be a number"))
        rand = random.Random(seed)
    else:
        assert False

    cl = repo.changelog

    # We already have cheap access to the parent mapping.
    # However, we need to build a mapping of the children mapping
    parents = repo.changelog._uncheckedparentrevs
    children_map = collections.defaultdict(list)
    for r in cl:
        p1, p2 = parents(r)
        if p1 >= 0:
            children_map[p1].append(r)
        if p2 >= 0:
            children_map[p2].append(r)
    children = children_map.__getitem__

    selected = set()
    undecided = SortedSet(cl)

    while undecided:
        # while there is "undecided content", we pick a random changeset X
        # and we remove anything in `::X + X::` from undecided content
        pick = rand.choice(undecided)
        selected.add(pick)
        undecided.remove(pick)

        ancestors = {p for p in parents(pick) if p in undecided}
        descendants = {c for c in children(pick) if c in undecided}

        while ancestors:
            current = ancestors.pop()
            undecided.remove(current)
            for p in parents(current):
                if p in undecided:
                    ancestors.add(p)
        while descendants:
            current = descendants.pop()
            undecided.remove(current)
            for p in children(current):
                if p in undecided:
                    ancestors.add(p)

    return smartset.baseset(selected) & subset
