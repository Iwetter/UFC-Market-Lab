import pytest

from backend.development import DevelopmentLease


def test_lease_prevents_overlap_and_requires_owner_token(tmp_path):
    first=DevelopmentLease(tmp_path).acquire('run-1',now=100)
    second=DevelopmentLease(tmp_path).acquire('run-2',now=101)
    assert first['acquired'] and not second['acquired'] and second['owner']=='run-1'
    with pytest.raises(ValueError):DevelopmentLease(tmp_path).release('not-the-owner')
    assert DevelopmentLease(tmp_path).status(now=102)['active']
    assert DevelopmentLease(tmp_path).release(first['token'])
    assert not DevelopmentLease(tmp_path).status(now=103)['active']


def test_expired_run_is_recoverable_without_overwriting_active_run(tmp_path):
    old=DevelopmentLease(tmp_path).acquire('old',ttl_seconds=60,now=100)
    current=DevelopmentLease(tmp_path).acquire('new',now=161)
    assert current['acquired']
    with pytest.raises(ValueError):DevelopmentLease(tmp_path).release(old['token'])
    assert DevelopmentLease(tmp_path).status(now=162)['owner']=='new'
