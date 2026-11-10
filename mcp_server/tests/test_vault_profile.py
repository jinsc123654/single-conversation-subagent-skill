import json
from pathlib import Path
from urllib.parse import parse_qs,urlparse

import pytest
from scs_mcp.errors import ScsError
from scs_mcp.paths import Paths
from scs_mcp.vault_profile import layout, prepare_profile, profile_info, profile_uri


def test_info_readonly_and_prepare_without_database(tmp_path):
    (tmp_path/'docs').mkdir()
    paths=Paths(tmp_path)
    assert not profile_info(paths,'docs')['prepared']
    assert list(tmp_path.iterdir())==[tmp_path/'docs']
    result=prepare_profile(paths,'docs')
    registry=json.loads((tmp_path/result['registry_path']).read_text())
    assert registry['vaults'][result['vault_id']]['path']==str(tmp_path/'docs')
    assert not (tmp_path/'.scs').exists()
    assert not result['global_registry_modified']
    assert not result['application_opened']


def test_preserves_existing_settings_and_profile_extensions(tmp_path):
    (tmp_path/'docs/.obsidian').mkdir(parents=True)
    settings=tmp_path/'docs/.obsidian/app.json';settings.write_text('{"user":true}')
    paths=Paths(tmp_path);one=prepare_profile(paths,'docs')
    registry=tmp_path/one['registry_path'];value=json.loads(registry.read_text());value['preference']='keep'
    registry.write_text(json.dumps(value));before=registry.read_bytes()
    two=prepare_profile(paths,'docs')
    assert one==two and settings.read_text()=='{"user":true}'
    assert registry.read_bytes()==before


def test_note_encoding_and_stable_vault_ids(tmp_path):
    paths=Paths(tmp_path)
    assert layout(paths,'docs')[1]!=layout(paths,'other/docs')[1]
    values=parse_qs(urlparse(profile_uri(profile_info(paths,'docs'),'a #?中文.md')).query)
    assert values['file']==['a #?中文.md'] and 'vault' in values


@pytest.mark.parametrize('obstruction',['unowned','marker','registry','symlink-profile','symlink-config'])
def test_conflicts_preserved(tmp_path,obstruction):
    (tmp_path/'docs').mkdir();paths=Paths(tmp_path)
    _,_,name=layout(paths,'docs');folder=tmp_path/name
    if obstruction=='unowned':
        folder.mkdir(parents=True);(folder/'keep.txt').write_text('untouched')
    elif obstruction=='symlink-profile':
        folder.parent.mkdir();folder.symlink_to(tmp_path/'docs',target_is_directory=True)
    elif obstruction=='symlink-config':
        (tmp_path/'docs/.obsidian').symlink_to(tmp_path,target_is_directory=True)
    else:
        result=prepare_profile(paths,'docs')
        if obstruction=='marker':
            (folder/'.scs-profile.json').write_text('{}')
        else:
            registry=folder/'obsidian.json';value=json.loads(registry.read_text())
            value['vaults'][result['vault_id']]['path']='/foreign'
            registry.write_text(json.dumps(value))
    before={p:p.read_bytes() for p in folder.glob('*') if p.is_file() and not p.is_symlink()}
    with pytest.raises(ScsError):prepare_profile(paths,'docs')
    assert all(p.read_bytes()==raw for p,raw in before.items())


def test_interrupted_marker_only_can_resume(tmp_path):
    (tmp_path/'docs').mkdir();paths=Paths(tmp_path);out=prepare_profile(paths,'docs')
    (tmp_path/out['registry_path']).unlink()
    assert prepare_profile(paths,'docs')['prepared']


def test_project_root_can_itself_be_a_vault(tmp_path):
    paths=Paths(tmp_path);result=prepare_profile(paths,'.')
    assert result['prepared']
    assert (tmp_path/'.obsidian/app.json').read_text()=='{}\n'
    registry=json.loads((tmp_path/result['registry_path']).read_text())
    assert registry['vaults'][result['vault_id']]['path']==str(tmp_path)
