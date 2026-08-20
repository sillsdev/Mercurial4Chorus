# linux.py - Linux specific automation functionality
#
# Copyright 2019 Gregory Szorc <gregory.szorc@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

# no-check-code because Python 3 native.

import os
import pathlib
import shlex
import subprocess
import tempfile

from .ssh import exec_command


# Linux distributions that are supported.
DISTROS = {
    'debian9',
    'debian10',
    'ubuntu18.04',
    'ubuntu19.04',
}

INSTALL_PYTHONS = r'''
PYENV3_VERSIONS="3.8.10 3.9.5 pypy3.5-7.0.0 pypy3.6-7.3.3 pypy3.7-7.3.3"

git clone https://github.com/pyenv/pyenv.git /hgdev/pyenv
pushd /hgdev/pyenv
git checkout 328fd42c3a2fbf14ae46dae2021a087fe27ba7e2
popd

export PYENV_ROOT="/hgdev/pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"

# pip 19.2.3.
PIP_SHA256=57e3643ff19f018f8a00dfaa6b7e4620e3c1a7a2171fd218425366ec006b3bfe
wget -O get-pip.py --progress dot:mega https://github.com/pypa/get-pip/raw/309a56c5fd94bd1134053a541cb4657a4e47e09d/get-pip.py
echo "${PIP_SHA256} get-pip.py" | sha256sum --check -

VIRTUALENV_SHA256=f78d81b62d3147396ac33fc9d77579ddc42cc2a98dd9ea38886f616b33bc7fb2
VIRTUALENV_TARBALL=virtualenv-16.7.5.tar.gz
wget -O ${VIRTUALENV_TARBALL} --progress dot:mega https://files.pythonhosted.org/packages/66/f0/6867af06d2e2f511e4e1d7094ff663acdebc4f15d4a0cb0fed1007395124/${VIRTUALENV_TARBALL}
echo "${VIRTUALENV_SHA256} ${VIRTUALENV_TARBALL}" | sha256sum --check -

for v in ${PYENV3_VERSIONS}; do
    pyenv install -v ${v}
    ${PYENV_ROOT}/versions/${v}/bin/python get-pip.py

    case ${v} in
        3.5.*)
            REQUIREMENTS=requirements-py3.5.txt
            ;;
        pypy3.5*)
            REQUIREMENTS=requirements-py3.5.txt
            ;;
        *)
            REQUIREMENTS=requirements-py3.txt
            ;;
    esac

    ${PYENV_ROOT}/versions/${v}/bin/pip install -r /hgdev/${REQUIREMENTS}
done

pyenv global ${PYENV3_VERSIONS} system
'''.lstrip().replace(
    '\r\n', '\n'
)

INSTALL_PYOXIDIZER = r'''
PYOXIDIZER_VERSION=0.16.0
PYOXIDIZER_SHA256=8875471c270312fbb934007fd30f65f1904cc0f5da6188d61c90ed2129b9f9c1
PYOXIDIZER_URL=https://github.com/indygreg/PyOxidizer/releases/download/pyoxidizer%2F${PYOXIDIZER_VERSION}/pyoxidizer-${PYOXIDIZER_VERSION}-linux_x86_64.zip

wget -O pyoxidizer.zip --progress dot:mega ${PYOXIDIZER_URL}
echo "${PYOXIDIZER_SHA256} pyoxidizer.zip" | sha256sum --check -

unzip pyoxidizer.zip
chmod +x pyoxidizer
sudo mv pyoxidizer /usr/local/bin/pyoxidizer
'''

INSTALL_RUST = r'''
RUSTUP_INIT_SHA256=a46fe67199b7bcbbde2dcbc23ae08db6f29883e260e23899a88b9073effc9076
wget -O rustup-init --progress dot:mega https://static.rust-lang.org/rustup/archive/1.18.3/x86_64-unknown-linux-gnu/rustup-init
echo "${RUSTUP_INIT_SHA256} rustup-init" | sha256sum --check -

chmod +x rustup-init
sudo -H -u hg -g hg ./rustup-init -y
sudo -H -u hg -g hg /home/hg/.cargo/bin/rustup install 1.41.1 1.52.0
sudo -H -u hg -g hg /home/hg/.cargo/bin/rustup component add clippy
'''


BOOTSTRAP_VIRTUALENV = r'''
/usr/bin/virtualenv /hgdev/venv-bootstrap

HG_SHA256=35fc8ba5e0379c1b3affa2757e83fb0509e8ac314cbd9f1fd133cf265d16e49f
HG_TARBALL=mercurial-5.1.1.tar.gz

wget -O ${HG_TARBALL} --progress dot:mega https://www.mercurial-scm.org/release/${HG_TARBALL}
echo "${HG_SHA256} ${HG_TARBALL}" | sha256sum --check -

/hgdev/venv-bootstrap/bin/pip install ${HG_TARBALL}
'''.lstrip().replace(
    '\r\n', '\n'
)


BOOTSTRAP_DEBIAN = (
    r'''
#!/bin/bash

set -ex

DISTRO=`grep DISTRIB_ID /etc/lsb-release  | awk -F= '{{print $2}}'`
DEBIAN_VERSION=`cat /etc/debian_version`
LSB_RELEASE=`lsb_release -cs`

sudo /usr/sbin/groupadd hg
sudo /usr/sbin/groupadd docker
sudo /usr/sbin/useradd -g hg -G sudo,docker -d /home/hg -m -s /bin/bash hg
sudo mkdir /home/hg/.ssh
sudo cp ~/.ssh/authorized_keys /home/hg/.ssh/authorized_keys
sudo chown -R hg:hg /home/hg/.ssh
sudo chmod 700 /home/hg/.ssh
sudo chmod 600 /home/hg/.ssh/authorized_keys

cat << EOF | sudo tee /etc/sudoers.d/90-hg
hg ALL=(ALL) NOPASSWD:ALL
EOF

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get -yq dist-upgrade

# Install packages necessary to set up Docker Apt repo.
sudo DEBIAN_FRONTEND=noninteractive apt-get -yq install --no-install-recommends \
    apt-transport-https \
    gnupg

cat > docker-apt-key << EOF
-----BEGIN PGP PUBLIC KEY BLOCK-----

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
YT90qFF93M3v01BbxP+EIY2/9tiIPbrd
=0YYh
-----END PGP PUBLIC KEY BLOCK-----
EOF

sudo apt-key add docker-apt-key

if [ "$LSB_RELEASE" = "stretch" ]; then
cat << EOF | sudo tee -a /etc/apt/sources.list
# Need backports for clang-format-6.0
deb http://deb.debian.org/debian stretch-backports main
EOF
fi

if [ "$LSB_RELEASE" = "stretch" -o "$LSB_RELEASE" = "buster" ]; then
cat << EOF | sudo tee -a /etc/apt/sources.list
# Sources are useful if we want to compile things locally.
deb-src http://deb.debian.org/debian $LSB_RELEASE main
deb-src http://security.debian.org/debian-security $LSB_RELEASE/updates main
deb-src http://deb.debian.org/debian $LSB_RELEASE-updates main
deb-src http://deb.debian.org/debian $LSB_RELEASE-backports main

deb [arch=amd64] https://download.docker.com/linux/debian $LSB_RELEASE stable
EOF

elif [ "$DISTRO" = "Ubuntu" ]; then
cat << EOF | sudo tee -a /etc/apt/sources.list
deb [arch=amd64] https://download.docker.com/linux/ubuntu $LSB_RELEASE stable
EOF

fi

sudo apt-get update

PACKAGES="\
    awscli \
    btrfs-progs \
    build-essential \
    bzr \
    clang-format-6.0 \
    cvs \
    darcs \
    debhelper \
    devscripts \
    docker-ce \
    dpkg-dev \
    dstat \
    emacs \
    gettext \
    git \
    htop \
    iotop \
    jfsutils \
    libbz2-dev \
    libexpat1-dev \
    libffi-dev \
    libgdbm-dev \
    liblzma-dev \
    libncurses5-dev \
    libnss3-dev \
    libreadline-dev \
    libsqlite3-dev \
    libssl-dev \
    netbase \
    ntfs-3g \
    nvme-cli \
    pyflakes3 \
    pylint3 \
    python3-boto3 \
    python3-dev \
    python3-docutils \
    python3-fuzzywuzzy \
    python3-pygments \
    python3-vcr \
    python3-venv \
    rsync \
    sqlite3 \
    subversion \
    tcl-dev \
    tk-dev \
    tla \
    unzip \
    uuid-dev \
    vim \
    virtualenv \
    wget \
    xfsprogs \
    zip \
    zlib1g-dev"

if [ "LSB_RELEASE" = "stretch" ]; then
    PACKAGES="$PACKAGES linux-perf"
elif [ "$DISTRO" = "Ubuntu" ]; then
    PACKAGES="$PACKAGES linux-tools-common"
fi

# Monotone only available in older releases.
if [ "$LSB_RELEASE" = "stretch" -o "$LSB_RELEASE" = "xenial" ]; then
    PACKAGES="$PACKAGES monotone"
fi

sudo DEBIAN_FRONTEND=noninteractive apt-get -yq install --no-install-recommends $PACKAGES

# Create clang-format symlink so test harness finds it.
sudo update-alternatives --install /usr/bin/clang-format clang-format \
    /usr/bin/clang-format-6.0 1000

sudo mkdir /hgdev
# Will be normalized to hg:hg later.
sudo chown `whoami` /hgdev

{install_rust}
{install_pyoxidizer}

cp requirements-*.txt /hgdev/

# Disable the pip version check because it uses the network and can
# be annoying.
cat << EOF | sudo tee -a /etc/pip.conf
[global]
disable-pip-version-check = True
EOF

{install_pythons}
{bootstrap_virtualenv}

/hgdev/venv-bootstrap/bin/hg clone https://www.mercurial-scm.org/repo/hg /hgdev/src

# Mark the repo as non-publishing.
cat >> /hgdev/src/.hg/hgrc << EOF
[phases]
publish = false
EOF

sudo chown -R hg:hg /hgdev
'''.lstrip()
    .format(
        install_rust=INSTALL_RUST,
        install_pyoxidizer=INSTALL_PYOXIDIZER,
        install_pythons=INSTALL_PYTHONS,
        bootstrap_virtualenv=BOOTSTRAP_VIRTUALENV,
    )
    .replace('\r\n', '\n')
)


# Prepares /hgdev for operations.
PREPARE_HGDEV = '''
#!/bin/bash

set -e

FS=$1

ensure_device() {
    if [ -z "${DEVICE}" ]; then
        echo "could not find block device to format"
        exit 1
    fi
}

# Determine device to partition for extra filesystem.
# If only 1 volume is present, it will be the root volume and
# should be /dev/nvme0. If multiple volumes are present, the
# root volume could be nvme0 or nvme1. Use whichever one doesn't have
# a partition.
if [ -e /dev/nvme1n1 ]; then
    if [ -e /dev/nvme0n1p1 ]; then
        DEVICE=/dev/nvme1n1
    else
        DEVICE=/dev/nvme0n1
    fi
else
    DEVICE=
fi

sudo mkdir /hgwork

if [ "${FS}" != "default" -a "${FS}" != "tmpfs" ]; then
    ensure_device
    echo "creating ${FS} filesystem on ${DEVICE}"
fi

if [ "${FS}" = "default" ]; then
    :

elif [ "${FS}" = "btrfs" ]; then
    sudo mkfs.btrfs ${DEVICE}
    sudo mount ${DEVICE} /hgwork

elif [ "${FS}" = "ext3" ]; then
    # lazy_journal_init speeds up filesystem creation at the expense of
    # integrity if things crash. We are an ephemeral instance, so we don't
    # care about integrity.
    sudo mkfs.ext3 -E lazy_journal_init=1 ${DEVICE}
    sudo mount ${DEVICE} /hgwork

elif [ "${FS}" = "ext4" ]; then
    sudo mkfs.ext4 -E lazy_journal_init=1 ${DEVICE}
    sudo mount ${DEVICE} /hgwork

elif [ "${FS}" = "jfs" ]; then
    sudo mkfs.jfs ${DEVICE}
    sudo mount ${DEVICE} /hgwork

elif [ "${FS}" = "tmpfs" ]; then
    echo "creating tmpfs volume in /hgwork"
    sudo mount -t tmpfs -o size=1024M tmpfs /hgwork

elif [ "${FS}" = "xfs" ]; then
    sudo mkfs.xfs ${DEVICE}
    sudo mount ${DEVICE} /hgwork

else
    echo "unsupported filesystem: ${FS}"
    exit 1
fi

echo "/hgwork ready"

sudo chown hg:hg /hgwork
mkdir /hgwork/tmp
chown hg:hg /hgwork/tmp

rsync -a /hgdev/src /hgwork/
'''.lstrip().replace(
    '\r\n', '\n'
)


HG_UPDATE_CLEAN = '''
set -ex

HG=/hgdev/venv-bootstrap/bin/hg

cd /hgwork/src
${HG} --config extensions.purge= purge --all
${HG} update -C $1
${HG} log -r .
'''.lstrip().replace(
    '\r\n', '\n'
)


def prepare_exec_environment(ssh_client, filesystem='default'):
    """Prepare an EC2 instance to execute things.

    The AMI has an ``/hgdev`` bootstrapped with various Python installs
    and a clone of the Mercurial repo.

    In EC2, EBS volumes launched from snapshots have wonky performance behavior.
    Notably, blocks have to be copied on first access, which makes volume
    I/O extremely slow on fresh volumes.

    Furthermore, we may want to run operations, tests, etc on alternative
    filesystems so we examine behavior on different filesystems.

    This function is used to facilitate executing operations on alternate
    volumes.
    """
    sftp = ssh_client.open_sftp()

    with sftp.open('/hgdev/prepare-hgdev', 'wb') as fh:
        fh.write(PREPARE_HGDEV)
        fh.chmod(0o0777)

    command = 'sudo /hgdev/prepare-hgdev %s' % filesystem
    chan, stdin, stdout = exec_command(ssh_client, command)
    stdin.close()

    for line in stdout:
        print(line, end='')

    res = chan.recv_exit_status()

    if res:
        raise Exception('non-0 exit code updating working directory; %d' % res)


def synchronize_hg(
    source_path: pathlib.Path, ec2_instance, revision: str = None
):
    """Synchronize a local Mercurial source path to remote EC2 instance."""

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir = pathlib.Path(temp_dir)

        ssh_dir = temp_dir / '.ssh'
        ssh_dir.mkdir()
        ssh_dir.chmod(0o0700)

        public_ip = ec2_instance.public_ip_address

        ssh_config = ssh_dir / 'config'

        with ssh_config.open('w', encoding='utf-8') as fh:
            fh.write('Host %s\n' % public_ip)
            fh.write('  User hg\n')
            fh.write('  StrictHostKeyChecking no\n')
            fh.write('  UserKnownHostsFile %s\n' % (ssh_dir / 'known_hosts'))
            fh.write('  IdentityFile %s\n' % ec2_instance.ssh_private_key_path)

        if not (source_path / '.hg').is_dir():
            raise Exception(
                '%s is not a Mercurial repository; synchronization '
                'not yet supported' % source_path
            )

        env = dict(os.environ)
        env['HGPLAIN'] = '1'
        env['HGENCODING'] = 'utf-8'

        hg_bin = source_path / 'hg'

        res = subprocess.run(
            ['python3', str(hg_bin), 'log', '-r', revision, '-T', '{node}'],
            cwd=str(source_path),
            env=env,
            check=True,
            capture_output=True,
        )

        full_revision = res.stdout.decode('ascii')

        args = [
            'python3',
            str(hg_bin),
            '--config',
            'ui.ssh=ssh -F %s' % ssh_config,
            '--config',
            'ui.remotecmd=/hgdev/venv-bootstrap/bin/hg',
            # Also ensure .hgtags changes are present so auto version
            # calculation works.
            'push',
            '-f',
            '-r',
            full_revision,
            '-r',
            'file(.hgtags)',
            'ssh://%s//hgwork/src' % public_ip,
        ]

        res = subprocess.run(args, cwd=str(source_path), env=env)

        # Allow 1 (no-op) to not trigger error.
        if res.returncode not in (0, 1):
            res.check_returncode()

        # TODO support synchronizing dirty working directory.

        sftp = ec2_instance.ssh_client.open_sftp()

        with sftp.open('/hgdev/hgup', 'wb') as fh:
            fh.write(HG_UPDATE_CLEAN)
            fh.chmod(0o0700)

        chan, stdin, stdout = exec_command(
            ec2_instance.ssh_client, '/hgdev/hgup %s' % full_revision
        )
        stdin.close()

        for line in stdout:
            print(line, end='')

        res = chan.recv_exit_status()

        if res:
            raise Exception(
                'non-0 exit code updating working directory; %d' % res
            )


def run_tests(ssh_client, python_version, test_flags=None):
    """Run tests on a remote Linux machine via an SSH client."""
    test_flags = test_flags or []

    print('running tests')

    if python_version == 'system3':
        python = '/usr/bin/python3'
    elif python_version.startswith('pypy'):
        python = '/hgdev/pyenv/shims/%s' % python_version
    else:
        python = '/hgdev/pyenv/shims/python%s' % python_version

    test_flags = shlex.join(test_flags)

    command = (
        '/bin/sh -c "export TMPDIR=/hgwork/tmp; '
        'cd /hgwork/src/tests && %s run-tests.py %s"' % (python, test_flags)
    )

    chan, stdin, stdout = exec_command(ssh_client, command)

    stdin.close()

    for line in stdout:
        print(line, end='')

    return chan.recv_exit_status()
