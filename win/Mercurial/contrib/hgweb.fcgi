#!/usr/bin/env python3
#
# An example FastCGI script for use with flup, edit as necessary

# Path to repo or hgweb config to serve (see 'hg help hgweb')
config = b"/path/to/repo/or/config"

# Uncomment and adjust if Mercurial is not installed system-wide
# (consult "installed modules" path from 'hg debuginstall'):
# import sys; sys.path.insert(0, "/path/to/python/lib")

from mercurial import demandimport

demandimport.enable()
from mercurial.hgweb import hgweb
from flup.server.fcgi import WSGIServer

application = hgweb(config)
WSGIServer(application).run()
