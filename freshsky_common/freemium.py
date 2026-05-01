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
    pro_monthly_dollars: str = '$1.99',
    pro_yearly_dollars: str = '$19',
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
        # stripe.Webhook.construct_event returns a StripeObject (not a
        # dict). Its attribute-access semantics treat `.get` as a field
        # lookup and raise AttributeError. .to_dict() recursively coerces
        # to plain Python types so the rest of this handler can use the
        # familiar .get() / dict idioms.
        try:
            event_d = event.to_dict() if hasattr(event, 'to_dict') else dict(event)
            etype = event_d.get('type', '')
            obj = event_d.get('data', {}).get('object', {}) or {}
        except Exception:
            etype, obj = '', {}
        logger.info('freemium webhook: %s', etype)
        # Persist Pro state to Firestore so signed-in users see the flip
        # immediately on next /api/user-status poll without re-login.
        # Best-effort: webhook returns 200 even if Firestore write fails
        # (Stripe will resend on 5xx; we don't want to block the receipt).
        try:
            email = ''
            # checkout.session.completed → customer_email + customer
            # customer.subscription.{created,updated,deleted} → customer
            customer_id = obj.get('customer') or ''
            email = (obj.get('customer_email') or
                     obj.get('customer_details', {}).get('email') or '')
            if not email and customer_id:
                # Look up the customer to get its email
                cust = stripe.Customer.retrieve(customer_id)
                email = cust.get('email', '') or ''
            if email:
                if etype in ('checkout.session.completed',
                             'customer.subscription.created',
                             'customer.subscription.updated'):
                    # Decide is_pro based on subscription status (if present)
                    sub_status = obj.get('status') or ''
                    if sub_status in ('active', 'trialing'):
                        _persist_pro_state(email.lower(), True)
                    elif sub_status in ('canceled', 'unpaid', 'incomplete_expired'):
                        _persist_pro_state(email.lower(), False)
                    else:
                        # checkout.session.completed has no status field — treat as active
                        if etype == 'checkout.session.completed':
                            _persist_pro_state(email.lower(), True)
                elif etype == 'customer.subscription.deleted':
                    _persist_pro_state(email.lower(), False)
        except Exception as exc:
            logger.warning('webhook persist skipped: %s', exc)
        return '', 200

    # ─── EMAIL CAPTURE ───────────────────────────────────────────
    @app.route('/api/notify', methods=['POST'])
    def freemium_email_capture():
        """Capture an email for re-engagement (paywall hit, no upgrade).
        Stores to Firestore notify_subscribers/<email>. Idempotent."""
        from flask import jsonify as _jsonify
        data = request.get_json(silent=True) or request.form or {}
        email = (data.get('email') or '').strip().lower()
        if not email or '@' not in email or len(email) > 200:
            return _jsonify(ok=False, error='invalid email'), 400
        source = (data.get('source') or '').strip()[:80]
        try:
            _persist_email_capture(email, source)
            return _jsonify(ok=True), 200
        except Exception as exc:
            logger.warning('email capture skipped: %s', exc)
            return _jsonify(ok=False, error='temporarily unavailable'), 503

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
        # Refresh is_pro from Firestore (server-pushed by webhook). Falls
        # through to the existing session value if Firestore is unreachable
        # or has no record yet.
        email = (session.get('user_email') or '').lower()
        if email:
            fs_pro = _read_pro_state(email)
            if fs_pro is not None:
                session['is_pro'] = fs_pro
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


# ─── FIRESTORE PERSISTENCE ──────────────────────────────────────────
# Free tier: 1 GiB storage, 50K reads/day, 20K writes/day. Sufficient
# for is_pro flips + email capture at any reasonable scale. Falls back
# silently if Firestore client isn't installed or auth fails — system
# remains functional, just without push-Pro and without email capture.

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


def _persist_pro_state(email: str, is_pro: bool) -> None:
    """Write {email → is_pro} to Firestore (collection: pro_users)."""
    db = _firestore()
    if not db or not email:
        return
    from google.cloud import firestore as _fs
    db.collection('pro_users').document(email).set({
        'is_pro': bool(is_pro),
        'updated_at': _fs.SERVER_TIMESTAMP,
    }, merge=True)


def _read_pro_state(email: str):
    """Return True/False if Firestore has a record for ``email``,
    else None (caller falls through to other sources)."""
    db = _firestore()
    if not db or not email:
        return None
    try:
        doc = db.collection('pro_users').document(email).get()
        if doc.exists:
            return bool(doc.to_dict().get('is_pro', False))
    except Exception as exc:
        logger.info('pro_users read failed for %s: %s', email, exc)
    return None


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
