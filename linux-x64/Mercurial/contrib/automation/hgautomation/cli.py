# cli.py - Command line interface for automation
#
# Copyright 2019 Gregory Szorc <gregory.szorc@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

# no-check-code because Python 3 native.

import argparse
import concurrent.futures as futures
import os
import pathlib
import time

from . import (
    aws,
    HGAutomation,
    linux,
    try_server,
    windows,
)


SOURCE_ROOT = pathlib.Path(
    os.path.abspath(__file__)
).parent.parent.parent.parent
DIST_PATH = SOURCE_ROOT / 'dist'


def bootstrap_linux_dev(
    hga: HGAutomation, aws_region, distros=None, parallel=False
):
    c = hga.aws_connection(aws_region)

    if distros:
        distros = distros.split(',')
    else:
        distros = sorted(linux.DISTROS)

    # TODO There is a wonky interaction involving KeyboardInterrupt whereby
    # the context manager that is supposed to terminate the temporary EC2
    # instance doesn't run. Until we fix this, make parallel building opt-in
    # so we don't orphan instances.
    if parallel:
        fs = []

        with futures.ThreadPoolExecutor(len(distros)) as e:
            for distro in distros:
                fs.append(e.submit(aws.ensure_linux_dev_ami, c, distro=distro))

            for f in fs:
                f.result()
    else:
        for distro in distros:
            aws.ensure_linux_dev_ami(c, distro=distro)


def bootstrap_windows_dev(hga: HGAutomation, aws_region, base_image_name):
    c = hga.aws_connection(aws_region)
    image = aws.ensure_windows_dev_ami(c, base_image_name=base_image_name)
    print('Windows development AMI available as %s' % image.id)


def build_inno(
    hga: HGAutomation,
    aws_region,
    arch,
    revision,
    version,
    base_image_name,
):
    c = hga.aws_connection(aws_region)
    image = aws.ensure_windows_dev_ami(c, base_image_name=base_image_name)
    DIST_PATH.mkdir(exist_ok=True)

    with aws.temporary_windows_dev_instances(c, image, 't3.medium') as insts:
        instance = insts[0]

        windows.synchronize_hg(SOURCE_ROOT, revision, instance)

        for a in arch:
            windows.build_inno_installer(
                instance.winrm_client,
                a,
                DIST_PATH,
                version=version,
            )


def build_wix(
    hga: HGAutomation,
    aws_region,
    arch,
    revision,
    version,
    base_image_name,
):
    c = hga.aws_connection(aws_region)
    image = aws.ensure_windows_dev_ami(c, base_image_name=base_image_name)
    DIST_PATH.mkdir(exist_ok=True)

    with aws.temporary_windows_dev_instances(c, image, 't3.medium') as insts:
        instance = insts[0]

        windows.synchronize_hg(SOURCE_ROOT, revision, instance)

        for a in arch:
            windows.build_wix_installer(
                instance.winrm_client,
                a,
                DIST_PATH,
                version=version,
            )


def build_windows_wheel(
    hga: HGAutomation,
    aws_region,
    python_version,
    arch,
    revision,
    base_image_name,
):
    c = hga.aws_connection(aws_region)
    image = aws.ensure_windows_dev_ami(c, base_image_name=base_image_name)
    DIST_PATH.mkdir(exist_ok=True)

    with aws.temporary_windows_dev_instances(c, image, 't3.medium') as insts:
        instance = insts[0]

        windows.synchronize_hg(SOURCE_ROOT, revision, instance)

        for py_version in python_version:
            for a in arch:
                windows.build_wheel(
                    instance.winrm_client, py_version, a, DIST_PATH
                )


def build_all_windows_packages(
    hga: HGAutomation, aws_region, revision, version, base_image_name
):
    c = hga.aws_connection(aws_region)
    image = aws.ensure_windows_dev_ami(c, base_image_name=base_image_name)
    DIST_PATH.mkdir(exist_ok=True)

    with aws.temporary_windows_dev_instances(c, image, 'm6i.large') as insts:
        instance = insts[0]

        winrm_client = instance.winrm_client

        windows.synchronize_hg(SOURCE_ROOT, revision, instance)

        for py_version in ("3.7", "3.8", "3.9", "3.10"):
            for arch in ("x86", "x64"):
                windows.purge_hg(winrm_client)
                windows.build_wheel(
                    winrm_client,
                    python_version=py_version,
                    arch=arch,
                    dest_path=DIST_PATH,
                )

        for arch in ('x86', 'x64'):
            windows.purge_hg(winrm_client)
            windows.build_inno_installer(
                winrm_client, arch, DIST_PATH, version=version
            )
            windows.build_wix_installer(
                winrm_client, arch, DIST_PATH, version=version
            )


def terminate_ec2_instances(hga: HGAutomation, aws_region):
    c = hga.aws_connection(aws_region, ensure_ec2_state=False)
    aws.terminate_ec2_instances(c.ec2resource)


def purge_ec2_resources(hga: HGAutomation, aws_region):
    c = hga.aws_connection(aws_region, ensure_ec2_state=False)
    aws.remove_resources(c)


def run_tests_linux(
    hga: HGAutomation,
    aws_region,
    instance_type,
    python_version,
    test_flags,
    distro,
    filesystem,
):
    c = hga.aws_connection(aws_region)
    image = aws.ensure_linux_dev_ami(c, distro=distro)

    t_start = time.time()

    ensure_extra_volume = filesystem not in ('default', 'tmpfs')

    with aws.temporary_linux_dev_instances(
        c, image, instance_type, ensure_extra_volume=ensure_extra_volume
    ) as insts:
        instance = insts[0]

        linux.prepare_exec_environment(
            instance.ssh_client, filesystem=filesystem
        )
        linux.synchronize_hg(SOURCE_ROOT, instance, '.')
        t_prepared = time.time()
        linux.run_tests(instance.ssh_client, python_version, test_flags)
        t_done = time.time()

    t_setup = t_prepared - t_start
    t_all = t_done - t_start

    print(
        'total time: %.1fs; setup: %.1fs; tests: %.1fs; setup overhead: %.1f%%'
        % (t_all, t_setup, t_done - t_prepared, t_setup / t_all * 100.0)
    )


def run_tests_windows(
    hga: HGAutomation,
    aws_region,
    instance_type,
    python_version,
    arch,
    test_flags,
    base_image_name,
):
    c = hga.aws_connection(aws_region)
    image = aws.ensure_windows_dev_ami(c, base_image_name=base_image_name)

    with aws.temporary_windows_dev_instances(
        c, image, instance_type, disable_antivirus=True
    ) as insts:
        instance = insts[0]

        windows.synchronize_hg(SOURCE_ROOT, '.', instance)
        windows.run_tests(
            instance.winrm_client, python_version, arch, test_flags
        )


def publish_windows_artifacts(
    hg: HGAutomation,
    aws_region,
    version: str,
    pypi: bool,
    mercurial_scm_org: bool,
    ssh_username: str,
):
    windows.publish_artifacts(
        DIST_PATH,
        version,
        pypi=pypi,
        mercurial_scm_org=mercurial_scm_org,
        ssh_username=ssh_username,
    )


def run_try(hga: HGAutomation, aws_region: str, rev: str):
    c = hga.aws_connection(aws_region, ensure_ec2_state=False)
    try_server.trigger_try(c, rev=rev)


def get_parser():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--state-path',
        default='~/.hgautomation',
        help='Path for local state files',
    )
    parser.add_argument(
        '--aws-region',
        help='AWS region to use',
        default='us-west-2',
    )

    subparsers = parser.add_subparsers()

    sp = subparsers.add_parser(
        'bootstrap-linux-dev',
        help='Bootstrap Linux development environments',
    )
    sp.add_argument(
        '--distros',
        help='Comma delimited list of distros to bootstrap',
    )
    sp.add_argument(
        '--parallel',
        action='store_true',
        help='Generate AMIs in parallel (not CTRL-c safe)',
    )
    sp.set_defaults(func=bootstrap_linux_dev)

    sp = subparsers.add_parser(
        'bootstrap-windows-dev',
        help='Bootstrap the Windows development environment',
    )
    sp.add_argument(
        '--base-image-name',
        help='AMI name of base image',
        default=aws.WINDOWS_BASE_IMAGE_NAME,
    )
    sp.set_defaults(func=bootstrap_windows_dev)

    sp = subparsers.add_parser(
        'build-all-windows-packages',
        help='Build all Windows packages',
    )
    sp.add_argument(
        '--revision',
        help='Mercurial revision to build',
        default='.',
    )
    sp.add_argument(
        '--version',
        help='Mercurial version string to use',
    )
    sp.add_argument(
        '--base-image-name',
        help='AMI name of base image',
        default=aws.WINDOWS_BASE_IMAGE_NAME,
    )
    sp.set_defaults(func=build_all_windows_packages)

    sp = subparsers.add_parser(
        'build-inno',
        help='Build Inno Setup installer(s)',
    )
    sp.add_argument(
        '--arch',
        help='Architecture to build for',
        choices={'x86', 'x64'},
        nargs='*',
        default=['x64'],
    )
    sp.add_argument(
        '--revision',
        help='Mercurial revision to build',
        default='.',
    )
    sp.add_argument(
        '--version',
        help='Mercurial version string to use in installer',
    )
    sp.add_argument(
        '--base-image-name',
        help='AMI name of base image',
        default=aws.WINDOWS_BASE_IMAGE_NAME,
    )
    sp.set_defaults(func=build_inno)

    sp = subparsers.add_parser(
        'build-windows-wheel',
        help='Build Windows wheel(s)',
    )
    sp.add_argument(
        '--python-version',
        help='Python version to build for',
        choices={'3.7', '3.8', '3.9', '3.10'},
        nargs='*',
        default=['3.8'],
    )
    sp.add_argument(
        '--arch',
        help='Architecture to build for',
        choices={'x86', 'x64'},
        nargs='*',
        default=['x64'],
    )
    sp.add_argument(
        '--revision',
        help='Mercurial revision to build',
        default='.',
    )
    sp.add_argument(
        '--base-image-name',
        help='AMI name of base image',
        default=aws.WINDOWS_BASE_IMAGE_NAME,
    )
    sp.set_defaults(func=build_windows_wheel)

    sp = subparsers.add_parser('build-wix', help='Build WiX installer(s)')
    sp.add_argument(
        '--arch',
        help='Architecture to build for',
        choices={'x86', 'x64'},
        nargs='*',
        default=['x64'],
    )
    sp.add_argument(
        '--revision',
        help='Mercurial revision to build',
        default='.',
    )
    sp.add_argument(
        '--version',
        help='Mercurial version string to use in installer',
    )
    sp.add_argument(
        '--base-image-name',
        help='AMI name of base image',
        default=aws.WINDOWS_BASE_IMAGE_NAME,
    )
    sp.set_defaults(func=build_wix)

    sp = subparsers.add_parser(
        'terminate-ec2-instances',
        help='Terminate all active EC2 instances managed by us',
    )
    sp.set_defaults(func=terminate_ec2_instances)

    sp = subparsers.add_parser(
        'purge-ec2-resources',
        help='Purge all EC2 resources managed by us',
    )
    sp.set_defaults(func=purge_ec2_resources)

    sp = subparsers.add_parser(
        'run-tests-linux',
        help='Run tests on Linux',
    )
    sp.add_argument(
        '--distro',
        help='Linux distribution to run tests on',
        choices=linux.DISTROS,
        default='debian10',
    )
    sp.add_argument(
        '--filesystem',
        help='Filesystem type to use',
        choices={'btrfs', 'default', 'ext3', 'ext4', 'jfs', 'tmpfs', 'xfs'},
        default='default',
    )
    sp.add_argument(
        '--instance-type',
        help='EC2 instance type to use',
        default='c5.9xlarge',
    )
    sp.add_argument(
        '--python-version',
        help='Python version to use',
        choices={
            'system3',
            '3.8',
            'pypy',
            'pypy3.5',
            'pypy3.6',
        },
        default='system3',
    )
    sp.add_argument(
        'test_flags',
        help='Extra command line flags to pass to run-tests.py',
        nargs='*',
    )
    sp.set_defaults(func=run_tests_linux)

    sp = subparsers.add_parser(
        'run-tests-windows',
        help='Run tests on Windows',
    )
    sp.add_argument(
        '--instance-type',
        help='EC2 instance type to use',
        default='m6i.large',
    )
    sp.add_argument(
        '--python-version',
        help='Python version to use',
        choices={'3.8', '3.9', '3.10'},
        default='3.9',
    )
    sp.add_argument(
        '--arch',
        help='Architecture to test',
        choices={'x86', 'x64'},
        default='x64',
    )
    sp.add_argument(
        '--test-flags',
        help='Extra command line flags to pass to run-tests.py',
    )
    sp.add_argument(
        '--base-image-name',
        help='AMI name of base image',
        default=aws.WINDOWS_BASE_IMAGE_NAME,
    )
    sp.set_defaults(func=run_tests_windows)

    sp = subparsers.add_parser(
        'publish-windows-artifacts',
        help='Publish built Windows artifacts (wheels, installers, etc)',
    )
    sp.add_argument(
        '--no-pypi',
        dest='pypi',
        action='store_false',
        default=True,
        help='Skip uploading to PyPI',
    )
    sp.add_argument(
        '--no-mercurial-scm-org',
        dest='mercurial_scm_org',
        action='store_false',
        default=True,
        help='Skip uploading to www.mercurial-scm.org',
    )
    sp.add_argument(
        '--ssh-username',
        help='SSH username for mercurial-scm.org',
    )
    sp.add_argument(
        'version',
        help='Mercurial version string to locate local packages',
    )
    sp.set_defaults(func=publish_windows_artifacts)

    sp = subparsers.add_parser(
        'try', help='Run CI automation against a custom changeset'
    )
    sp.add_argument('-r', '--rev', default='.', help='Revision to run CI on')
    sp.set_defaults(func=run_try)

    return parser


def main():
    parser = get_parser()
    args = parser.parse_args()

    local_state_path = pathlib.Path(os.path.expanduser(args.state_path))
    automation = HGAutomation(local_state_path)

    if not hasattr(args, 'func'):
        parser.print_help()
        return

    kwargs = dict(vars(args))
    del kwargs['func']
    del kwargs['state_path']

    args.func(automation, **kwargs)
