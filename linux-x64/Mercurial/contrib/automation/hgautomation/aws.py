# aws.py - Automation code for Amazon Web Services
#
# Copyright 2019 Gregory Szorc <gregory.szorc@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

# no-check-code because Python 3 native.

import contextlib
import copy
import hashlib
import json
import os
import pathlib
import subprocess
import time

import boto3
import botocore.exceptions

from .linux import BOOTSTRAP_DEBIAN
from .ssh import (
    exec_command as ssh_exec_command,
    wait_for_ssh,
)
from .winrm import (
    run_powershell,
    wait_for_winrm,
)


SOURCE_ROOT = pathlib.Path(
    os.path.abspath(__file__)
).parent.parent.parent.parent

INSTALL_WINDOWS_DEPENDENCIES = (
    SOURCE_ROOT / 'contrib' / 'install-windows-dependencies.ps1'
)


INSTANCE_TYPES_WITH_STORAGE = {
    'c5d',
    'd2',
    'h1',
    'i3',
    'm5ad',
    'm5d',
    'r5d',
    'r5ad',
    'x1',
    'z1d',
}


AMAZON_ACCOUNT_ID = '801119661308'
DEBIAN_ACCOUNT_ID = '379101102735'
DEBIAN_ACCOUNT_ID_2 = '136693071363'
UBUNTU_ACCOUNT_ID = '099720109477'


WINDOWS_BASE_IMAGE_NAME = 'Windows_Server-2022-English-Full-Base-*'


KEY_PAIRS = {
    'automation',
}


SECURITY_GROUPS = {
    'linux-dev-1': {
        'description': 'Mercurial Linux instances that perform build/test automation',
        'ingress': [
            {
                'FromPort': 22,
                'ToPort': 22,
                'IpProtocol': 'tcp',
                'IpRanges': [
                    {
                        'CidrIp': '0.0.0.0/0',
                        'Description': 'SSH from entire Internet',
                    },
                ],
            },
        ],
    },
    'windows-dev-1': {
        'description': 'Mercurial Windows instances that perform build automation',
        'ingress': [
            {
                'FromPort': 22,
                'ToPort': 22,
                'IpProtocol': 'tcp',
                'IpRanges': [
                    {
                        'CidrIp': '0.0.0.0/0',
                        'Description': 'SSH from entire Internet',
                    },
                ],
            },
            {
                'FromPort': 3389,
                'ToPort': 3389,
                'IpProtocol': 'tcp',
                'IpRanges': [
                    {
                        'CidrIp': '0.0.0.0/0',
                        'Description': 'RDP from entire Internet',
                    },
                ],
            },
            {
                'FromPort': 5985,
                'ToPort': 5986,
                'IpProtocol': 'tcp',
                'IpRanges': [
                    {
                        'CidrIp': '0.0.0.0/0',
                        'Description': 'PowerShell Remoting (Windows Remote Management)',
                    },
                ],
            },
        ],
    },
}


IAM_ROLES = {
    'ephemeral-ec2-role-1': {
        'description': 'Mercurial temporary EC2 instances',
        'policy_arns': [
            'arn:aws:iam::aws:policy/service-role/AmazonEC2RoleforSSM',
        ],
    },
}


ASSUME_ROLE_POLICY_DOCUMENT = '''
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ec2.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
'''.strip()


IAM_INSTANCE_PROFILES = {
    'ephemeral-ec2-1': {
        'roles': [
            'ephemeral-ec2-role-1',
        ],
    }
}


# User Data for Windows EC2 instance. Mainly used to set the password
# and configure WinRM.
# Inspired by the User Data script used by Packer
# (from https://www.packer.io/intro/getting-started/build-image.html).
WINDOWS_USER_DATA = r'''
<powershell>

# TODO enable this once we figure out what is failing.
#$ErrorActionPreference = "stop"

# Set administrator password
net user Administrator "%s"
wmic useraccount where "name='Administrator'" set PasswordExpires=FALSE

# And set it via EC2Launch so it persists across reboots.
$config = & $env:ProgramFiles\Amazon\EC2Launch\EC2Launch.exe get-agent-config --format json | ConvertFrom-Json
$config | ConvertTo-Json -Depth 6 | Out-File -encoding UTF8 $env:ProgramData/Amazon/EC2Launch/config/agent-config.yml
$setAdminAccount = @"
{
  "task": "setAdminAccount",
  "inputs": {
    "password": {
      "type": "static",
      "data": "%s"
    }
  }
}
"@
$config.config | %%{if($_.stage -eq 'preReady'){$_.tasks += (ConvertFrom-Json -InputObject $setAdminAccount)}}
$config | ConvertTo-Json -Depth 6 | Out-File -encoding UTF8 $env:ProgramData/Amazon/EC2Launch/config/agent-config.yml

# First, make sure WinRM can't be connected to
netsh advfirewall firewall set rule name="Windows Remote Management (HTTP-In)" new enable=yes action=block

# Delete any existing WinRM listeners
winrm delete winrm/config/listener?Address=*+Transport=HTTP  2>$Null
winrm delete winrm/config/listener?Address=*+Transport=HTTPS 2>$Null

# Create a new WinRM listener and configure
winrm create winrm/config/listener?Address=*+Transport=HTTP
winrm set winrm/config/winrs '@{MaxMemoryPerShellMB="0"}'
winrm set winrm/config '@{MaxTimeoutms="7200000"}'
winrm set winrm/config/service '@{AllowUnencrypted="true"}'
winrm set winrm/config/service '@{MaxConcurrentOperationsPerUser="12000"}'
winrm set winrm/config/service/auth '@{Basic="true"}'
winrm set winrm/config/client/auth '@{Basic="true"}'

# Configure UAC to allow privilege elevation in remote shells
$Key = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
$Setting = 'LocalAccountTokenFilterPolicy'
Set-ItemProperty -Path $Key -Name $Setting -Value 1 -Force

# Avoid long usernames in the temp directory path because the '~' causes extra quoting in ssh output
[System.Environment]::SetEnvironmentVariable('TMP', 'C:\Temp', [System.EnvironmentVariableTarget]::User)
[System.Environment]::SetEnvironmentVariable('TEMP', 'C:\Temp', [System.EnvironmentVariableTarget]::User)

# Configure and restart the WinRM Service; Enable the required firewall exception
Stop-Service -Name WinRM
Set-Service -Name WinRM -StartupType Automatic
netsh advfirewall firewall set rule name="Windows Remote Management (HTTP-In)" new action=allow localip=any remoteip=any
Start-Service -Name WinRM

# Disable firewall on private network interfaces so prompts don't appear.
Set-NetFirewallProfile -Name private -Enabled false
</powershell>
'''.lstrip()


WINDOWS_BOOTSTRAP_POWERSHELL = '''
Write-Output "installing PowerShell dependencies"
Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force
Set-PSRepository -Name PSGallery -InstallationPolicy Trusted
Install-Module -Name OpenSSHUtils -RequiredVersion 0.0.2.0

Write-Output "installing OpenSSL server"
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
# Various tools will attempt to use older versions of .NET. So we enable
# the feature that provides them so it doesn't have to be auto-enabled
# later.
Write-Output "enabling .NET Framework feature"
Install-WindowsFeature -Name Net-Framework-Core
'''


class AWSConnection:
    """Manages the state of a connection with AWS."""

    def __init__(self, automation, region: str, ensure_ec2_state: bool = True):
        self.automation = automation
        self.local_state_path = automation.state_path

        self.prefix = 'hg-'

        self.session = boto3.session.Session(region_name=region)
        self.ec2client = self.session.client('ec2')
        self.ec2resource = self.session.resource('ec2')
        self.iamclient = self.session.client('iam')
        self.iamresource = self.session.resource('iam')
        self.security_groups = {}

        if ensure_ec2_state:
            ensure_key_pairs(automation.state_path, self.ec2resource)
            self.security_groups = ensure_security_groups(self.ec2resource)
            ensure_iam_state(self.iamclient, self.iamresource)

    def key_pair_path_private(self, name):
        """Path to a key pair private key file."""
        return self.local_state_path / 'keys' / ('keypair-%s' % name)

    def key_pair_path_public(self, name):
        return self.local_state_path / 'keys' / ('keypair-%s.pub' % name)


def rsa_key_fingerprint(p: pathlib.Path):
    """Compute the fingerprint of an RSA private key."""

    # TODO use rsa package.
    res = subprocess.run(
        [
            'openssl',
            'pkcs8',
            '-in',
            str(p),
            '-nocrypt',
            '-topk8',
            '-outform',
            'DER',
        ],
        capture_output=True,
        check=True,
    )

    sha1 = hashlib.sha1(res.stdout).hexdigest()
    return ':'.join(a + b for a, b in zip(sha1[::2], sha1[1::2]))


def ensure_key_pairs(state_path: pathlib.Path, ec2resource, prefix='hg-'):
    remote_existing = {}

    for kpi in ec2resource.key_pairs.all():
        if kpi.name.startswith(prefix):
            remote_existing[kpi.name[len(prefix) :]] = kpi.key_fingerprint

    # Validate that we have these keys locally.
    key_path = state_path / 'keys'
    key_path.mkdir(exist_ok=True, mode=0o700)

    def remove_remote(name):
        print('deleting key pair %s' % name)
        key = ec2resource.KeyPair(name)
        key.delete()

    def remove_local(name):
        pub_full = key_path / ('keypair-%s.pub' % name)
        priv_full = key_path / ('keypair-%s' % name)

        print('removing %s' % pub_full)
        pub_full.unlink()
        print('removing %s' % priv_full)
        priv_full.unlink()

    local_existing = {}

    for f in sorted(os.listdir(key_path)):
        if not f.startswith('keypair-') or not f.endswith('.pub'):
            continue

        name = f[len('keypair-') : -len('.pub')]

        pub_full = key_path / f
        priv_full = key_path / ('keypair-%s' % name)

        with open(pub_full, encoding='ascii') as fh:
            data = fh.read()

        if not data.startswith('ssh-rsa '):
            print(
                'unexpected format for key pair file: %s; removing' % pub_full
            )
            pub_full.unlink()
            priv_full.unlink()
            continue

        local_existing[name] = rsa_key_fingerprint(priv_full)

    for name in sorted(set(remote_existing) | set(local_existing)):
        if name not in local_existing:
            actual = '%s%s' % (prefix, name)
            print('remote key %s does not exist locally' % name)
            remove_remote(actual)
            del remote_existing[name]

        elif name not in remote_existing:
            print('local key %s does not exist remotely' % name)
            remove_local(name)
            del local_existing[name]

        elif remote_existing[name] != local_existing[name]:
            print(
                'key fingerprint mismatch for %s; '
                'removing from local and remote' % name
            )
            remove_local(name)
            remove_remote('%s%s' % (prefix, name))
            del local_existing[name]
            del remote_existing[name]

    missing = KEY_PAIRS - set(remote_existing)

    for name in sorted(missing):
        actual = '%s%s' % (prefix, name)
        print('creating key pair %s' % actual)

        priv_full = key_path / ('keypair-%s' % name)
        pub_full = key_path / ('keypair-%s.pub' % name)

        kp = ec2resource.create_key_pair(KeyName=actual)

        with priv_full.open('w', encoding='ascii') as fh:
            fh.write(kp.key_material)
            fh.write('\n')

        priv_full.chmod(0o0600)

        # SSH public key can be extracted via `ssh-keygen`.
        with pub_full.open('w', encoding='ascii') as fh:
            subprocess.run(
                ['ssh-keygen', '-y', '-f', str(priv_full)],
                stdout=fh,
                check=True,
            )

        pub_full.chmod(0o0600)


def delete_instance_profile(profile):
    for role in profile.roles:
        print(
            'removing role %s from instance profile %s'
            % (role.name, profile.name)
        )
        profile.remove_role(RoleName=role.name)

    print('deleting instance profile %s' % profile.name)
    profile.delete()


def ensure_iam_state(iamclient, iamresource, prefix='hg-'):
    """Ensure IAM state is in sync with our canonical definition."""

    remote_profiles = {}

    for profile in iamresource.instance_profiles.all():
        if profile.name.startswith(prefix):
            remote_profiles[profile.name[len(prefix) :]] = profile

    for name in sorted(set(remote_profiles) - set(IAM_INSTANCE_PROFILES)):
        delete_instance_profile(remote_profiles[name])
        del remote_profiles[name]

    remote_roles = {}

    for role in iamresource.roles.all():
        if role.name.startswith(prefix):
            remote_roles[role.name[len(prefix) :]] = role

    for name in sorted(set(remote_roles) - set(IAM_ROLES)):
        role = remote_roles[name]

        print('removing role %s' % role.name)
        role.delete()
        del remote_roles[name]

    # We've purged remote state that doesn't belong. Create missing
    # instance profiles and roles.
    for name in sorted(set(IAM_INSTANCE_PROFILES) - set(remote_profiles)):
        actual = '%s%s' % (prefix, name)
        print('creating IAM instance profile %s' % actual)

        profile = iamresource.create_instance_profile(
            InstanceProfileName=actual
        )
        remote_profiles[name] = profile

        waiter = iamclient.get_waiter('instance_profile_exists')
        waiter.wait(InstanceProfileName=actual)
        print('IAM instance profile %s is available' % actual)

    for name in sorted(set(IAM_ROLES) - set(remote_roles)):
        entry = IAM_ROLES[name]

        actual = '%s%s' % (prefix, name)
        print('creating IAM role %s' % actual)

        role = iamresource.create_role(
            RoleName=actual,
            Description=entry['description'],
            AssumeRolePolicyDocument=ASSUME_ROLE_POLICY_DOCUMENT,
        )

        waiter = iamclient.get_waiter('role_exists')
        waiter.wait(RoleName=actual)
        print('IAM role %s is available' % actual)

        remote_roles[name] = role

        for arn in entry['policy_arns']:
            print('attaching policy %s to %s' % (arn, role.name))
            role.attach_policy(PolicyArn=arn)

    # Now reconcile state of profiles.
    for name, meta in sorted(IAM_INSTANCE_PROFILES.items()):
        profile = remote_profiles[name]
        wanted = {'%s%s' % (prefix, role) for role in meta['roles']}
        have = {role.name for role in profile.roles}

        for role in sorted(have - wanted):
            print('removing role %s from %s' % (role, profile.name))
            profile.remove_role(RoleName=role)

        for role in sorted(wanted - have):
            print('adding role %s to %s' % (role, profile.name))
            profile.add_role(RoleName=role)


def find_image(ec2resource, owner_id, name, reverse_sort_field=None):
    """Find an AMI by its owner ID and name."""

    images = ec2resource.images.filter(
        Filters=[
            {
                'Name': 'owner-id',
                'Values': [owner_id],
            },
            {
                'Name': 'state',
                'Values': ['available'],
            },
            {
                'Name': 'image-type',
                'Values': ['machine'],
            },
            {
                'Name': 'name',
                'Values': [name],
            },
        ]
    )

    if reverse_sort_field:
        images = sorted(
            images,
            key=lambda image: getattr(image, reverse_sort_field),
            reverse=True,
        )

    for image in images:
        return image

    raise Exception('unable to find image for %s' % name)


def ensure_security_groups(ec2resource, prefix='hg-'):
    """Ensure all necessary Mercurial security groups are present.

    All security groups are prefixed with ``hg-`` by default. Any security
    groups having this prefix but aren't in our list are deleted.
    """
    existing = {}

    for group in ec2resource.security_groups.all():
        if group.group_name.startswith(prefix):
            existing[group.group_name[len(prefix) :]] = group

    purge = set(existing) - set(SECURITY_GROUPS)

    for name in sorted(purge):
        group = existing[name]
        print('removing legacy security group: %s' % group.group_name)
        group.delete()

    security_groups = {}

    for name, group in sorted(SECURITY_GROUPS.items()):
        if name in existing:
            security_groups[name] = existing[name]
            continue

        actual = '%s%s' % (prefix, name)
        print('adding security group %s' % actual)

        group_res = ec2resource.create_security_group(
            Description=group['description'],
            GroupName=actual,
        )

        group_res.authorize_ingress(
            IpPermissions=group['ingress'],
        )

        security_groups[name] = group_res

    return security_groups


def terminate_ec2_instances(ec2resource, prefix='hg-'):
    """Terminate all EC2 instances managed by us."""
    waiting = []

    for instance in ec2resource.instances.all():
        if instance.state['Name'] == 'terminated':
            continue

        for tag in instance.tags or []:
            if tag['Key'] == 'Name' and tag['Value'].startswith(prefix):
                print('terminating %s' % instance.id)
                instance.terminate()
                waiting.append(instance)

    for instance in waiting:
        instance.wait_until_terminated()


def remove_resources(c, prefix='hg-'):
    """Purge all of our resources in this EC2 region."""
    ec2resource = c.ec2resource
    iamresource = c.iamresource

    terminate_ec2_instances(ec2resource, prefix=prefix)

    for image in ec2resource.images.filter(Owners=['self']):
        if image.name.startswith(prefix):
            remove_ami(ec2resource, image)

    for group in ec2resource.security_groups.all():
        if group.group_name.startswith(prefix):
            print('removing security group %s' % group.group_name)
            group.delete()

    for profile in iamresource.instance_profiles.all():
        if profile.name.startswith(prefix):
            delete_instance_profile(profile)

    for role in iamresource.roles.all():
        if role.name.startswith(prefix):
            for p in role.attached_policies.all():
                print('detaching policy %s from %s' % (p.arn, role.name))
                role.detach_policy(PolicyArn=p.arn)

            print('removing role %s' % role.name)
            role.delete()


def wait_for_ip_addresses(instances):
    """Wait for the public IP addresses of an iterable of instances."""
    for instance in instances:
        while True:
            if not instance.public_ip_address:
                time.sleep(2)
                instance.reload()
                continue

            print(
                'public IP address for %s: %s'
                % (instance.id, instance.public_ip_address)
            )
            break


def remove_ami(ec2resource, image):
    """Remove an AMI and its underlying snapshots."""
    snapshots = []

    for device in image.block_device_mappings:
        if 'Ebs' in device:
            snapshots.append(ec2resource.Snapshot(device['Ebs']['SnapshotId']))

    print('deregistering %s' % image.id)
    image.deregister()

    for snapshot in snapshots:
        print('deleting snapshot %s' % snapshot.id)
        snapshot.delete()


def wait_for_ssm(ssmclient, instances):
    """Wait for SSM to come online for an iterable of instance IDs."""
    while True:
        res = ssmclient.describe_instance_information(
            Filters=[
                {
                    'Key': 'InstanceIds',
                    'Values': [i.id for i in instances],
                },
            ],
        )

        available = len(res['InstanceInformationList'])
        wanted = len(instances)

        print('%d/%d instances available in SSM' % (available, wanted))

        if available == wanted:
            return

        time.sleep(2)


def run_ssm_command(ssmclient, instances, document_name, parameters):
    """Run a PowerShell script on an EC2 instance."""

    res = ssmclient.send_command(
        InstanceIds=[i.id for i in instances],
        DocumentName=document_name,
        Parameters=parameters,
        CloudWatchOutputConfig={
            'CloudWatchOutputEnabled': True,
        },
    )

    command_id = res['Command']['CommandId']

    for instance in instances:
        while True:
            try:
                res = ssmclient.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance.id,
                )
            except botocore.exceptions.ClientError as e:
                if e.response['Error']['Code'] == 'InvocationDoesNotExist':
                    print('could not find SSM command invocation; waiting')
                    time.sleep(1)
                    continue
                else:
                    raise

            if res['Status'] == 'Success':
                break
            elif res['Status'] in ('Pending', 'InProgress', 'Delayed'):
                time.sleep(2)
            else:
                raise Exception(
                    'command failed on %s: %s' % (instance.id, res['Status'])
                )


@contextlib.contextmanager
def temporary_ec2_instances(ec2resource, config):
    """Create temporary EC2 instances.

    This is a proxy to ``ec2client.run_instances(**config)`` that takes care of
    managing the lifecycle of the instances.

    When the context manager exits, the instances are terminated.

    The context manager evaluates to the list of data structures
    describing each created instance. The instances may not be available
    for work immediately: it is up to the caller to wait for the instance
    to start responding.
    """

    ids = None

    try:
        res = ec2resource.create_instances(**config)

        ids = [i.id for i in res]
        print('started instances: %s' % ' '.join(ids))

        yield res
    finally:
        if ids:
            print('terminating instances: %s' % ' '.join(ids))
            for instance in res:
                instance.terminate()
            print('terminated %d instances' % len(ids))


@contextlib.contextmanager
def create_temp_windows_ec2_instances(
    c: AWSConnection, config, bootstrap: bool = False
):
    """Create temporary Windows EC2 instances.

    This is a higher-level wrapper around ``create_temp_ec2_instances()`` that
    configures the Windows instance for Windows Remote Management. The emitted
    instances will have a ``winrm_client`` attribute containing a
    ``pypsrp.client.Client`` instance bound to the instance.
    """
    if 'IamInstanceProfile' in config:
        raise ValueError('IamInstanceProfile cannot be provided in config')
    if 'UserData' in config:
        raise ValueError('UserData cannot be provided in config')

    password = c.automation.default_password()

    config = copy.deepcopy(config)
    config['IamInstanceProfile'] = {
        'Name': 'hg-ephemeral-ec2-1',
    }
    config.setdefault('TagSpecifications', []).append(
        {
            'ResourceType': 'instance',
            'Tags': [{'Key': 'Name', 'Value': 'hg-temp-windows'}],
        }
    )

    if bootstrap:
        config['UserData'] = WINDOWS_USER_DATA % (password, password)

    with temporary_ec2_instances(c.ec2resource, config) as instances:
        wait_for_ip_addresses(instances)

        print('waiting for Windows Remote Management service...')

        for instance in instances:
            client = wait_for_winrm(
                instance.public_ip_address, 'Administrator', password
            )
            print('established WinRM connection to %s' % instance.id)
            instance.winrm_client = client

        yield instances


def resolve_fingerprint(fingerprint):
    fingerprint = json.dumps(fingerprint, sort_keys=True)
    return hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()


def find_and_reconcile_image(ec2resource, name, fingerprint):
    """Attempt to find an existing EC2 AMI with a name and fingerprint.

    If an image with the specified fingerprint is found, it is returned.
    Otherwise None is returned.

    Existing images for the specified name that don't have the specified
    fingerprint or are missing required metadata or deleted.
    """
    # Find existing AMIs with this name and delete the ones that are invalid.
    # Store a reference to a good image so it can be returned one the
    # image state is reconciled.
    images = ec2resource.images.filter(
        Filters=[{'Name': 'name', 'Values': [name]}]
    )

    existing_image = None

    for image in images:
        if image.tags is None:
            print(
                'image %s for %s lacks required tags; removing'
                % (image.id, image.name)
            )
            remove_ami(ec2resource, image)
        else:
            tags = {t['Key']: t['Value'] for t in image.tags}

            if tags.get('HGIMAGEFINGERPRINT') == fingerprint:
                existing_image = image
            else:
                print(
                    'image %s for %s has wrong fingerprint; removing'
                    % (image.id, image.name)
                )
                remove_ami(ec2resource, image)

    return existing_image


def create_ami_from_instance(
    ec2client, instance, name, description, fingerprint
):
    """Create an AMI from a running instance.

    Returns the ``ec2resource.Image`` representing the created AMI.
    """
    instance.stop()

    ec2client.get_waiter('instance_stopped').wait(
        InstanceIds=[instance.id],
        WaiterConfig={
            'Delay': 5,
        },
    )
    print('%s is stopped' % instance.id)

    image = instance.create_image(
        Name=name,
        Description=description,
    )

    image.create_tags(
        Tags=[
            {
                'Key': 'HGIMAGEFINGERPRINT',
                'Value': fingerprint,
            },
        ]
    )

    print('waiting for image %s' % image.id)

    ec2client.get_waiter('image_available').wait(
        ImageIds=[image.id],
    )

    print('image %s available as %s' % (image.id, image.name))

    return image


def ensure_linux_dev_ami(c: AWSConnection, distro='debian10', prefix='hg-'):
    """Ensures a Linux development AMI is available and up-to-date.

    Returns an ``ec2.Image`` of either an existing AMI or a newly-built one.
    """
    ec2client = c.ec2client
    ec2resource = c.ec2resource

    name = '%s%s-%s' % (prefix, 'linux-dev', distro)

    if distro == 'debian9':
        image = find_image(
            ec2resource,
            DEBIAN_ACCOUNT_ID,
            'debian-stretch-hvm-x86_64-gp2-2019-09-08-17994',
        )
        ssh_username = 'admin'
    elif distro == 'debian10':
        image = find_image(
            ec2resource,
            DEBIAN_ACCOUNT_ID_2,
            'debian-10-amd64-20190909-10',
        )
        ssh_username = 'admin'
    elif distro == 'ubuntu18.04':
        image = find_image(
            ec2resource,
            UBUNTU_ACCOUNT_ID,
            'ubuntu/images/hvm-ssd/ubuntu-bionic-18.04-amd64-server-20190918',
        )
        ssh_username = 'ubuntu'
    elif distro == 'ubuntu19.04':
        image = find_image(
            ec2resource,
            UBUNTU_ACCOUNT_ID,
            'ubuntu/images/hvm-ssd/ubuntu-disco-19.04-amd64-server-20190918',
        )
        ssh_username = 'ubuntu'
    else:
        raise ValueError('unsupported Linux distro: %s' % distro)

    config = {
        'BlockDeviceMappings': [
            {
                'DeviceName': image.block_device_mappings[0]['DeviceName'],
                'Ebs': {
                    'DeleteOnTermination': True,
                    'VolumeSize': 10,
                    'VolumeType': 'gp3',
                },
            },
        ],
        'EbsOptimized': True,
        'ImageId': image.id,
        'InstanceInitiatedShutdownBehavior': 'stop',
        # 8 VCPUs for compiling Python.
        'InstanceType': 't3.2xlarge',
        'KeyName': '%sautomation' % prefix,
        'MaxCount': 1,
        'MinCount': 1,
        'SecurityGroupIds': [c.security_groups['linux-dev-1'].id],
    }

    requirements3_path = (
        pathlib.Path(__file__).parent.parent / 'linux-requirements-py3.txt'
    )
    requirements35_path = (
        pathlib.Path(__file__).parent.parent / 'linux-requirements-py3.5.txt'
    )
    with requirements3_path.open('r', encoding='utf-8') as fh:
        requirements3 = fh.read()
    with requirements35_path.open('r', encoding='utf-8') as fh:
        requirements35 = fh.read()

    # Compute a deterministic fingerprint to determine whether image needs to
    # be regenerated.
    fingerprint = resolve_fingerprint(
        {
            'instance_config': config,
            'bootstrap_script': BOOTSTRAP_DEBIAN,
            'requirements_py3': requirements3,
            'requirements_py35': requirements35,
        }
    )

    existing_image = find_and_reconcile_image(ec2resource, name, fingerprint)

    if existing_image:
        return existing_image

    print('no suitable %s image found; creating one...' % name)

    with temporary_ec2_instances(ec2resource, config) as instances:
        wait_for_ip_addresses(instances)

        instance = instances[0]

        client = wait_for_ssh(
            instance.public_ip_address,
            22,
            username=ssh_username,
            key_filename=str(c.key_pair_path_private('automation')),
        )

        home = '/home/%s' % ssh_username

        with client:
            print('connecting to SSH server')
            sftp = client.open_sftp()

            print('uploading bootstrap files')
            with sftp.open('%s/bootstrap' % home, 'wb') as fh:
                fh.write(BOOTSTRAP_DEBIAN)
                fh.chmod(0o0700)

            with sftp.open('%s/requirements-py3.txt' % home, 'wb') as fh:
                fh.write(requirements3)
                fh.chmod(0o0700)

            with sftp.open('%s/requirements-py3.5.txt' % home, 'wb') as fh:
                fh.write(requirements35)
                fh.chmod(0o0700)

            print('executing bootstrap')
            chan, stdin, stdout = ssh_exec_command(
                client, '%s/bootstrap' % home
            )
            stdin.close()

            for line in stdout:
                print(line, end='')

            res = chan.recv_exit_status()
            if res:
                raise Exception('non-0 exit from bootstrap: %d' % res)

            print(
                'bootstrap completed; stopping %s to create %s'
                % (instance.id, name)
            )

        return create_ami_from_instance(
            ec2client,
            instance,
            name,
            'Mercurial Linux development environment',
            fingerprint,
        )


@contextlib.contextmanager
def temporary_linux_dev_instances(
    c: AWSConnection,
    image,
    instance_type,
    prefix='hg-',
    ensure_extra_volume=False,
):
    """Create temporary Linux development EC2 instances.

    Context manager resolves to a list of ``ec2.Instance`` that were created
    and are running.

    ``ensure_extra_volume`` can be set to ``True`` to require that instances
    have a 2nd storage volume available other than the primary AMI volume.
    For instance types with instance storage, this does nothing special.
    But for instance types without instance storage, an additional EBS volume
    will be added to the instance.

    Instances have an ``ssh_client`` attribute containing a paramiko SSHClient
    instance bound to the instance.

    Instances have an ``ssh_private_key_path`` attributing containing the
    str path to the SSH private key to connect to the instance.
    """

    block_device_mappings = [
        {
            'DeviceName': image.block_device_mappings[0]['DeviceName'],
            'Ebs': {
                'DeleteOnTermination': True,
                'VolumeSize': 12,
                'VolumeType': 'gp3',
            },
        }
    ]

    # This is not an exhaustive list of instance types having instance storage.
    # But
    if ensure_extra_volume and not instance_type.startswith(
        tuple(INSTANCE_TYPES_WITH_STORAGE)
    ):
        main_device = block_device_mappings[0]['DeviceName']

        if main_device == 'xvda':
            second_device = 'xvdb'
        elif main_device == '/dev/sda1':
            second_device = '/dev/sdb'
        else:
            raise ValueError(
                'unhandled primary EBS device name: %s' % main_device
            )

        block_device_mappings.append(
            {
                'DeviceName': second_device,
                'Ebs': {
                    'DeleteOnTermination': True,
                    'VolumeSize': 8,
                    'VolumeType': 'gp3',
                },
            }
        )

    config = {
        'BlockDeviceMappings': block_device_mappings,
        'EbsOptimized': True,
        'ImageId': image.id,
        'InstanceInitiatedShutdownBehavior': 'terminate',
        'InstanceType': instance_type,
        'KeyName': '%sautomation' % prefix,
        'MaxCount': 1,
        'MinCount': 1,
        'SecurityGroupIds': [c.security_groups['linux-dev-1'].id],
    }

    with temporary_ec2_instances(c.ec2resource, config) as instances:
        wait_for_ip_addresses(instances)

        ssh_private_key_path = str(c.key_pair_path_private('automation'))

        for instance in instances:
            client = wait_for_ssh(
                instance.public_ip_address,
                22,
                username='hg',
                key_filename=ssh_private_key_path,
            )

            instance.ssh_client = client
            instance.ssh_private_key_path = ssh_private_key_path

        try:
            yield instances
        finally:
            for instance in instances:
                instance.ssh_client.close()


def ensure_windows_dev_ami(
    c: AWSConnection,
    prefix='hg-',
    base_image_name=WINDOWS_BASE_IMAGE_NAME,
):
    """Ensure Windows Development AMI is available and up-to-date.

    If necessary, a modern AMI will be built by starting a temporary EC2
    instance and bootstrapping it.

    Obsolete AMIs will be deleted so there is only a single AMI having the
    desired name.

    Returns an ``ec2.Image`` of either an existing AMI or a newly-built
    one.
    """
    ec2client = c.ec2client
    ec2resource = c.ec2resource
    ssmclient = c.session.client('ssm')

    name = '%s%s' % (prefix, 'windows-dev')

    image = find_image(
        ec2resource,
        AMAZON_ACCOUNT_ID,
        base_image_name,
        reverse_sort_field="name",
    )

    config = {
        'BlockDeviceMappings': [
            {
                'DeviceName': '/dev/sda1',
                'Ebs': {
                    'DeleteOnTermination': True,
                    'VolumeSize': 32,
                    'VolumeType': 'gp3',
                },
            }
        ],
        'ImageId': image.id,
        'InstanceInitiatedShutdownBehavior': 'stop',
        'InstanceType': 'm6i.large',
        'KeyName': '%sautomation' % prefix,
        'MaxCount': 1,
        'MinCount': 1,
        'SecurityGroupIds': [c.security_groups['windows-dev-1'].id],
    }

    commands = [
        # Need to start the service so sshd_config is generated.
        'Start-Service sshd',
        'Write-Output "modifying sshd_config"',
        r'$content = Get-Content C:\ProgramData\ssh\sshd_config',
        '$content = $content -replace "Match Group administrators","" -replace "AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys",""',
        r'$content | Set-Content C:\ProgramData\ssh\sshd_config',
        'Import-Module OpenSSHUtils',
        r'Repair-SshdConfigPermission C:\ProgramData\ssh\sshd_config -Confirm:$false',
        'Restart-Service sshd',
        'Write-Output "installing OpenSSL client"',
        'Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0',
        'Set-Service -Name sshd -StartupType "Automatic"',
        'Write-Output "OpenSSH server running"',
    ]

    with INSTALL_WINDOWS_DEPENDENCIES.open('r', encoding='utf-8') as fh:
        commands.extend(l.rstrip() for l in fh)

    # Disable Windows Defender when bootstrapping because it just slows
    # things down.
    commands.insert(0, 'Set-MpPreference -DisableRealtimeMonitoring $true')
    commands.append('Set-MpPreference -DisableRealtimeMonitoring $false')

    # Trigger shutdown to prepare for imaging.
    commands.append(
        'Stop-Computer -ComputerName localhost',
    )

    # Compute a deterministic fingerprint to determine whether image needs
    # to be regenerated.
    fingerprint = resolve_fingerprint(
        {
            'instance_config': config,
            'user_data': WINDOWS_USER_DATA,
            'initial_bootstrap': WINDOWS_BOOTSTRAP_POWERSHELL,
            'bootstrap_commands': commands,
            'base_image_name': base_image_name,
        }
    )

    existing_image = find_and_reconcile_image(ec2resource, name, fingerprint)

    if existing_image:
        return existing_image

    print('no suitable Windows development image found; creating one...')

    with create_temp_windows_ec2_instances(
        c, config, bootstrap=True
    ) as instances:
        assert len(instances) == 1
        instance = instances[0]

        wait_for_ssm(ssmclient, [instance])

        # On first boot, install various Windows updates.
        # We would ideally use PowerShell Remoting for this. However, there are
        # trust issues that make it difficult to invoke Windows Update
        # remotely. So we use SSM, which has a mechanism for running Windows
        # Update.
        print('installing Windows features...')
        run_ssm_command(
            ssmclient,
            [instance],
            'AWS-RunPowerShellScript',
            {
                'commands': WINDOWS_BOOTSTRAP_POWERSHELL.split('\n'),
            },
        )

        # Reboot so all updates are fully applied.
        #
        # We don't use instance.reboot() here because it is asynchronous and
        # we don't know when exactly the instance has rebooted. It could take
        # a while to stop and we may start trying to interact with the instance
        # before it has rebooted.
        print('rebooting instance %s' % instance.id)
        instance.stop()
        ec2client.get_waiter('instance_stopped').wait(
            InstanceIds=[instance.id],
            WaiterConfig={
                'Delay': 5,
            },
        )

        instance.start()
        wait_for_ip_addresses([instance])

        # There is a race condition here between the User Data PS script running
        # and us connecting to WinRM. This can manifest as
        # "AuthorizationManager check failed" failures during run_powershell().
        # TODO figure out a workaround.

        print('waiting for Windows Remote Management to come back...')
        client = wait_for_winrm(
            instance.public_ip_address,
            'Administrator',
            c.automation.default_password(),
        )
        print('established WinRM connection to %s' % instance.id)
        instance.winrm_client = client

        print('bootstrapping instance...')
        run_powershell(instance.winrm_client, '\n'.join(commands))

        print('bootstrap completed; stopping %s to create image' % instance.id)
        return create_ami_from_instance(
            ec2client,
            instance,
            name,
            'Mercurial Windows development environment',
            fingerprint,
        )


@contextlib.contextmanager
def temporary_windows_dev_instances(
    c: AWSConnection,
    image,
    instance_type,
    prefix='hg-',
    disable_antivirus=False,
):
    """Create a temporary Windows development EC2 instance.

    Context manager resolves to the list of ``EC2.Instance`` that were created.
    """
    config = {
        'BlockDeviceMappings': [
            {
                'DeviceName': '/dev/sda1',
                'Ebs': {
                    'DeleteOnTermination': True,
                    'VolumeSize': 32,
                    'VolumeType': 'gp3',
                },
            }
        ],
        'ImageId': image.id,
        'InstanceInitiatedShutdownBehavior': 'stop',
        'InstanceType': instance_type,
        'KeyName': '%sautomation' % prefix,
        'MaxCount': 1,
        'MinCount': 1,
        'SecurityGroupIds': [c.security_groups['windows-dev-1'].id],
    }

    with create_temp_windows_ec2_instances(c, config) as instances:
        if disable_antivirus:
            for instance in instances:
                run_powershell(
                    instance.winrm_client,
                    'Set-MpPreference -DisableRealtimeMonitoring $true',
                )

        yield instances
