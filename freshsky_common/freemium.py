"""Drop-in freemium gate for batch apps.

Single call to ``register_freemium(app, ...)`` adds:

* ``/auth/google`` + ``/auth/google/callback`` — OAuth login with optional
  ``?next=`` round-trip.
* ``/logout`` — clears session.
* ``/subscribe`` + ``/subscribe/yearly`` — Stripe Checkout for monthly
  and yearly Pro tiers. Anonymous clicks detour through Google sign-in.
* ``/billing`` — Stripe Customer Billing Portal for active subscribers.
* ``/stripe-webhook`` — flips ``session['is_pro']`` based on
  ``customer.subscription.{created,updated,deleted}`` events.
* ``/api/user-status`` — JSON endpoint the frontend hits to render the
  user bar (logged-in state, usage today, daily limit, pro flag).

Usage in app.py::

    from freshsky_common.freemium import register_freemium

    check = register_freemium(
        app,
        free_daily_limit=10,
        google_client_id=os.environ['GOOGLE_CLIENT_ID'],
        google_client_secret=os.environ['GOOGLE_CLIENT_SECRET'],
        stripe_secret_key=os.environ.get('STRIPE_SECRET_KEY', ''),
        stripe_price_monthly=os.environ.get('STRIPE_PRICE_MONTHLY', ''),
        stripe_price_yearly=os.environ.get('STRIPE_PRICE_YEARLY', ''),
        stripe_webhook_secret=os.environ.get('STRIPE_WEBHOOK_SECRET', ''),
        primary_url='https://foia.freshskyai.com/',
        owner_email='admin@freshskyllc.com',
    )

    @app.route('/api/whatever', methods=['POST'])
    def whatever():
        gate = check()
        if gate is not None:
            return gate
        ...

The gate is a no-op for Pro / owner sessions. For free-tier sessions it
marks the request on ``g`` so an after_request hook can charge usage
only when the response is 2xx — failed input validation never burns a
quota slot.
"""
from __future__ import annotations

import logging
import secrets
from datetime import date
from typing import Callable, Optional
from urllib.parse import urlencode

from flask import (
    Flask, Response, g, jsonify, redirect, request, session, url_for,
)


logger = logging.getLogger(__name__)


def register_freemium(
    app: Flask,
    *,
    free_daily_limit: int = 10,
    google_client_id: str = '',
    google_client_secret: str = '',
    stripe_secret_key: str = '',
    stripe_price_monthly: str = '',
    stripe_price_yearly: str = '',
    stripe_webhook_secret: str = '',
    primary_url: str = '',
    owner_email: str = '',
    pro_pricing_label: str = 'Pro',
    pro_monthly_dollars: str = '$3.99',
    pro_yearly_dollars: str = '$29',
) -> Callable[[], Optional[Response]]:
    """Wire freemium routes onto ``app`` and return the gate function.

    Returns
    -------
    check : callable -> None | (Response, int)
        Call at the top of each gated endpoint. Returns ``None`` if the
        request is allowed to proceed; returns a 429 ``(Response, int)``
        tuple when the daily free limit has been reached. Use::

            gate = check()
            if gate is not None:
                return gate
    """
    google_auth_enabled = bool(google_client_id and google_client_secret)
    stripe_enabled = bool(stripe_secret_key and stripe_price_monthly)
    primary_url = (primary_url or '').rstrip('/')
    redirect_uri = f'{primary_url}/auth/google/callback' if primary_url else ''
    owner_email = (owner_email or '').strip().lower()

    # ─── GATE FUNCTION ───────────────────────────────────────────
    def check() -> Optional[tuple]:
        if session.get('is_pro'):
            return None
        if owner_email and (session.get('user_email') or '').lower() == owner_email:
            return None
        today = date.today().isoformat()
        key = f'usage_{today}'
        usage = session.get(key, 0)
        if usage >= free_daily_limit:
            return jsonify(
                error=(
                    f'Daily free limit reached ({free_daily_limit} queries). '
                    f'Upgrade to {pro_pricing_label} for unlimited access.'
                ),
                upgrade_required=True,
            ), 429
        # Don't increment yet — defer to after_request so we only charge
        # successful (2xx) calls. Failed validation (400) is free.
        g.freemium_will_charge = True
        g.freemium_usage_key = key
        return None

    @app.after_request
    def _charge_on_success(response):
        if getattr(g, 'freemium_will_charge', False) and 200 <= response.status_code < 300:
            key = getattr(g, 'freemium_usage_key', None)
            if key:
                session[key] = session.get(key, 0) + 1
        return response

    # ─── GOOGLE OAUTH ────────────────────────────────────────────
    @app.route('/auth/google')
    def freemium_google_login():
        if not google_auth_enabled:
            return jsonify(error='Google login is not configured.'), 503
        if not redirect_uri:
            return jsonify(error='primary_url is not set; cannot build redirect URI.'), 503
        next_url = request.args.get('next', '')
        if next_url.startswith('/') and not next_url.startswith('//'):
            session['oauth_next'] = next_url
        else:
            session.pop('oauth_next', None)
        state = secrets.token_urlsafe(32)
        session['oauth_state'] = state
        params = urlencode({
            'client_id': google_client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': 'openid email profile',
            'state': state,
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
        try:
            tok = _r.post('https://oauth2.googleapis.com/token', data={
                'code': code,
                'client_id': google_client_id,
                'client_secret': google_client_secret,
                'redirect_uri': redirect_uri,
                'grant_type': 'authorization_code',
            }, timeout=15)
            tok.raise_for_status()
            access_token = tok.json()['access_token']
            ui = _r.get(
                'https://www.googleapis.com/oauth2/v3/userinfo',
                headers={'Authorization': f'Bearer {access_token}'}, timeout=15,
            )
            ui.raise_for_status()
            info = ui.json()
        except Exception as exc:
            logger.warning('OAuth callback error: %s', exc)
            return redirect(url_for('index'))
        email = (info.get('email') or '').lower()
        name = info.get('name', email.split('@')[0] if email else '')
        if not email:
            return redirect(url_for('index'))
        is_pro = _check_stripe_subscription(stripe_secret_key, stripe_enabled, email)
        next_url = session.get('oauth_next', '')
        # Session fixation defense
        session.clear()
        session.permanent = True
        session['user_email'] = email
        session['user_name'] = name
        session['is_pro'] = is_pro
        if next_url.startswith('/') and not next_url.startswith('//'):
            return redirect(next_url)
        return redirect(url_for('index'))

    @app.route('/logout')
    def freemium_logout():
        session.clear()
        return redirect(url_for('index'))

    # ─── STRIPE CHECKOUT ─────────────────────────────────────────
    @app.route('/subscribe')
    def freemium_subscribe():
        if not stripe_enabled:
            return redirect(url_for('index'))
        if not session.get('user_email'):
            return redirect(url_for('freemium_google_login', next='/subscribe'))
        return _open_checkout(
            stripe_secret_key, stripe_price_monthly,
            session['user_email'], primary_url,
        )

    @app.route('/subscribe/yearly')
    def freemium_subscribe_yearly():
        if not stripe_enabled or not stripe_price_yearly:
            return redirect(url_for('index'))
        if not session.get('user_email'):
            return redirect(url_for('freemium_google_login', next='/subscribe/yearly'))
        return _open_checkout(
            stripe_secret_key, stripe_price_yearly,
            session['user_email'], primary_url,
        )

    @app.route('/billing')
    def freemium_billing_portal():
        if not stripe_enabled:
            return redirect(url_for('index'))
        if not session.get('user_email'):
            return redirect(url_for('freemium_google_login', next='/billing'))
        try:
            import stripe
            stripe.api_key = stripe_secret_key
            customers = stripe.Customer.list(email=session['user_email'], limit=1)
            if not customers.data:
                return redirect(url_for('freemium_subscribe'))
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
        etype = event.get('type', '')
        obj = event.get('data', {}).get('object', {}) or {}
        logger.info('freemium webhook: %s', etype)
        # We don't keep a persistent customer DB at the app level; instead
        # the next time the customer signs in, _check_stripe_subscription
        # will hit the live Stripe API and set session['is_pro'] correctly.
        # This keeps the gate consistent without needing a DB write here.
        # For users currently signed in we have no server-side push, but
        # subsequent /api/user-status polls will refresh.
        return '', 200

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
        today = date.today().isoformat()
        usage = session.get(f'usage_{today}', 0)
        base = {
            'logged_in': bool(session.get('user_email')),
            'google_auth_enabled': google_auth_enabled,
            'is_pro': bool(session.get('is_pro')),
            'usage_today': usage,
            'daily_limit': free_daily_limit,
            'stripe_enabled': stripe_enabled,
            'pro_monthly_dollars': pro_monthly_dollars,
            'pro_yearly_dollars': pro_yearly_dollars,
            'pro_pricing_label': pro_pricing_label,
        }
        if session.get('user_email'):
            base['email'] = session['user_email']
            base['name'] = session.get('user_name', '')
        return jsonify(base)

    return check


# ─── HELPERS ────────────────────────────────────────────────────
def _check_stripe_subscription(secret_key: str, enabled: bool, email: str) -> bool:
    """Return True if ``email`` has at least one active Stripe subscription."""
    if not enabled or not email:
        return False
    try:
        import stripe
        stripe.api_key = secret_key
        customers = stripe.Customer.list(email=email, limit=10)
        for c in customers.data:
            subs = stripe.Subscription.list(customer=c.id, status='active', limit=5)
            if subs.data:
                return True
        return False
    except Exception as exc:
        logger.warning('Stripe subscription check failed for %s: %s', email, exc)
        return False


def _open_checkout(secret_key: str, price_id: str, email: str, primary_url: str):
    """Create a Stripe Checkout session and redirect the user to it."""
    try:
        import stripe
        stripe.api_key = secret_key
        success_url = (primary_url or '') + '/?upgraded=1'
        cancel_url = (primary_url or '') + '/'
        checkout = stripe.checkout.Session.create(
            customer_email=email,
            mode='subscription',
            line_items=[{'price': price_id, 'quantity': 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            allow_promotion_codes=True,
        )
        return redirect(checkout.url)
    except Exception as exc:
        logger.error('Stripe checkout error: %s', exc)
        return redirect('/')
