#!/usr/bin/env python3
#
#  Copyright 2018 Paul Morelle <Paul.Morelle@octobus.net>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.
#
# This script use the output of `hg perfrevlogwrite -T json --details` to draw
# various plot related to write performance in a revlog
#
# usage: perf-revlog-write-plot.py details.json
import json
import re

import numpy as np
import scipy.signal

from matplotlib import (
    pyplot as plt,
    ticker as mticker,
)


def plot(data, title=None):
    items = {}
    re_title = re.compile(r'^revisions #\d+ of \d+, rev (\d+)$')
    for item in data:
        m = re_title.match(item['title'])
        if m is None:
            continue

        rev = int(m.group(1))
        items[rev] = item

    min_rev = min(items.keys())
    max_rev = max(items.keys())
    ary = np.empty((2, max_rev - min_rev + 1))
    for rev, item in items.items():
        ary[0][rev - min_rev] = rev
        ary[1][rev - min_rev] = item['wall']

    fig = plt.figure()
    comb_plt = fig.add_subplot(211)
    other_plt = fig.add_subplot(212)

    comb_plt.plot(
        ary[0], np.cumsum(ary[1]), color='red', linewidth=1, label='comb'
    )

    plots = []
    p = other_plt.plot(ary[0], ary[1], color='red', linewidth=1, label='wall')
    plots.append(p)

    colors = {
        10: ('green', 'xkcd:grass green'),
        100: ('blue', 'xkcd:bright blue'),
        1000: ('purple', 'xkcd:dark pink'),
    }
    for n, color in colors.items():
        avg_n = np.convolve(ary[1], np.full(n, 1.0 / n), 'valid')
        p = other_plt.plot(
            ary[0][n - 1 :],
            avg_n,
            color=color[0],
            linewidth=1,
            label='avg time last %d' % n,
        )
        plots.append(p)

        med_n = scipy.signal.medfilt(ary[1], n + 1)
        p = other_plt.plot(
            ary[0],
            med_n,
            color=color[1],
            linewidth=1,
            label='median time last %d' % n,
        )
        plots.append(p)

    formatter = mticker.ScalarFormatter()
    formatter.set_scientific(False)
    formatter.set_useOffset(False)

    comb_plt.grid()
    comb_plt.xaxis.set_major_formatter(formatter)
    comb_plt.legend()

    other_plt.grid()
    other_plt.xaxis.set_major_formatter(formatter)
    leg = other_plt.legend()
    leg2plot = {}
    for legline, plot in zip(leg.get_lines(), plots):
        legline.set_picker(5)
        leg2plot[legline] = plot

    def onpick(event):
        legline = event.artist
        plot = leg2plot[legline]
        visible = not plot[0].get_visible()
        for l in plot:
            l.set_visible(visible)

        if visible:
            legline.set_alpha(1.0)
        else:
            legline.set_alpha(0.2)
        fig.canvas.draw()

    if title is not None:
        fig.canvas.set_window_title(title)
    fig.canvas.mpl_connect('pick_event', onpick)

    plt.show()


if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1:
        print('reading from %r' % sys.argv[1])
        with open(sys.argv[1]) as fp:
            plot(json.load(fp), title=sys.argv[1])
    else:
        print('reading from stdin')
        plot(json.load(sys.stdin))
