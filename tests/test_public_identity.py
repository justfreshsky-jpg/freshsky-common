import pytest
from flask import Flask

from freshsky_common import freemium
from freshsky_common.legal import register_legal_routes
from freshsky_common.public_identity import public_origin
from freshsky_common.security import install_security_headers


def test_origin_defaults_canonical_and_legacy_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv('FRESHSKY_PUBLIC_ORIGIN', raising=False)
    monkeypatch.delenv('FRESHSKY_LEGACY_DOMAIN_COMPATIBILITY', raising=False)
    assert public_origin() == 'https://freshskyllc.org'
    for value in ['https://www.freshskyai.com', 'https://freshskyllc.org.evil.test',
                  'http://freshskyllc.org', 'https://freshskyllc.org/path',
                  'https://freshskyllc.org@evil.test']:
        monkeypatch.setenv('FRESHSKY_PUBLIC_ORIGIN', value)
        with pytest.raises(ValueError):
            public_origin()
    monkeypatch.setenv('FRESHSKY_PUBLIC_ORIGIN', 'https://www.freshskyai.com')
    monkeypatch.setenv('FRESHSKY_LEGACY_DOMAIN_COMPATIBILITY', 'true')
    assert public_origin() == 'https://www.freshskyai.com'


@pytest.mark.parametrize('legacy', [False, True])
def test_planner_and_csp_use_the_same_validated_origin(monkeypatch, legacy):
    if legacy:
        monkeypatch.setenv('FRESHSKY_PUBLIC_ORIGIN', 'https://www.freshskyai.com')
        monkeypatch.setenv('FRESHSKY_LEGACY_DOMAIN_COMPATIBILITY', 'true')
    app = Flask(__name__)
    app.add_url_rule('/', 'index', lambda: '<html><body>Ready</body></html>')
    install_security_headers(app)
    response = app.test_client().get('/')
    origin = public_origin()
    assert f'src="{origin}/static/planner.js"' in response.text
    assert origin in response.headers['Content-Security-Policy']
    absent = 'https://freshskyllc.org' if legacy else 'https://www.freshskyai.com'
    assert absent not in response.text
    assert absent not in response.headers['Content-Security-Policy']


@pytest.mark.parametrize('host', ['freshskyllc.org', 'www.freshskyllc.org', 'www.freshskyai.com'])
def test_unconfigured_hub_billing_does_not_redirect_to_itself(host):
    app = Flask(__name__)
    app.secret_key = 'test'
    app.add_url_rule('/', 'index', lambda: 'Ready')
    freemium.register_freemium(app, primary_url=f'https://{host}')
    response = app.test_client().get('/billing', base_url=f'https://{host}')
    assert response.location == '/'


def test_legacy_access_reference_is_upgraded_and_new_bundle_targets_org():
    app = Flask(__name__)
    app.secret_key = 'test'
    app.add_url_rule('/', 'index', lambda: '<body><script src="/freshsky-access-v062.js"></script></body>')
    freemium.register_freemium(app, primary_url='https://freshskyllc.org')
    client = app.test_client()
    page = client.get('/').text
    assert page.count('/freshsky-access-v062.js?version=071') == 1
    assert 'src="/freshsky-access-v062.js"' not in page
    bundle = client.get('/freshsky-access-v071.js')
    assert 'https://freshskyllc.org/' in bundle.text
    assert 'https://www.freshskyai.com/' not in bundle.text
    assert client.get('/freshsky-access-v062.js').status_code == 200


def test_legal_routes_serve_on_org_and_redirect_exact_legacy_subapps():
    app = Flask(__name__)
    register_legal_routes(app, brand='Fresh Sky', primary_url='https://freshskyllc.org')
    assert app.test_client().get('/privacy').status_code == 200
    subapp = Flask('subapp')
    register_legal_routes(subapp, brand='Tool', primary_url='https://forms.freshskyai.com')
    assert subapp.test_client().get('/privacy').location == 'https://freshskyllc.org/privacy'


def test_explicit_hold_selects_public_links_by_exact_request_host(monkeypatch):
    monkeypatch.setenv('FRESHSKY_PUBLIC_ORIGIN', 'https://www.freshskyai.com')
    monkeypatch.setenv('FRESHSKY_LEGACY_DOMAIN_COMPATIBILITY', 'true')
    app = Flask('cutover')
    app.secret_key = 'test'
    app.add_url_rule('/', 'index', lambda: '<body>Ready</body>')
    install_security_headers(app)
    register_legal_routes(app, brand='Fresh Sky', primary_url='https://freshskyllc.org')
    freemium.register_freemium(app, primary_url='https://freshskyllc.org')
    client = app.test_client()
    assert public_origin() == 'https://www.freshskyai.com'  # background work
    hosts = ['freshskyllc.org', 'www.freshskyllc.org', 'www.freshskyai.com',
             'candidate.service.run.app', 'freshskyllc.org.evil.test',
             'evil-freshskyllc.org', 'freshskyllc.org:8443']
    for host in hosts:
        canonical = host in {'freshskyllc.org', 'www.freshskyllc.org'}
        origin = 'https://freshskyllc.org' if canonical else 'https://www.freshskyai.com'
        headers = {'X-Forwarded-Host': 'freshskyllc.org'}
        page = client.get('/', base_url=f'https://{host}', headers=headers)
        assert f'src="{origin}/static/planner.js"' in page.text
        assert origin in page.headers['Content-Security-Policy']
        legal = client.get('/privacy', base_url=f'https://{host}', headers=headers)
        assert f'href="{origin}/#products"' in legal.text
        bundle = client.get('/freshsky-access-v071.js', base_url=f'https://{host}', headers=headers)
        assert f'href="{origin}/"' in bundle.text
        assert bundle.headers["Cache-Control"] == "no-store, max-age=0"
