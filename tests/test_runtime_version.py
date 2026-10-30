from backend import runtime_version


def test_running_source_detects_edits_and_new_modules(tmp_path, monkeypatch):
    module = tmp_path/'strategy.py'
    module.write_text('original')
    monkeypatch.setattr(runtime_version, 'DIRECTORY', tmp_path)
    loaded = runtime_version.source_fingerprint(tmp_path)
    monkeypatch.setattr(runtime_version, 'LOADED_SOURCE', loaded)
    assert not runtime_version.runtime_status()['restart_required']
    module.write_text('profit policy changed')
    assert runtime_version.runtime_status() == {'loaded_source': loaded, 'restart_required': True}
    module.write_text('original')
    (tmp_path/'new_policy.py').write_text('new')
    assert runtime_version.runtime_status()['restart_required']
