import json
from app import sandbox


def test_allowed_ioctl_space_is_exact_disjoint_complement():
    # A complete, disjoint partition proves there is no rare request or upper
    # syscall-bit combination that reopens a quota-changing operation.
    prefixes=list(sandbox._allowed_ioctl_prefixes())
    assert sum(1 << (32-mask.bit_count()) for mask,_ in prefixes)==2**32-3
    for i,(mask,value) in enumerate(prefixes):
        assert mask <= 0xffffffff and value & mask == value
        assert all(request & mask != value for request in sandbox._QUOTA_IOCTLS)
        for other_mask,other_value in prefixes[i+1:]:
            common=mask & other_mask
            assert value & common != other_value & common
    policy=json.loads(sandbox.profile())
    assert policy['defaultAction']=='SCMP_ACT_ERRNO'
    assert all(rule.get('args') for rule in policy['syscalls'] if 'ioctl' in rule['names'])
    assert not any(rule['action']=='SCMP_ACT_ALLOW' and 'io_uring_setup' in rule['names'] for rule in policy['syscalls'])


def test_builder_policy_denies_all_quota_setters_with_low32_matching():
    rules=json.loads(sandbox.profile(builder=True))['syscalls']
    deny=[rule for rule in rules if 'ioctl' in rule['names']]
    assert {rule['args'][0]['valueTwo'] for rule in deny}==set(sandbox._QUOTA_IOCTLS)
    assert all(rule['action']=='SCMP_ACT_ERRNO' and rule['args'][0]['value']==0xffffffff for rule in deny)
