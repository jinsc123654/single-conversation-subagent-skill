"""Project-local Obsidian profile; no global registration, launching, or SDK imports."""
import hashlib
import json
import os
import time
from urllib.parse import quote, urlencode

from .errors import ScsError


def layout(paths, vault):
    vault = "" if vault in {"", "."} else paths.validate(vault)
    vault_id = hashlib.sha256(str(paths.root / vault).encode()).hexdigest()[:16]
    profile = '.scs-obsidian/' + vault_id
    return vault, vault_id, profile


def profile_info(paths, vault):
    vault, vault_id, profile = layout(paths, vault)
    marker = profile + '/.scs-profile.json'
    raw = paths.read_optional(marker)
    expected = {'schema':1, 'project_root':str(paths.root), 'vault_root':vault, 'vault_id':vault_id}
    if raw is not None:
        try:
            if json.loads(raw) != expected:
                raise ValueError()
            registry = json.loads(paths.read(profile + '/obsidian.json'))
            if registry['vaults'][vault_id]['path'] != str(paths.root / vault):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ScsError('PROFILE_CONFLICT', 'Dedicated Obsidian profile changed or belongs to another vault; preserved for review.') from None
    return {'prepared':raw is not None,'vault_id':vault_id,'profile_root':str(paths.root / profile),
            'registry_path':profile + '/obsidian.json', 'launch_flag':'--user-data-dir=' + str(paths.root / profile),
            'global_registry_modified':False,'application_opened':False}


def prepare_profile(paths, vault):
    vault, vault_id, profile = layout(paths, vault)
    # Check ownership before creating or changing anything.
    marker = profile + '/.scs-profile.json'
    expected = {'schema':1, 'project_root':str(paths.root), 'vault_root':vault, 'vault_id':vault_id}
    raw = paths.read_optional(marker)
    if raw is None:
        with paths.directory(paths.parts(profile), missing_ok=True) as fd:
            if fd is not None and os.listdir(fd):
                raise ScsError('PROFILE_CONFLICT', 'Nonempty profile has no ownership marker; existing files were preserved.')
    else:
        try:
            if json.loads(raw) != expected:
                raise ValueError()
        except (ValueError,TypeError):
            raise ScsError('PROFILE_CONFLICT', 'Profile ownership changed; existing files were preserved.') from None
    registry_path = profile + '/obsidian.json'
    registry_raw = paths.read_optional(registry_path)
    if registry_raw is not None:
        # Preserve preferences/windows maintained by Obsidian itself.
        profile_info(paths, vault)
    with paths.directory(paths.parts(vault) if vault else (), create=True):
        pass
    with paths.directory(paths.parts((vault + '/.obsidian').lstrip('/')), missing_ok=True) as fd:
        configured = fd is not None
    if not configured:
        paths.create_once((vault + '/.obsidian/app.json').lstrip('/'),'{}\n')
    paths.create_once(marker,json.dumps(expected,ensure_ascii=False,sort_keys=True)+'\n')
    if registry_raw is None:
        registry={'vaults':{vault_id:{'path':str(paths.root / vault),'ts':int(time.time()*1000),'open':True}}}
        paths.create_once(registry_path,json.dumps(registry,ensure_ascii=False,sort_keys=True)+'\n')
    return profile_info(paths, vault)


def profile_uri(profile, relative_note=None):
    values={'vault':profile['vault_id']}
    if relative_note:
        values['file']=relative_note
    return 'obsidian://open?' + urlencode(values,quote_via=quote)
