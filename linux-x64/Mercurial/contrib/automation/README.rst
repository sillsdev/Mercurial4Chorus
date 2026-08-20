====================
Mercurial Automation
====================

This directory contains code and utilities for building and testing Mercurial
on remote machines.

The ``automation.py`` Script
============================

``automation.py`` is an executable Python script (requires Python 3.5+)
that serves as a driver to common automation tasks.

When executed, the script will *bootstrap* a virtualenv in
``<source-root>/build/venv-automation`` then re-execute itself using
that virtualenv. So there is no need for the caller to have a virtualenv
explicitly activated. This virtualenv will be populated with various
dependencies (as defined by the ``requirements.txt`` file).

To see what you can do with this script, simply run it::

   $ ./automation.py

Local State
===========

By default, local state required to interact with remote servers is stored
in the ``~/.hgautomation`` directory.

We attempt to limit persistent state to this directory. Even when
performing tasks that may have side-effects, we try to limit those
side-effects so they don't impact the local system. e.g. when we SSH
into a remote machine, we create a temporary directory for the SSH
config so the user's known hosts file isn't updated.

Try Server
==========

There exists a *Try Server* which allows automation to run against
an arbitrary Mercurial changeset and displays results via the web.

.. note::

   The *Try Server* is still experimental infrastructure.

To use the *Try Server*::

   $ ./automation.py try

With a custom AWS profile::

   $ AWS_PROFILE=hg contrib/automation/automation.py try

By default, the ``.`` revision is submitted. **Any uncommitted changes
are not submitted.**

To switch which revision is used::

   $ ./automation.py try -r abcdef

Access to the *Try Server* requires access to a special AWS account.
This account is currently run by Gregory Szorc. Here is the procedure
for accessing the *Try Server*:

1. Email Gregory Szorc at gregory.szorc@gmail.com and request a
   username. This username will be stored in the public domain.
2. Wait for an email reply containing your temporary AWS credentials.
3. Log in at https://gregoryszorc-hg.signin.aws.amazon.com/console
   and set a new, secure password.
4. Go to https://console.aws.amazon.com/iam/home?region=us-west-2#/security_credentials
5. Under ``Access keys for CLI, SDK, & API access``, click the
   ``Create access key`` button.
6. See the ``AWS Integration`` section for instructions on
   configuring your local client to use the generated credentials.

AWS Integration
===============

Various automation tasks integrate with AWS to provide access to
resources such as EC2 instances for generic compute.

This obviously requires an AWS account and credentials to work.

We use the ``boto3`` library for interacting with AWS APIs. We do not employ
any special functionality for telling ``boto3`` where to find AWS credentials. See
https://boto3.amazonaws.com/v1/documentation/api/latest/guide/configuration.html
for how ``boto3`` works. Once you have configured your environment such
that ``boto3`` can find credentials, interaction with AWS should *just work*.

To configure ``boto3``, you can use the ``aws configure`` command to
write out configuration files. (The ``aws`` command is typically provided
by an ``awscli`` package available in your package manager, including
``pip``.) Alternatively, you can write out files in ``~/.aws/`` directly.
e.g.::

   # ~/.aws/config
   [default]
   region = us-west-2

   # ~/.aws/credentials
   [default]
   aws_access_key_id = XXXX
   aws_secret_access_key = YYYY

If you have multiple AWS accounts, you can name the profile something
different from ``default``. e.g. ``hg``. You can influence which profile
is used by ``boto3`` by setting the ``AWS_PROFILE`` environment variable.
e.g. ``AWS_PROFILE=hg``.

Resource Management
-------------------

Depending on the task being performed, various AWS services will be accessed.
This of course requires AWS credentials with permissions to access these
services.

The following AWS services can be accessed by automation tasks:

* EC2
* IAM
* Simple Systems Manager (SSM)

Various resources will also be created as part of performing various tasks.
This also requires various permissions.

The following AWS resources can be created by automation tasks:

* EC2 key pairs
* EC2 security groups
* EC2 instances
* IAM roles and instance profiles
* SSM command invocations

When possible, we prefix resource names with ``hg-`` so they can easily
be identified as belonging to Mercurial.

.. important::

   We currently assume that AWS accounts utilized by *us* are single
   tenancy. Attempts to have discrete users of ``automation.py`` (including
   sharing credentials across machines) using the same AWS account can result
   in them interfering with each other and things breaking.

Cost of Operation
-----------------

``automation.py`` tries to be frugal with regards to utilization of remote
resources. Persistent remote resources are minimized in order to keep costs
in check. For example, EC2 instances are often ephemeral and only live as long
as the operation being performed.

Under normal operation, recurring costs are limited to:

* Storage costs for AMI / EBS snapshots. This should be just a few pennies
  per month.

When running EC2 instances, you'll be billed accordingly. Default instance
types vary by operation. We try to be respectful of your money when choosing
defaults. e.g. for Windows instances which are billed per hour, we use e.g.
``t3.medium`` instances, which cost ~$0.07 per hour. For operations that
scale well to many CPUs like running Linux tests, we may use a more powerful
instance like ``c5.9xlarge``. However, since Linux instances are billed
per second and the cost of running an e.g. ``c5.9xlarge`` for half the time
of a ``c5.4xlarge`` is roughly the same, the choice is justified.

.. note::

   When running Windows EC2 instances, AWS bills at the full hourly cost, even
   if the instance doesn't run for a full hour (per-second billing doesn't
   apply to Windows AMIs).

Managing Remote Resources
-------------------------

Occassionally, there may be an error purging a temporary resource. Or you
may wish to forcefully purge remote state. Commands can be invoked to manually
purge remote resources.

To terminate all EC2 instances that we manage::

   $ automation.py terminate-ec2-instances

To purge all EC2 resources that we manage::

   $ automation.py purge-ec2-resources

Remote Machine Interfaces
=========================

The code that connects to a remote machine and executes things is
theoretically machine agnostic as long as the remote machine conforms to
an *interface*. In other words, to perform actions like running tests
remotely or triggering packaging, it shouldn't matter if the remote machine
is an EC2 instance, a virtual machine, etc. This section attempts to document
the interface that remote machines need to provide in order to be valid
*targets* for remote execution. These interfaces are often not ideal nor
the most flexible. Instead, they have often evolved as the requirements of
our automation code have evolved.

Linux
-----

Remote Linux machines expose an SSH server on port 22. The SSH server
must allow the ``hg`` user to authenticate using the SSH key generated by
the automation code. The ``hg`` user should be part of the ``hg`` group
and it should have ``sudo`` access without password prompting.

The SSH channel must support SFTP to facilitate transferring files from
client to server.

``/bin/bash`` must be executable and point to a bash shell executable.

The ``/hgdev`` directory must exist and all its content owned by ``hg::hg``.

The ``/hgdev/pyenv`` directory should contain an installation of
``pyenv``. Various Python distributions should be installed. The exact
versions shouldn't matter. ``pyenv global`` should have been run so
``/hgdev/pyenv/shims/`` is populated with redirector scripts that point
to the appropriate Python executable.

The ``/hgdev/venv-bootstrap`` directory must contain a virtualenv
with Mercurial installed. The ``/hgdev/venv-bootstrap/bin/hg`` executable
is referenced by various scripts and the client.

The ``/hgdev/src`` directory MUST contain a clone of the Mercurial
source code. The state of the working directory is not important.

In order to run tests, the ``/hgwork`` directory will be created.
This may require running various ``mkfs.*`` executables and ``mount``
to provision a new filesystem. This will require elevated privileges
via ``sudo``.

Various dependencies to run the Mercurial test harness are also required.
Documenting them is beyond the scope of this document. Various tests
also require other optional dependencies and missing dependencies will
be printed by the test runner when a test is skipped.

Releasing Windows Artifacts
===========================

The `automation.py` script can be used to automate the release of Windows
artifacts::

   $ ./automation.py build-all-windows-packages --revision 5.1.1
   $ ./automation.py publish-windows-artifacts 5.1.1

The first command will launch an EC2 instance to build all Windows packages
and copy them into the `dist` directory relative to the repository root. The
second command will then attempt to upload these files to PyPI (via `twine`)
and to `mercurial-scm.org` (via SSH).

Uploading to PyPI requires a PyPI account with write access to the `Mercurial`
package. You can skip PyPI uploading by passing `--no-pypi`.

Uploading to `mercurial-scm.org` requires an SSH account on that server
with `windows` group membership and for the SSH key for that account to be the
default SSH key (e.g. `~/.ssh/id_rsa`) or in a running SSH agent. You can
skip `mercurial-scm.org` uploading by passing `--no-mercurial-scm-org`.
