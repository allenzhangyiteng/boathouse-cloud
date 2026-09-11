"""Docker's default app sandbox, with project-quota mutations prohibited."""
import json
from pathlib import Path

# Linux truncates ioctl requests to 32 bits. Cover native and compat SETFLAGS,
# and FSSETXATTR (project ID/inheritance). Read-only attribute queries still work.
_QUOTA_IOCTLS = (0x401c5820, 0x40086602, 0x40046602)


def _allowed_ioctl_prefixes(blocked=_QUOTA_IOCTLS, bit=31, mask=0, value=0):
    """Partition the 32-bit command space, excluding the blocked commands.

    An unconditional ALLOW shadows a narrower ERRNO in libseccomp. Replace it
    with disjoint allowed prefixes instead of appending ineffective deny rules.
    One masked comparison per rule also handles nonzero upper syscall bits.
    """
    if not blocked:
        yield mask, value
    elif bit >= 0:
        flag = 1 << bit
        for choice in (0, flag):
            subset = tuple(x for x in blocked if x & flag == choice)
            yield from _allowed_ioctl_prefixes(subset, bit - 1, mask | flag, value | choice)


def profile(builder=False):
    default = json.loads((Path(__file__).parent / 'security/moby-default.json').read_text())
    if builder:
        # Rootless BuildKit needs mount/user-namespace syscalls. Its outer
        # container still prohibits quota mutations and io_uring entry points.
        value = {'defaultAction': 'SCMP_ACT_ALLOW', 'archMap': default['archMap'], 'syscalls': []}
        for request in _QUOTA_IOCTLS:
            value['syscalls'].append({'names': ['ioctl'], 'action': 'SCMP_ACT_ERRNO', 'errnoRet': 13,
                'args': [{'index': 1, 'value': 0xffffffff, 'valueTwo': request, 'op': 'SCMP_CMP_MASKED_EQ'}]})
        value['syscalls'].append({'names': ['io_uring_setup'], 'action': 'SCMP_ACT_ERRNO', 'errnoRet': 13})
    else:
        value = default
        for rule in value['syscalls']:
            rule['names'] = [name for name in rule['names'] if name != 'ioctl']
        value['syscalls'] = [rule for rule in value['syscalls'] if rule['names']]
        for mask, command in _allowed_ioctl_prefixes():
            value['syscalls'].append({'names': ['ioctl'], 'action': 'SCMP_ACT_ALLOW',
                'args': [{'index': 1, 'value': mask, 'valueTwo': command, 'op': 'SCMP_CMP_MASKED_EQ'}]})
    return json.dumps(value, separators=(',', ':'))
