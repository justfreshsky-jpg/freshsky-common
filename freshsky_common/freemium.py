"""Drop-in access, authentication, and subscription helpers for Fresh Sky apps.

Single call to ``register_freemium(app, ...)`` adds:

* ``/auth/google`` + ``/auth/google/callback`` — OAuth login with optional
  ``?next=`` round-trip.
* ``/logout`` — clears session.
* ``/subscribe`` — optional monthly Stripe Checkout (disabled by default).
* ``/subscribe/yearly`` — intentionally redirects to monthly pricing.
* ``/billing`` — Stripe Customer Billing Portal for recurring supporters and
  historical subscribers.
* ``/api/user-status`` — JSON endpoint the frontend hits to render the
  user bar (logged-in state and full free access).

Usage in app.py::

    from freshsky_common.freemium import register_freemium

    check = register_freemium(
        app,
        google_client_id=os.environ['GOOGLE_CLIENT_ID'],
        google_client_secret=os.environ['GOOGLE_CLIENT_SECRET'],
        stripe_secret_key=os.environ.get('STRIPE_SECRET_KEY', ''),
        stripe_webhook_secret=os.environ.get('STRIPE_WEBHOOK_SECRET', ''),
        primary_url='https://foia.freshskyai.com/',
    )

    @app.route('/api/whatever', methods=['POST'])
    def whatever():
        gate = check()
        if gate is not None:
            return gate
        ...

The default remains unrestricted access. A service must explicitly provide a
monthly Stripe Price ID *and* enable subscriptions before the gate can charge.
Provider safety controls and platform cost controls remain separate.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Callable, Optional
from urllib.parse import urlencode, urlparse

from flask import (
    Flask, Response, jsonify, redirect, request, session, url_for,
)


logger = logging.getLogger(__name__)


def register_freemium(
    app: Flask,
    *,
    google_client_id: str = '',
    google_client_secret: str = '',
    stripe_secret_key: str = '',
    stripe_webhook_secret: str = '',
    primary_url: str = '',
    community_mode: bool = False,
    enable_email_capture: bool = False,
    expose_provider_metrics: bool = False,
    subscriptions_enabled: bool = False,
    subscription_tier: str = '',
    subscription_price_id: str = '',
    subscription_amount_cents: int = 0,
    free_request_limit: Optional[int] = None,
) -> Callable[[], Optional[Response]]:
    """Wire free-access routes onto ``app`` and return the gate function.

    Returns
    -------
    check : callable -> None | (Response, int)
        Call at the top of each gated endpoint. Returns ``None`` if the
        request is allowed to proceed. The return type remains compatible
        with older call sites that checked for a possible response. Use::

            gate = check()
            if gate is not None:
                return gate
    """
    from .brand import install_brand_assets

    install_brand_assets(app)
    google_client_id = google_client_id or os.environ.get('GOOGLE_CLIENT_ID', '')
    google_client_secret = google_client_secret or os.environ.get('GOOGLE_CLIENT_SECRET', '')
    stripe_secret_key = stripe_secret_key or os.environ.get('STRIPE_SECRET_KEY', '')
    stripe_webhook_secret = (
        stripe_webhook_secret or os.environ.get('STRIPE_WEBHOOK_SECRET', '')
    )
    google_auth_enabled = bool(google_client_id and google_client_secret)
    stripe_enabled = bool(stripe_secret_key)
    env_enabled = os.environ.get('FRESHSKY_SUBSCRIPTIONS_ENABLED', '').lower()
    subscriptions_enabled = subscriptions_enabled or env_enabled in {'1', 'true', 'yes'}
    subscription_tier = (
        subscription_tier or os.environ.get('FRESHSKY_SUBSCRIPTION_TIER', '')
    ).strip().lower()
    subscription_price_id = (
        subscription_price_id or os.environ.get('FRESHSKY_SUBSCRIPTION_PRICE_ID', '')
    ).strip()
    if not subscription_amount_cents:
        try:
            subscription_amount_cents = int(
                os.environ.get('FRESHSKY_SUBSCRIPTION_AMOUNT_CENTS', '0')
            )
        except ValueError:
            subscription_amount_cents = 0
    if free_request_limit is None and os.environ.get('FRESHSKY_FREE_REQUEST_LIMIT'):
        try:
            free_request_limit = max(
                0, int(os.environ['FRESHSKY_FREE_REQUEST_LIMIT'])
            )
        except ValueError:
            free_request_limit = None
    subscription_ready = bool(
        subscriptions_enabled
        and stripe_enabled
        and subscription_tier
        and subscription_price_id
        and subscription_amount_cents > 0
    )
    primary_url = (primary_url or '').rstrip('/')
    redirect_uri = f'{primary_url}/auth/google/callback' if primary_url else ''
    primary_host = (urlparse(primary_url).hostname or '').lower()
    # Community mode is retained for UI compatibility with civic-volunteer
    # apps, but every app now receives the same unrestricted access.
    # Three triggers (any one is enough):
    #   1. register_freemium(..., community_mode=True) in app.py
    #   2. COMMUNITY_TOOL=true env var on the Cloud Run service
    #   3. Hostname auto-detection at request time (see below) — covers the
    #      known civic-volunteer subdomains without per-app config.
    _STATIC_COMMUNITY_HOSTS = {
        'nfirs.freshskyai.com', 'capr.freshskyai.com',
        'capmeeting.freshskyai.com', 'capstudy.freshskyai.com',
        'firstresponder.freshskyai.com', 'cap.freshskyai.com',
    }
    community_mode_static = community_mode or os.environ.get('COMMUNITY_TOOL', '').lower() in ('1', 'true', 'yes')
    def _is_community_request() -> bool:
        if community_mode_static:
            return True
        host = (request.host or '').split(':')[0].lower()
        return host in _STATIC_COMMUNITY_HOSTS

    # ─── GATE FUNCTION ───────────────────────────────────────────
    def _session_has_subscription() -> bool:
        return bool(
            subscription_ready
            and session.get('subscription_tier') == subscription_tier
            and float(session.get('subscription_checked_at') or 0) > time.time() - 300
        )

    def _stripe_has_subscription(email: str) -> bool:
        """Verify an active subscription for a confirmed email.

        Stripe stays the source of truth. The five-minute session cache avoids
        an API lookup on every generation request while keeping cancellations
        reasonably prompt.
        """
        if not subscription_ready or not email:
            return False
        try:
            import stripe
            stripe.api_key = stripe_secret_key
            customers = stripe.Customer.list(email=email, limit=10)
            for customer in customers.data:
                subscriptions = stripe.Subscription.list(
                    customer=customer.id, status='all', limit=100
                )
                for item in subscriptions.data:
                    status = getattr(item, 'status', '')
                    if status not in {'active', 'trialing'}:
                        continue
                    for sub_item in getattr(getattr(item, 'items', None), 'data', []):
                        if getattr(getattr(sub_item, 'price', None), 'id', '') == subscription_price_id:
                            session['subscription_tier'] = subscription_tier
                            session['subscription_checked_at'] = time.time()
                            return True
        except Exception as exc:
            logger.warning('Subscription verification unavailable: %s', exc)
        return False

    def check() -> Optional[tuple]:
        if not subscription_ready:
            return None
        if _session_has_subscription() or _stripe_has_subscription(
            (session.get('user_email') or '').lower()
        ):
            return None
        if free_request_limit is None:
            return None
        used = max(0, int(session.get('free_requests_used') or 0))
        if used < max(0, free_request_limit):
            session['free_requests_used'] = used + 1
            return None
        return jsonify(
            error='A monthly plan is required for additional runs.',
            code='subscription_required',
            tier=subscription_tier,
            price_cents=subscription_amount_cents,
            subscribe_url='/subscribe',
            login_url='/auth/google?next=/subscribe',
        ), 402

    if expose_provider_metrics:
        from .llm import install_provider_metrics
        install_provider_metrics(app)

    # ─── GOOGLE OAUTH ────────────────────────────────────────────
    @app.route('/auth/google')
    def freemium_google_login():
        if not google_auth_enabled:
            return jsonify(error='Google login is not configured.'), 503
        if not redirect_uri or not primary_host:
            return jsonify(error='Google login callback is not configured.'), 503
        next_url = request.args.get('next', '')
        if next_url.startswith('/') and not next_url.startswith('//'):
            session['oauth_next'] = next_url
        else:
            session.pop('oauth_next', None)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        session['oauth_state'] = state
        session['oauth_nonce'] = nonce
        params = urlencode({
            'client_id': google_client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': 'openid email profile',
            'state': state,
            'nonce': nonce,
            'access_type': 'online',
            'prompt': 'select_account',
        })
        return redirect(f'https://accounts.google.com/o/oauth2/v2/auth?{params}')

    @app.route('/auth/google/callback')
    def freemium_google_callback():
        import requests as _r
        if request.args.get('error'):
            return redirect(url_for('index'))
        code = request.args.get('code')
        state = request.args.get('state')
        if not code or state != session.pop('oauth_state', None):
            return redirect(url_for('index'))
        expected_nonce = session.pop('oauth_nonce', None)
        try:
            from google.auth.transport.requests import Request as GoogleRequest
            from google.oauth2 import id_token as google_id_token

            tok = _r.post('https://oauth2.googleapis.com/token', data={
                'code': code,
                'client_id': google_client_id,
                'client_secret': google_client_secret,
                'redirect_uri': redirect_uri,
                'grant_type': 'authorization_code',
            }, timeout=15)
            tok.raise_for_status()
            raw_id_token = tok.json().get('id_token', '')
            info = google_id_token.verify_oauth2_token(
                raw_id_token,
                GoogleRequest(),
                audience=google_client_id,
            )
            returned_nonce = str(info.get('nonce') or '')
            if not expected_nonce or not secrets.compare_digest(
                str(expected_nonce), returned_nonce
            ):
                raise ValueError('Google ID token nonce did not match')
        except Exception as exc:
            logger.warning('OAuth callback error: %s', exc)
            return redirect(url_for('index'))
        email = (info.get('email') or '').lower()
        name = info.get('name', email.split('@')[0] if email else '')
        if not email or info.get('email_verified') is not True:
            return redirect(url_for('index'))
        next_url = session.get('oauth_next', '')
        # Session fixation defense
        session.clear()
        session.permanent = True
        session['user_email'] = email
        session['user_name'] = name
        if next_url.startswith('/') and not next_url.startswith('//'):
            return redirect(next_url)
        return redirect(url_for('index'))

    @app.route('/logout')
    def freemium_logout():
        session.clear()
        return redirect(url_for('index'))

    @app.route('/subscribe')
    def freemium_subscribe():
        if not subscription_ready:
            return redirect('https://www.freshskyai.com/donate', code=302)
        try:
            import stripe
            stripe.api_key = stripe_secret_key
            args = {
                'mode': 'subscription',
                'line_items': [{'price': subscription_price_id, 'quantity': 1}],
                'success_url': (
                    f'{primary_url}/subscription/success'
                    '?session_id={CHECKOUT_SESSION_ID}'
                ),
                'cancel_url': f'{primary_url}/?checkout=canceled',
                'allow_promotion_codes': True,
                'metadata': {
                    'app_host': primary_host,
                    'tier': subscription_tier,
                },
                'subscription_data': {
                    'metadata': {
                        'app_host': primary_host,
                        'tier': subscription_tier,
                    }
                },
            }
            email = (session.get('user_email') or '').lower()
            if email:
                args['customer_email'] = email
            checkout = stripe.checkout.Session.create(**args)
            return redirect(checkout.url, code=303)
        except Exception as exc:
            logger.error('Stripe subscription checkout error: %s', exc)
            return redirect(f'{primary_url}/?checkout=unavailable', code=302)

    @app.route('/subscribe/yearly')
    def freemium_subscribe_yearly():
        # FreshSky subscriptions are monthly only.
        return redirect(url_for('freemium_subscribe'), code=302)

    @app.route('/subscription/success')
    def freemium_subscription_success():
        if not subscription_ready:
            return redirect(url_for('index'))
        checkout_id = request.args.get('session_id', '')
        if not checkout_id.startswith('cs_'):
            return redirect(f'{primary_url}/?checkout=unverified', code=302)
        try:
            import stripe
            stripe.api_key = stripe_secret_key
            checkout = stripe.checkout.Session.retrieve(checkout_id)
            metadata = getattr(checkout, 'metadata', {}) or {}
            details = getattr(checkout, 'customer_details', None)
            email = (getattr(details, 'email', '') or '').lower()
            verified = bool(
                getattr(checkout, 'status', '') == 'complete'
                and getattr(checkout, 'mode', '') == 'subscription'
                and getattr(checkout, 'subscription', None)
                and metadata.get('app_host') == primary_host
                and metadata.get('tier') == subscription_tier
                and email
            )
            if not verified:
                raise ValueError('checkout did not match this application')
            session.permanent = True
            session['user_email'] = email
            session.setdefault('user_name', email.split('@')[0])
            session['subscription_tier'] = subscription_tier
            session['subscription_checked_at'] = time.time()
            return redirect(f'{primary_url}/?checkout=success', code=303)
        except Exception as exc:
            logger.warning('Subscription checkout verification failed: %s', exc)
            return redirect(f'{primary_url}/?checkout=unverified', code=302)

    @app.route('/billing')
    def freemium_billing_portal():
        if not stripe_enabled:
            if (request.host or '').split(':')[0].lower() in {
                'freshskyai.com', 'www.freshskyai.com',
            }:
                return redirect(url_for('index'))
            return redirect('https://www.freshskyai.com/billing', code=302)
        if not session.get('user_email'):
            return redirect(url_for('freemium_google_login', next='/billing'))
        try:
            import stripe
            stripe.api_key = stripe_secret_key
            customers = stripe.Customer.list(email=session['user_email'], limit=1)
            if not customers.data:
                return redirect('https://www.freshskyai.com/donate', code=302)
            portal = stripe.billing_portal.Session.create(
                customer=customers.data[0].id,
                return_url=primary_url or url_for('index', _external=True),
            )
            return redirect(portal.url)
        except Exception as exc:
            logger.error('Stripe portal error: %s', exc)
            return redirect(url_for('index'))

    # ─── WEBHOOK ─────────────────────────────────────────────────
    @app.route('/stripe-webhook', methods=['POST'])
    def freemium_stripe_webhook():
        if not stripe_enabled:
            return '', 503
        try:
            import stripe
            stripe.api_key = stripe_secret_key
            event = stripe.Webhook.construct_event(
                request.data, request.headers.get('Stripe-Signature', ''),
                stripe_webhook_secret,
            )
        except Exception:
            return 'Invalid signature', 400
        try:
            event_d = event.to_dict() if hasattr(event, 'to_dict') else dict(event)
            etype = event_d.get('type', '')
        except Exception:
            etype = ''
        logger.info('freemium webhook: %s', etype)
        return '', 200

    if enable_email_capture:
        @app.route('/api/notify', methods=['POST'])
        def freemium_email_capture():
            """Capture an explicitly opted-in product-update email."""
            data = request.get_json(silent=True) or request.form or {}
            email = (data.get('email') or '').strip().lower()
            if not email or '@' not in email or len(email) > 200:
                return jsonify(ok=False, error='invalid email'), 400
            source = (data.get('source') or '').strip()[:80]
            try:
                _persist_email_capture(email, source)
                return jsonify(ok=True), 200
            except Exception as exc:
                logger.warning('email capture skipped: %s', exc)
                return jsonify(ok=False, error='temporarily unavailable'), 503

    # ─── FREEMIUM STATIC JS ──────────────────────────────────────
    # Served from package data so apps don't need to copy the file into
    # their own static/ directory.
    import importlib.resources as _ir

    @app.route('/freemium.js')
    def freemium_js():
        try:
            content = (_ir.files('freshsky_common.static') / 'freemium.js').read_text(encoding='utf-8')
        except Exception:
            content = ''
        resp = Response(content, mimetype='application/javascript; charset=utf-8')
        resp.headers['Cache-Control'] = 'public, max-age=3600'
        return resp

    # ─── USER STATUS API ─────────────────────────────────────────
    @app.route('/api/user-status')
    def freemium_user_status():
        email = (session.get('user_email') or '').lower()
        community_request = _is_community_request()
        base = {
            'logged_in': bool(email),
            'google_auth_enabled': google_auth_enabled,
            'free_access': not subscription_ready or free_request_limit is None,
            'full_access': not subscription_ready,
            'daily_limit': free_request_limit,
            'stripe_enabled': bool(stripe_enabled),
            'subscription_enabled': subscription_ready,
            'subscription_tier': subscription_tier or None,
            'subscription_price_cents': subscription_amount_cents or None,
            'community_mode': community_request,
            'donate_url': 'https://www.freshskyai.com/donate',
            # Compatibility alias for older app JavaScript.
            'sponsor_url': 'https://www.freshskyai.com/donate',
        }
        if email:
            base['email'] = email
            base['name'] = session.get('user_name', '')
            if subscription_ready:
                base['full_access'] = (
                    _session_has_subscription() or _stripe_has_subscription(email)
                )
        if subscription_ready:
            base['free_requests_used'] = int(session.get('free_requests_used') or 0)
            base['subscribe_url'] = '/subscribe'
        return jsonify(base)

    return check


# ─── FIRESTORE PERSISTENCE ──────────────────────────────────────────
# Free tier: 1 GiB storage, 50K reads/day, 20K writes/day. Sufficient
# for email capture at any reasonable scale. Falls back silently if the
# Firestore client isn't installed or authentication fails.

_FIRESTORE_CLIENT = None
_FIRESTORE_TRIED = False


def _firestore():
    """Lazy singleton; returns None if Firestore is unavailable."""
    global _FIRESTORE_CLIENT, _FIRESTORE_TRIED
    if _FIRESTORE_TRIED:
        return _FIRESTORE_CLIENT
    _FIRESTORE_TRIED = True
    try:
        from google.cloud import firestore
        _FIRESTORE_CLIENT = firestore.Client()
    except Exception as exc:
        logger.info('Firestore unavailable: %s', exc)
    return _FIRESTORE_CLIENT


def _persist_email_capture(email: str, source: str) -> None:
    """Write captured email to Firestore (collection: notify_subscribers)."""
    db = _firestore()
    if not db:
        raise RuntimeError('Firestore unavailable')
    from google.cloud import firestore as _fs
    db.collection('notify_subscribers').document(email).set({
        'email': email,
        'source': source,
        'captured_at': _fs.SERVER_TIMESTAMP,
    }, merge=True)
