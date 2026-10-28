"""Bounded public image discovery; root daemon inventory supplies installed identity."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from docker_image_updates import DIGEST, Registry, reference, inventory_local
from infrastructure_update_policy import classify_version

TAG_VERSION = re.compile(r'v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)([-_.][A-Za-z0-9_.-]+)?\Z')
UNSTABLE = re.compile(r'(?:^|[-_.])(?:rc|alpha|beta|dev|nightly|preview|canary)(?:[0-9_.-]|$)', re.I)
DELEGATED = {'homecompute-automation-aula-mcp-1', 'homecompute-automation-tilbudstrolden-mcp-1'}
MAX_TAGS = 12000


def version_tag(tag):
    if not isinstance(tag, str) or UNSTABLE.search(tag):
        return None
    match = TAG_VERSION.fullmatch(tag)
    if not match:
        return None
    numbers = tuple(int(match[i]) for i in (1, 2, 3))
    return {'version': '.'.join(map(str, numbers)), 'numbers': numbers, 'suffix': match[4] or '', 'tag': tag}


def choose_tags(installed, tags):
    old = version_tag(installed)
    if old is None:
        raise ValueError('installed_version_unproven')
    if not isinstance(tags, list) or len(tags) > MAX_TAGS:
        raise ValueError('image_tag_inventory_incomplete')
    candidates = [value for tag in tags if (value := version_tag(tag)) is not None
                  and value['suffix'] == old['suffix'] and value['numbers'] > old['numbers']]
    if not candidates:
        return old, None, None
    candidates.sort(key=lambda value: (value['numbers'], value['tag']))
    routine = [value for value in candidates
               if classify_version(old['version'], value['version'], 'semver')['classification'] == 'routine']
    majors = [value for value in candidates
              if classify_version(old['version'], value['version'], 'semver')['classification'] == 'major']
    return old, routine[-1] if routine else None, majors[-1] if majors else None


def image_tags(registry, ref):
    """Enumerate completely or refuse; anonymous pull tokens stay inside Registry."""
    target = ref['digest'] or ref['tag'] or 'latest'
    registry.manifest(ref['registry'], ref['repository'], target)
    from docker_image_updates import REGISTRIES
    host = REGISTRIES[ref['registry']][0]
    prefix = '/v2/' + ref['repository'] + '/tags/list'
    path, tags, seen = prefix + '?n=1000', [], set()
    for _ in range(12):
        if path in seen:
            raise ValueError('image_tag_inventory_incomplete')
        seen.add(path)
        token = registry.tokens.get((ref['registry'], ref['repository']))
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        status, fields, raw = registry.request(host, path, headers, 1048576)
        value = json.loads(raw)
        if (status != 200 or not isinstance(value, dict) or value.get('name') != ref['repository']
                or not isinstance(value.get('tags'), list)
                or any(not isinstance(tag, str) or len(tag) > 128 for tag in value['tags'])):
            raise ValueError('image_tag_inventory_unavailable')
        tags.extend(value['tags'])
        if len(tags) > MAX_TAGS:
            raise ValueError('image_tag_inventory_incomplete')
        link = fields.get('link')
        if not link:
            return sorted(set(tags))
        match = re.fullmatch(r'<([^<>]+)>;\s*rel="?next"?', link)
        if not match:
            raise ValueError('image_tag_inventory_incomplete')
        parsed = urlsplit(match[1])
        if (parsed.scheme not in {'', 'https'} or parsed.netloc not in {'', host}
                or parsed.path != prefix or parsed.fragment or parsed.username or parsed.password):
            raise ValueError('image_tag_inventory_incomplete')
        path = parsed.path + ('?' + parsed.query if parsed.query else '')
    raise ValueError('image_tag_inventory_incomplete')


def discover(host, tracking, *, inventory=None, registry_factory=Registry):
    """Every actual container gets a result, including dormant/custom/unproven images."""
    if host not in {'home-core', 'home-spark'}:
        raise ValueError('fixed maintenance host required')
    if (not isinstance(tracking, dict) or tracking.get('schema_version') != 1
            or not isinstance(tracking.get('containers'), dict)):
        raise ValueError('reviewed image tracking policy required')
    inventory = inventory if inventory is not None else inventory_local()
    rows = inventory.get('containers') if isinstance(inventory, dict) else None
    if not isinstance(rows, list) or len(rows) > 128:
        raise ValueError('bounded Docker inventory required')
    result, cache = [], {}
    for row in rows:
        name = row.get('container')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', name):
            raise ValueError('invalid Docker inventory identity')
        item = {'component': name, 'kind': 'docker', 'status': 'review', 'reason': 'image_version_unproven'}
        if re.search(r'-before-[a-f0-9]{12}$', name):
            result.append(dict(item, status='delegated', reason='retained_rollback_container'))
            continue
        if host == 'home-core' and name in DELEGATED:
            result.append(dict(item, status='delegated', reason='existing_mcp_controller'))
            continue
        try:
            rule = tracking['containers'].get(name)
            if not isinstance(rule, dict) or rule.get('source_lane') not in {'registry', 'source_pin'}:
                raise ValueError('image_tracking_unavailable')
            if rule['source_lane'] == 'source_pin':
                raise ValueError('source_pin_requires_review')
            ref = reference(row.get('image'))
            installed = version_tag(ref['tag'])
            if installed is None:
                raise ValueError('image_version_unproven')
            if not row.get('repo_digests') or row.get('os') != 'linux':
                raise ValueError('image_provenance_unavailable')
            key = (ref['registry'], ref['repository'])
            if key not in cache:
                registry = registry_factory(seconds=75)
                cache[key] = (registry, image_tags(registry, ref))
            registry, tags = cache[key]
            # A readable tag is not proof of the deployed version. Check both
            # daemon provenance and immutable image identity before claiming
            # current or proposing any replacement. Moved tags stay in review.
            proof = registry.platform(ref, ref['tag'], row['os'], row['architecture'], row.get('variant', ''))
            digests = row['repo_digests']
            if not isinstance(digests, list) or len(digests) > 64:
                raise ValueError('image_provenance_unavailable')
            known = set()
            for value in digests:
                parsed = reference(value)
                if (parsed['registry'], parsed['repository']) == key and parsed['digest']:
                    known.add(parsed['digest'])
            proven = set(proof.values())
            if (not known.intersection(proven) or row.get('image_id') not in proven
                    or ref['digest'] and (ref['digest'] not in known or ref['digest'] not in proven)):
                raise ValueError('image_provenance_unavailable')
            old, routine, major = choose_tags(ref['tag'], tags)
            candidate = routine or major
            item['installed'] = old['version']
            if not candidate:
                item.update(status='current', reason='no_newer_stable_tag')
            else:
                manifest = registry.platform(ref, candidate['tag'], row['os'], row['architecture'], row.get('variant', ''))
                candidate_ref = ref['registry'] + '/' + ref['repository'] + ':' + candidate['tag'] + '@' + manifest['index_digest']
                item.update(status='available', reason='stable_registry_candidate', candidate=candidate['version'],
                            candidate_ref=candidate_ref, evidence={'installed_tag': ref['tag'], 'candidate_tag': candidate['tag']})
                if major and routine:
                    item['major_candidate'] = major['version']
        except Exception as error:
            # Registry errors and local images are explicit gaps, never "current".
            reason = str(error)
            item['reason'] = reason if reason in {'image_version_unproven', 'image_provenance_unavailable',
                'image_tag_inventory_unavailable', 'image_tag_inventory_incomplete',
                'image_tracking_unavailable', 'source_pin_requires_review'} else 'image_source_unavailable'
        result.append(item)
    return result


def prepare(controller, container, output_path):
    """Finite assistant selection; only fresh root discovery chooses refs and versions."""
    import docker_maintenance
    from pathlib import Path
    from infrastructure_maintenance import ENV, root_json
    host = controller.local_target
    policy = root_json(Path('/opt/homecompute/host-maintenance/docker-image-tracking.json'), 0)['hosts'][host]
    matches = [item for item in discover(host, policy) if item['component'] == container]
    if len(matches) != 1 or matches[0]['status'] != 'available':
        raise ValueError('no qualified image candidate; inspect host update coverage')
    item = matches[0]
    docker = '/run/current-system/sw/bin/docker' if host == 'home-core' else '/usr/bin/docker'
    pulled = controller.runner([docker, 'pull', item['candidate_ref']], timeout=300, env=ENV.copy(), cwd='/', shell=False)
    if pulled.returncode:
        raise ValueError('image candidate staging failed')
    return docker_maintenance.prepare(controller, container, item['candidate_ref'], item['candidate'], item['installed'],
                                      output_path, evidence=item['evidence'])
