#!/usr/bin/env python3
"""Preserve GitHub branch/tag histories on GitLab without checking out source code."""
import concurrent.futures
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

OWNER = 'openresearchtools'
NAMESPACE_ID = 142942734
EXCLUDED = {'apt', 'wildbuzzard', 'bashkitten', 'wildbuzzard-android', 'termux-suite', 'gitlab-mirror', 'github-mirror-sync'}
CONTROLLER = 'gitlab-mirror'
CONFIG = Path(__file__).with_name('mirrors.json')
GL_API = 'https://gitlab.com/api/v4'
GH_API = 'https://api.github.com'
TOKEN = os.environ.get('MIRROR_GITLAB_TOKEN', '')
MARKER = 'Read-only mirror of '
REFSPECS = ['+refs/heads/*:refs/heads/*', '+refs/tags/*:refs/tags/*']


def api(base, path, method='GET', body=None):
    if base not in (GL_API, GH_API) or path.startswith('/') or '..' in path:
        raise ValueError('Unexpected API destination')
    headers = {'Accept': 'application/json', 'User-Agent': 'openresearchtools-gitlab-mirror'}
    if base == GL_API:
        headers['PRIVATE-TOKEN'] = TOKEN
    elif os.environ.get('GITHUB_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GITHUB_TOKEN']
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        headers['Content-Type'] = 'application/json'
    for attempt in range(3):
        try:
            with urllib.request.build_opener(NoRedirect()).open(urllib.request.Request(base + '/' + path, data=data, headers=headers, method=method), timeout=45) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503, 504) and attempt < 2 and method in ('GET', 'PUT'):
                time.sleep(min(int(error.headers.get('Retry-After', '5')), 30))
                continue
            detail = error.read().decode()[:500].replace(TOKEN, '[MASKED]') if TOKEN else error.read().decode()[:500]
            raise RuntimeError(f'{method} {path}: HTTP {error.code}: {detail}') from None
    raise RuntimeError('API retries exhausted')


def pages(base, path):
    result = []
    separator = '&' if '?' in path else '?'
    for page in range(1, 101):
        batch = api(base, f'{path}{separator}per_page=100&page={page}')
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise RuntimeError('Pagination guard reached')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('API redirect refused; credentials remain on the expected host')


def git(*args, cwd=None, timeout=300):
    # No shell, checkout, hooks, submodules, LFS filters, or repository-provided scripts.
    command = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'http.followRedirects=false',
               '-c', 'credential.helper=', *args]
    for attempt in range(3):
        proc = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout)
        if proc.returncode == 0:
            return proc.stdout
        message = proc.stderr[-2400:]
        for secret in (TOKEN, os.environ.get('GITHUB_TOKEN', '')):
            if secret:
                message = message.replace(secret, '[MASKED]')
        if attempt < 2 and re.search(r'(error: (429|500|502|503|504)|Too Many Requests|timed out|Connection reset)', message, re.I):
            time.sleep((5, 15)[attempt])
            continue
        raise RuntimeError(f'git {args[0]} failed: {message}')


def refs(url):
    result = {}
    for line in git('ls-remote', '--refs', '--heads', '--tags', url, timeout=60).splitlines():
        sha, ref = line.split('\t', 1)
        if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', sha):
            raise RuntimeError('Invalid remote object ID')
        result[ref] = sha
    return result


def local_refs(directory):
    return {ref: sha for sha, ref in (line.split(' ', 1) for line in git('for-each-ref', '--format=%(objectname) %(refname)', 'refs/heads', 'refs/tags', cwd=directory).splitlines())}


def description(repo):
    return (MARKER + repo['html_url'] + '. ' + (repo.get('description') or '')).rstrip()[:2000]


def source_repositories():
    repos = pages(GH_API, f'users/{OWNER}/repos?type=owner')
    repos = [r for r in repos if r['owner']['login'].lower() == OWNER.lower() and r['name'].lower() not in EXCLUDED]
    if not repos:
        raise RuntimeError('GitHub returned no eligible repositories; refusing changes')
    for repo in repos:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', repo['name']):
            raise RuntimeError('Unsafe repository name')
    return sorted(repos, key=lambda r: r['name'].lower())


def validate_pair(entry, repo, project):
    name = entry['name']
    if name.lower() in EXCLUDED or not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
        raise RuntimeError('Excluded or invalid repository in allowlist')
    if repo['id'] != entry['github_id'] or repo['owner']['id'] != 229047507:
        raise RuntimeError('GitHub repository identity changed; refusing transfer')
    if repo['full_name'] != f'{OWNER}/{name}' or repo.get('private'):
        raise RuntimeError('Source renamed or no longer public; retaining existing mirror')
    if project['id'] != entry['gitlab_id'] or project['namespace']['id'] != NAMESPACE_ID:
        raise RuntimeError('GitLab repository identity changed; refusing transfer')
    if project['path_with_namespace'] != f'{OWNER}/{name}':
        raise RuntimeError('GitLab mirror path changed; refusing transfer')
    if not (project.get('description') or '').startswith(MARKER + repo['html_url'] + '.'):
        raise RuntimeError('Destination is not a managed mirror; refusing overwrite')


def ensure_settings(repo, project):
    expected = dict(description=description(repo), visibility='private' if repo['private'] else 'public',
                    builds_access_level='disabled', auto_devops_enabled=False, shared_runners_enabled=False, lfs_enabled=False)
    changes = {key: value for key, value in expected.items() if project.get(key) != value}
    if changes:
        project = api(GL_API, f"projects/{project['id']}", 'PUT', changes)
    # Do not silently weaken branch protection if the owner changes it later.
    if pages(GL_API, f"projects/{project['id']}/protected_branches"):
        raise RuntimeError('Mirror branch protection changed; owner review required')
    return project


def compare(repo, project):
    name = repo['name']
    source = f'https://github.com/{OWNER}/{name}.git'
    target = f'https://gitlab.com/{OWNER}/{name}.git'
    source_refs, target_refs = refs(source), refs(target)
    return repo, project, source, target, source_refs, target_refs


def sync_repo(repo, project, source, target, source_refs, target_refs):
    default = repo.get('default_branch')
    default_ref = f'refs/heads/{default}'
    if source_refs and default_ref not in source_refs:
        raise RuntimeError('Upstream default branch changed during inventory; retry next run')
    changed = source_refs != target_refs
    if changed and project.get('import_status') in ('scheduled', 'started'):
        raise RuntimeError('Initial import is still running and references differ; retry after import')
    if changed:
        with tempfile.TemporaryDirectory(prefix='gitlab-mirror-') as directory:
            git('init', '--bare', '--initial-branch=' + (default or 'main'), directory)
            git('remote', 'add', 'source', source, cwd=directory)
            git('remote', 'add', 'target', target, cwd=directory)
            git('fetch', '--force', '--prune', '--no-tags', 'source', *REFSPECS, cwd=directory)
            desired = local_refs(directory)
            # Never prune based on a partial or concurrently changing source snapshot.
            if desired != refs(source):
                raise RuntimeError('Upstream changed during fetch; postponing this repository')
            if source_refs and not desired:
                raise RuntimeError('Unexpected empty fetch; refusing to prune')
            if desired and default_ref not in desired:
                raise RuntimeError('Default branch changed during fetch; postponing')
            # Add the new default branch first, switch HEAD, then prune an old default.
            if desired and project.get('default_branch') != default:
                git('push', 'target', f'+{default_ref}:{default_ref}', cwd=directory)
                api(GL_API, f"projects/{project['id']}", 'PUT', {'default_branch': default})
            if not desired and target_refs:
                raise RuntimeError('Upstream is empty but destination has history; refusing automatic repository erasure')
            if desired:
                git('push', '--atomic', '--prune', 'target', *REFSPECS, cwd=directory)
            if refs(target) != desired:
                raise RuntimeError('Post-push branch/tag verification failed')
            source_refs = desired
    elif source_refs and project.get('default_branch') != default:
        api(GL_API, f"projects/{project['id']}", 'PUT', {'default_branch': default})
    return {'repository': repo['name'], 'status': 'updated' if changed else 'unchanged',
            'branches': sum(r.startswith('refs/heads/') for r in source_refs),
            'tags': sum(r.startswith('refs/tags/') for r in source_refs)}


def main():
    if not TOKEN:
        raise RuntimeError('MIRROR_GITLAB_TOKEN CI variable is required')
    os.environ['GIT_TERMINAL_PROMPT'] = '0'
    results = []
    sources = source_repositories()
    config = json.loads(CONFIG.read_text())
    if config['github_owner'] != OWNER or config['github_owner_id'] != 229047507 or config['gitlab_namespace_id'] != NAMESPACE_ID:
        raise RuntimeError('Unexpected owner/namespace in allowlist')
    entries = config['mirrors']
    if len({e['github_id'] for e in entries}) != len(entries) or len({e['gitlab_id'] for e in entries}) != len(entries):
        raise RuntimeError('Duplicate repository IDs in allowlist')
    by_id = {r['id']: r for r in sources}
    pairs = []
    for entry in entries:
        try:
            repo = by_id.get(entry['github_id'])
            if repo is None:
                raise RuntimeError('Source missing or no longer public; mirror retained')
            project = api(GL_API, f"projects/{entry['gitlab_id']}")
            validate_pair(entry, repo, project)
            pairs.append((repo, ensure_settings(repo, project)))
        except Exception as error:
            results.append({'repository': entry['name'], 'status': 'error', 'error': str(error)})
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        checks = {pool.submit(compare, *pair): pair[0]['name'] for pair in pairs}
        ready = []
        for check in concurrent.futures.as_completed(checks):
            try:
                ready.append(check.result())
            except Exception as error:
                results.append({'repository': checks[check], 'status': 'error', 'error': str(error)})
    # Two changed repositories at once balances runner memory and elapsed compute time.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        syncs = {pool.submit(sync_repo, *item): item[0]['name'] for item in ready}
        for future in concurrent.futures.as_completed(syncs):
            try:
                result = future.result()
            except Exception as error:
                result = {'repository': syncs[future], 'status': 'error', 'error': str(error)}
            results.append(result)
            print(json.dumps(result), flush=True)
    report = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'excluded': sorted(EXCLUDED), 'results': sorted(results, key=lambda r: r['repository'].lower())}
    Path('mirror-report.json').write_text(json.dumps(report, indent=2) + '\n')
    for result in results:
        if result['status'] == 'error':
            print(json.dumps(result), flush=True)
    errors = sum(r['status'] == 'error' for r in results)
    changed = sum(r['status'] == 'updated' for r in results)
    print(f'{len(results)} repositories: {changed} updated, {len(results)-errors-changed} unchanged, {errors} errors', flush=True)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f'checked={len(results)}\nupdated={changed}\nerrors={errors}\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write(f'## GitLab mirrors\n\n{len(results)} checked, {changed} updated, {errors} errors.\n\n')
            for result in sorted(results, key=lambda r: r['repository'].lower()):
                summary.write(f"- {result['repository']}: {result['status']}\n")
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
