from __future__ import annotations
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'docs'


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = set()
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        for key in ('href','src'):
            if attrs.get(key):
                self.links.append(attrs[key])


def test_website_internal_links_and_fragments():
    errors = []
    for file in SITE.rglob('*.html'):
        doc = Links(); doc.feed(file.read_text())
        for value in doc.links:
            url = urlsplit(value)
            if url.scheme or url.netloc:
                continue
            target = ((SITE / unquote(url.path).lstrip('/')) if url.path.startswith('/') else file.parent / unquote(url.path)) if url.path else file
            if target.is_dir():
                target /= 'index.html'
            if not target.exists():
                errors.append(f'{file.relative_to(SITE)} -> {value}')
            elif url.fragment and target.suffix == '.html':
                linked = Links(); linked.feed(target.read_text())
                if unquote(url.fragment) not in linked.ids:
                    errors.append(f'{file.relative_to(SITE)} -> missing fragment {value}')
    assert not errors, '\n'.join(errors)


def test_generated_docs_match_source():
    import runpy
    generator = runpy.run_path(str(ROOT/'scripts/sync_docs.py'))
    for file, content in generator['outputs']().items():
        assert file.read_text() == content, f'Regenerate {file}'


def test_vendored_runtime_assets_and_license_checksums():
    directory = ROOT / 'app/static/vendor'
    manifest = json.loads((directory/'SHA256SUMS.json').read_text())
    assert {'bootstrap.min.css','bootstrap.bundle.min.js','fullcalendar.min.js','bootstrap-LICENSE','fullcalendar-LICENSE.md'} <= manifest.keys()
    for name, digest in manifest.items():
        assert hashlib.sha256((directory/name).read_bytes()).hexdigest() == digest


def test_template_static_asset_references_exist():
    for file in (ROOT/'app/templates').glob('*.html'):
        for relative in re.findall(r'(?:src|href)=[\'"](/static/[^\'"?{}]+)',file.read_text()):
            assert (ROOT/'app'/relative.lstrip('/')).is_file(), f'{file}: {relative}'
    base = (ROOT/'app/templates/base.html').read_text()
    calendar = (ROOT/'app/templates/calendar.html').read_text()
    assert 'cdn.jsdelivr.net' not in base + calendar
    assert 'index.global.min.css' not in calendar


def test_removed_aliases_are_unreferenced():
    removed = json.loads((ROOT/'scripts/removed-files.json').read_text())['paths']
    sources = [p.read_text() for p in (ROOT/'app').rglob('*') if p.is_file() and p.suffix in {'.py','.html','.js','.json','.webmanifest'} and 'vendor' not in p.parts]
    for name in removed:
        assert not (ROOT/name).exists()
        url = '/' + name.removeprefix('app/')
        # Match a complete path, not /favicon.ico inside /static/favicon.ico.
        reference = re.compile(r'(?<![A-Za-z0-9_./-])' + re.escape(url) + r'(?![A-Za-z0-9_./-])')
        assert all(not reference.search(source) for source in sources), name
