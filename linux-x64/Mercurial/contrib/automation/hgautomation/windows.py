# windows.py - Automation specific to Windows
#
# Copyright 2019 Gregory Szorc <gregory.szorc@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

# no-check-code because Python 3 native.

import datetime
import os
import paramiko
import pathlib
import re
import subprocess
import tempfile

from .pypi import upload as pypi_upload
from .winrm import run_powershell


HG_PURGE = r'''
$Env:PATH = "C:\hgdev\venv-bootstrap\Scripts;$Env:PATH"
Set-Location C:\hgdev\src
hg.exe --config extensions.purge= purge --all
if ($LASTEXITCODE -ne 0) {
    throw "process exited non-0: $LASTEXITCODE"
}
Write-Output "purged Mercurial repo"
'''

HG_UPDATE_CLEAN = r'''
$Env:PATH = "C:\hgdev\venv-bootstrap\Scripts;$Env:PATH"
Set-Location C:\hgdev\src
hg.exe --config extensions.purge= purge --all
if ($LASTEXITCODE -ne 0) {{
    throw "process exited non-0: $LASTEXITCODE"
}}
hg.exe update -C {revision}
if ($LASTEXITCODE -ne 0) {{
    throw "process exited non-0: $LASTEXITCODE"
}}
hg.exe log -r .
Write-Output "updated Mercurial working directory to {revision}"
'''.lstrip()

BUILD_INNO_PYTHON3 = r'''
$Env:PATH = "C:\hgdev\venv-bootstrap\Scripts;$Env:PATH"
$Env:RUSTUP_HOME = "C:\hgdev\rustup"
$Env:CARGO_HOME = "C:\hgdev\cargo"
Set-Location C:\hgdev\src
C:\hgdev\python37-x64\python.exe contrib\packaging\packaging.py inno --pyoxidizer-target {pyoxidizer_target} --version {version}
if ($LASTEXITCODE -ne 0) {{
    throw "process exited non-0: $LASTEXITCODE"
}}
'''


BUILD_WHEEL = r'''
$Env:PATH = "C:\hgdev\venv-bootstrap\Scripts;$Env:PATH"
Set-Location C:\hgdev\src
C:\hgdev\python{python_version}-{arch}\python.exe -m pip wheel --wheel-dir dist .
if ($LASTEXITCODE -ne 0) {{
    throw "process exited non-0: $LASTEXITCODE"
}}
'''

BUILD_WIX_PYTHON3 = r'''
$Env:PATH = "C:\hgdev\venv-bootstrap\Scripts;$Env:PATH"
$Env:RUSTUP_HOME = "C:\hgdev\rustup"
$Env:CARGO_HOME = "C:\hgdev\cargo"
Set-Location C:\hgdev\src
C:\hgdev\python37-x64\python.exe contrib\packaging\packaging.py wix --pyoxidizer-target {pyoxidizer_target} --version {version}
if ($LASTEXITCODE -ne 0) {{
    throw "process exited non-0: $LASTEXITCODE"
}}
'''


RUN_TESTS = r'''
C:\hgdev\MinGW\msys\1.0\bin\sh.exe --login -c "cd /c/hgdev/src/tests && /c/hgdev/{python_path}/python.exe run-tests.py {test_flags}"
if ($LASTEXITCODE -ne 0) {{
    throw "process exited non-0: $LASTEXITCODE"
}}
'''


WHEEL_FILENAME_PYTHON37_X86 = 'mercurial-{version}-cp37-cp37m-win32.whl'
WHEEL_FILENAME_PYTHON37_X64 = 'mercurial-{version}-cp37-cp37m-win_amd64.whl'
WHEEL_FILENAME_PYTHON38_X86 = 'mercurial-{version}-cp38-cp38-win32.whl'
WHEEL_FILENAME_PYTHON38_X64 = 'mercurial-{version}-cp38-cp38-win_amd64.whl'
WHEEL_FILENAME_PYTHON39_X86 = 'mercurial-{version}-cp39-cp39-win32.whl'
WHEEL_FILENAME_PYTHON39_X64 = 'mercurial-{version}-cp39-cp39-win_amd64.whl'
WHEEL_FILENAME_PYTHON310_X86 = 'mercurial-{version}-cp310-cp310-win32.whl'
WHEEL_FILENAME_PYTHON310_X64 = 'mercurial-{version}-cp310-cp310-win_amd64.whl'

EXE_FILENAME_PYTHON3_X86 = 'Mercurial-{version}-x86.exe'
EXE_FILENAME_PYTHON3_X64 = 'Mercurial-{version}-x64.exe'

MSI_FILENAME_PYTHON3_X86 = 'mercurial-{version}-x86.msi'
MSI_FILENAME_PYTHON3_X64 = 'mercurial-{version}-x64.msi'

MERCURIAL_SCM_BASE_URL = 'https://mercurial-scm.org/release/windows'

X86_USER_AGENT_PATTERN = '.*Windows.*'
X64_USER_AGENT_PATTERN = '.*Windows.*(WOW|x)64.*'

# TODO remove Python version once Python 2 is dropped.
EXE_PYTHON3_X86_DESCRIPTION = (
    'Mercurial {version} Inno Setup installer - x86 Windows (Python 3) '
    '- does not require admin rights'
)
EXE_PYTHON3_X64_DESCRIPTION = (
    'Mercurial {version} Inno Setup installer - x64 Windows (Python 3) '
    '- does not require admin rights'
)
MSI_PYTHON3_X86_DESCRIPTION = (
    'Mercurial {version} MSI installer - x86 Windows (Python 3) '
    '- requires admin rights'
)
MSI_PYTHON3_X64_DESCRIPTION = (
    'Mercurial {version} MSI installer - x64 Windows (Python 3) '
    '- requires admin rights'
)


def fix_authorized_keys_permissions(winrm_client, path):
    commands = [
        '$ErrorActionPreference = "Stop"',
        'Repair-AuthorizedKeyPermission -FilePath %s -Confirm:$false' % path,
        r'icacls %s /remove:g "NT Service\sshd"' % path,
    ]

    run_powershell(winrm_client, '\n'.join(commands))


def synchronize_hg(hg_repo: pathlib.Path, revision: str, ec2_instance):
    """Synchronize local Mercurial repo to remote EC2 instance."""

    winrm_client = ec2_instance.winrm_client

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir = pathlib.Path(temp_dir)

        ssh_dir = temp_dir / '.ssh'
        ssh_dir.mkdir()
        ssh_dir.chmod(0o0700)

        # Generate SSH key to use for communication.
        subprocess.run(
            [
                'ssh-keygen',
                '-t',
                'rsa',
                '-b',
                '4096',
                '-N',
                '',
                '-f',
                str(ssh_dir / 'id_rsa'),
            ],
            check=True,
            capture_output=True,
        )

        # Add it to ~/.ssh/authorized_keys on remote.
        # This assumes the file doesn't already exist.
        authorized_keys = r'c:\Users\Administrator\.ssh\authorized_keys'
        winrm_client.execute_cmd(r'mkdir c:\Users\Administrator\.ssh')
        winrm_client.copy(str(ssh_dir / 'id_rsa.pub'), authorized_keys)
        fix_authorized_keys_permissions(winrm_client, authorized_keys)

        public_ip = ec2_instance.public_ip_address

        ssh_config = temp_dir / '.ssh' / 'config'

        with open(ssh_config, 'w', encoding='utf-8') as fh:
            fh.write('Host %s\n' % public_ip)
            fh.write('  User Administrator\n')
            fh.write('  StrictHostKeyChecking no\n')
            fh.write('  UserKnownHostsFile %s\n' % (ssh_dir / 'known_hosts'))
            fh.write('  IdentityFile %s\n' % (ssh_dir / 'id_rsa'))

        if not (hg_repo / '.hg').is_dir():
            raise Exception(
                '%s is not a Mercurial repository; '
                'synchronization not yet supported' % hg_repo
            )

        env = dict(os.environ)
        env['HGPLAIN'] = '1'
        env['HGENCODING'] = 'utf-8'

        hg_bin = hg_repo / 'hg'

        res = subprocess.run(
            ['python3', str(hg_bin), 'log', '-r', revision, '-T', '{node}'],
            cwd=str(hg_repo),
            env=env,
            check=True,
            capture_output=True,
        )

        full_revision = res.stdout.decode('ascii')

        args = [
            'python3',
            hg_bin,
            '--config',
            'ui.ssh=ssh -F %s' % ssh_config,
            '--config',
            'ui.remotecmd=c:/hgdev/venv-bootstrap/Scripts/hg.exe',
            # Also ensure .hgtags changes are present so auto version
            # calculation works.
            'push',
            '-f',
            '-r',
            full_revision,
            '-r',
            'file(.hgtags)',
            'ssh://%s/c:/hgdev/src' % public_ip,
        ]

        res = subprocess.run(args, cwd=str(hg_repo), env=env)

        # Allow 1 (no-op) to not trigger error.
        if res.returncode not in (0, 1):
            res.check_returncode()

        run_powershell(
            winrm_client, HG_UPDATE_CLEAN.format(revision=full_revision)
        )

        # TODO detect dirty local working directory and synchronize accordingly.


def purge_hg(winrm_client):
    """Purge the Mercurial source repository on an EC2 instance."""
    run_powershell(winrm_client, HG_PURGE)


def find_latest_dist(winrm_client, pattern):
    """Find path to newest file in dist/ directory matching a pattern."""

    res = winrm_client.execute_ps(
        r'$v = Get-ChildItem -Path C:\hgdev\src\dist -Filter "%s" '
        '| Sort-Object LastWriteTime -Descending '
        '| Select-Object -First 1\n'
        '$v.name' % pattern
    )
    return res[0]


def copy_latest_dist(winrm_client, pattern, dest_path):
    """Copy latest file matching pattern in dist/ directory.

    Given a WinRM client and a file pattern, find the latest file on the remote
    matching that pattern and copy it to the ``dest_path`` directory on the
    local machine.
    """
    latest = find_latest_dist(winrm_client, pattern)
    source = r'C:\hgdev\src\dist\%s' % latest
    dest = dest_path / latest
    print('copying %s to %s' % (source, dest))
    winrm_client.fetch(source, str(dest))


def build_inno_installer(
    winrm_client,
    arch: str,
    dest_path: pathlib.Path,
    version=None,
):
    """Build the Inno Setup installer on a remote machine.

    Using a WinRM client, remote commands are executed to build
    a Mercurial Inno Setup installer.
    """
    print('building Inno Setup installer for %s' % arch)

    # TODO fix this limitation in packaging code
    if not version:
        raise Exception("version string is required when building for Python 3")

    if arch == "x86":
        target_triple = "i686-pc-windows-msvc"
    elif arch == "x64":
        target_triple = "x86_64-pc-windows-msvc"
    else:
        raise Exception("unhandled arch: %s" % arch)

    ps = BUILD_INNO_PYTHON3.format(
        pyoxidizer_target=target_triple,
        version=version,
    )

    run_powershell(winrm_client, ps)
    copy_latest_dist(winrm_client, '*.exe', dest_path)


def build_wheel(
    winrm_client, python_version: str, arch: str, dest_path: pathlib.Path
):
    """Build Python wheels on a remote machine.

    Using a WinRM client, remote commands are executed to build a Python wheel
    for Mercurial.
    """
    print('Building Windows wheel for Python %s %s' % (python_version, arch))

    ps = BUILD_WHEEL.format(
        python_version=python_version.replace(".", ""), arch=arch
    )

    run_powershell(winrm_client, ps)
    copy_latest_dist(winrm_client, '*.whl', dest_path)


def build_wix_installer(
    winrm_client,
    arch: str,
    dest_path: pathlib.Path,
    version=None,
):
    """Build the WiX installer on a remote machine.

    Using a WinRM client, remote commands are executed to build a WiX installer.
    """
    print('Building WiX installer for %s' % arch)

    # TODO fix this limitation in packaging code
    if not version:
        raise Exception("version string is required when building for Python 3")

    if arch == "x86":
        target_triple = "i686-pc-windows-msvc"
    elif arch == "x64":
        target_triple = "x86_64-pc-windows-msvc"
    else:
        raise Exception("unhandled arch: %s" % arch)

    ps = BUILD_WIX_PYTHON3.format(
        pyoxidizer_target=target_triple,
        version=version,
    )

    run_powershell(winrm_client, ps)
    copy_latest_dist(winrm_client, '*.msi', dest_path)


def run_tests(winrm_client, python_version, arch, test_flags=''):
    """Run tests on a remote Windows machine.

    ``python_version`` is a ``X.Y`` string like ``2.7`` or ``3.7``.
    ``arch`` is ``x86`` or ``x64``.
    ``test_flags`` is a str representing extra arguments to pass to
    ``run-tests.py``.
    """
    if not re.match(r'\d\.\d', python_version):
        raise ValueError(
            r'python_version must be \d.\d; got %s' % python_version
        )

    if arch not in ('x86', 'x64'):
        raise ValueError('arch must be x86 or x64; got %s' % arch)

    python_path = 'python%s-%s' % (python_version.replace('.', ''), arch)

    ps = RUN_TESTS.format(
        python_path=python_path,
        test_flags=test_flags or '',
    )

    run_powershell(winrm_client, ps)


def resolve_wheel_artifacts(dist_path: pathlib.Path, version: str):
    return (
        dist_path / WHEEL_FILENAME_PYTHON37_X86.format(version=version),
        dist_path / WHEEL_FILENAME_PYTHON37_X64.format(version=version),
        dist_path / WHEEL_FILENAME_PYTHON38_X86.format(version=version),
        dist_path / WHEEL_FILENAME_PYTHON38_X64.format(version=version),
        dist_path / WHEEL_FILENAME_PYTHON39_X86.format(version=version),
        dist_path / WHEEL_FILENAME_PYTHON39_X64.format(version=version),
        dist_path / WHEEL_FILENAME_PYTHON310_X86.format(version=version),
        dist_path / WHEEL_FILENAME_PYTHON310_X64.format(version=version),
    )


def resolve_all_artifacts(dist_path: pathlib.Path, version: str):
    return (
        dist_path / WHEEL_FILENAME_PYTHON37_X86.format(version=version),
        dist_path / WHEEL_FILENAME_PYTHON37_X64.format(version=version),
        dist_path / WHEEL_FILENAME_PYTHON38_X86.format(version=version),
        dist_path / WHEEL_FILENAME_PYTHON38_X64.format(version=version),
        dist_path / WHEEL_FILENAME_PYTHON39_X86.format(version=version),
        dist_path / WHEEL_FILENAME_PYTHON39_X64.format(version=version),
        dist_path / WHEEL_FILENAME_PYTHON310_X86.format(version=version),
        dist_path / WHEEL_FILENAME_PYTHON310_X64.format(version=version),
        dist_path / EXE_FILENAME_PYTHON3_X86.format(version=version),
        dist_path / EXE_FILENAME_PYTHON3_X64.format(version=version),
        dist_path / MSI_FILENAME_PYTHON3_X86.format(version=version),
        dist_path / MSI_FILENAME_PYTHON3_X64.format(version=version),
    )


def generate_latest_dat(version: str):
    python3_x86_exe_filename = EXE_FILENAME_PYTHON3_X86.format(version=version)
    python3_x64_exe_filename = EXE_FILENAME_PYTHON3_X64.format(version=version)
    python3_x86_msi_filename = MSI_FILENAME_PYTHON3_X86.format(version=version)
    python3_x64_msi_filename = MSI_FILENAME_PYTHON3_X64.format(version=version)

    entries = (
        (
            '10',
            version,
            X86_USER_AGENT_PATTERN,
            '%s/%s' % (MERCURIAL_SCM_BASE_URL, python3_x86_exe_filename),
            EXE_PYTHON3_X86_DESCRIPTION.format(version=version),
        ),
        (
            '10',
            version,
            X64_USER_AGENT_PATTERN,
            '%s/%s' % (MERCURIAL_SCM_BASE_URL, python3_x64_exe_filename),
            EXE_PYTHON3_X64_DESCRIPTION.format(version=version),
        ),
        (
            '10',
            version,
            X86_USER_AGENT_PATTERN,
            '%s/%s' % (MERCURIAL_SCM_BASE_URL, python3_x86_msi_filename),
            MSI_PYTHON3_X86_DESCRIPTION.format(version=version),
        ),
        (
            '10',
            version,
            X64_USER_AGENT_PATTERN,
            '%s/%s' % (MERCURIAL_SCM_BASE_URL, python3_x64_msi_filename),
            MSI_PYTHON3_X64_DESCRIPTION.format(version=version),
        ),
    )

    lines = ['\t'.join(e) for e in entries]

    return '\n'.join(lines) + '\n'


def publish_artifacts_pypi(dist_path: pathlib.Path, version: str):
    """Publish Windows release artifacts to PyPI."""

    wheel_paths = resolve_wheel_artifacts(dist_path, version)

    for p in wheel_paths:
        if not p.exists():
            raise Exception('%s not found' % p)

    print('uploading wheels to PyPI (you may be prompted for credentials)')
    pypi_upload(wheel_paths)


def publish_artifacts_mercurial_scm_org(
    dist_path: pathlib.Path, version: str, ssh_username=None
):
    """Publish Windows release artifacts to mercurial-scm.org."""
    all_paths = resolve_all_artifacts(dist_path, version)

    for p in all_paths:
        if not p.exists():
            raise Exception('%s not found' % p)

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    # We assume the system SSH configuration knows how to connect.
    print('connecting to mercurial-scm.org via ssh...')
    try:
        client.connect('mercurial-scm.org', username=ssh_username)
    except paramiko.AuthenticationException:
        print('error authenticating; is an SSH key available in an SSH agent?')
        raise

    print('SSH connection established')

    print('opening SFTP client...')
    sftp = client.open_sftp()
    print('SFTP client obtained')

    for p in all_paths:
        dest_path = '/var/www/release/windows/%s' % p.name
        print('uploading %s to %s' % (p, dest_path))

        with p.open('rb') as fh:
            data = fh.read()

        with sftp.open(dest_path, 'wb') as fh:
            fh.write(data)
            fh.chmod(0o0664)

    latest_dat_path = '/var/www/release/windows/latest.dat'

    now = datetime.datetime.utcnow()
    backup_path = dist_path / (
        'latest-windows-%s.dat' % now.strftime('%Y%m%dT%H%M%S')
    )
    print('backing up %s to %s' % (latest_dat_path, backup_path))

    with sftp.open(latest_dat_path, 'rb') as fh:
        latest_dat_old = fh.read()

    with backup_path.open('wb') as fh:
        fh.write(latest_dat_old)

    print('writing %s with content:' % latest_dat_path)
    latest_dat_content = generate_latest_dat(version)
    print(latest_dat_content)

    with sftp.open(latest_dat_path, 'wb') as fh:
        fh.write(latest_dat_content.encode('ascii'))


def publish_artifacts(
    dist_path: pathlib.Path,
    version: str,
    pypi=True,
    mercurial_scm_org=True,
    ssh_username=None,
):
    """Publish Windows release artifacts.

    Files are found in `dist_path`. We will look for files with version string
    `version`.

    `pypi` controls whether we upload to PyPI.
    `mercurial_scm_org` controls whether we upload to mercurial-scm.org.
    """
    if pypi:
        publish_artifacts_pypi(dist_path, version)

    if mercurial_scm_org:
        publish_artifacts_mercurial_scm_org(
            dist_path, version, ssh_username=ssh_username
        )
