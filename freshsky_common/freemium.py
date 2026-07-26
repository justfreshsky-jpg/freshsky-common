"""Drop-in access, authentication, and subscription helpers for Fresh Sky apps.

Single call to ``register_freemium(app, ...)`` adds:

* ``/auth/google`` + ``/auth/google/callback`` — OAuth login with optional
  ``?next=`` round-trip.
* ``/logout`` — clears session.
* ``/subscribe`` — optional monthly Stripe Checkout (disabled by default).
* ``/subscribe/yearly`` — intentionally redirects to monthly pricing.
* ``/billing`` — Stripe Customer Billing Portal for current and historical
  subscription customers.
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
FreshSky plans unlock their own tier and lower tiers, with a portfolio-wide
paid usage allowance. Provider safety controls and platform cost controls
remain separate.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlencode, urlparse

from flask import (
    Flask, Response, jsonify, redirect, request, session, url_for,
)


logger = logging.getLogger(__name__)

PLAN_RANK = {'focus': 1, 'civic': 2, 'plus': 3, 'advanced': 4}
PLAN_LIMITS = {
    'focus': {'daily': 20, 'monthly': 100},
    'civic': {'daily': 40, 'monthly': 200},
    'plus': {'daily': 60, 'monthly': 300},
    'advanced': {'daily': 120, 'monthly': 600},
    'owner': {'daily': 500, 'monthly': 2000},
}
def subscription_item_tier(
    item,
    configured_price_id: str = '',
    configured_tier: str = '',
    *,
    product_override=None,
) -> str:
    """Return the trusted FreshSky tier represented by a Stripe line item."""
    price = getattr(item, 'price', None)
    if isinstance(item, dict):
        price = item.get('price', {})
    price_id = (
        price.get('id', '') if isinstance(price, dict) else getattr(price, 'id', '')
    )
    if configured_price_id and price_id == configured_price_id:
        return configured_tier if configured_tier in PLAN_RANK else ''

    product = product_override
    if product is None:
        product = (
            price.get('product', {}) if isinstance(price, dict)
            else getattr(price, 'product', None)
        )
    metadata = (
        product.get('metadata', {}) if isinstance(product, dict)
        else getattr(product, 'metadata', {}) or {}
    )
    tier = str(
        metadata.get('freshsky_tier') or metadata.get('tier') or ''
    ).strip().lower()
    if tier in PLAN_RANK:
        return tier

    name = str(
        product.get('name', '') if isinstance(product, dict)
        else getattr(product, 'name', '')
    ).strip().lower()
    for candidate in PLAN_RANK:
        if name.startswith(f'freshsky {candidate}'):
            return candidate
    return ''


_USAGE_FIRESTORE_CLIENT = None
_USAGE_FIRESTORE_TRIED = False


def _usage_firestore_client():
    global _USAGE_FIRESTORE_CLIENT, _USAGE_FIRESTORE_TRIED
    if _USAGE_FIRESTORE_TRIED:
        return _USAGE_FIRESTORE_CLIENT
    _USAGE_FIRESTORE_TRIED = True
    try:
        from google.cloud import firestore
        _USAGE_FIRESTORE_CLIENT = firestore.Client()
    except Exception as exc:
        logger.warning('Paid usage meter unavailable: %s', exc)
    return _USAGE_FIRESTORE_CLIENT


def consume_paid_identity(
    identity: str,
    tier: str,
    *,
    usage_units: int = 1,
) -> tuple[bool, dict]:
    """Atomically consume portfolio-wide usage units by pseudonymous ID."""
    if tier not in PLAN_LIMITS:
        raise ValueError('unknown usage tier')
    if not isinstance(usage_units, int) or usage_units <= 0:
        raise ValueError('usage_units must be a positive integer')
    limits = PLAN_LIMITS[tier]
    client = _usage_firestore_client()
    if client is None:
        raise RuntimeError('paid usage meter is unavailable')

    from google.cloud import firestore

    now = time.gmtime()
    month = time.strftime('%Y%m', now)
    day = time.strftime('%Y%m%d', now)
    reference = client.collection('paid_ai_usage_units_monthly').document(
        f'{month}-{identity}'
    )
    transaction = client.transaction()

    @firestore.transactional
    def consume(txn):
        snapshot = reference.get(transaction=txn)
        data = snapshot.to_dict() if snapshot.exists else {}
        total = max(0, int(data.get('total') or 0))
        days = dict(data.get('days') or {})
        today = max(0, int(days.get(day) or 0))
        if (
            total + usage_units > limits['monthly']
            or today + usage_units > limits['daily']
        ):
            return False, {
                'daily_used': today,
                'monthly_used': total,
                'required_units': usage_units,
                'quota_unit': 'usage_unit',
                **limits,
            }
        days[day] = today + usage_units
        txn.set(
            reference,
            {
                'tier': tier,
                'month': month,
                'total': total + usage_units,
                'days': days,
                'quota_unit': 'usage_unit',
                'updated_at': firestore.SERVER_TIMESTAMP,
            },
        )
        return True, {
            'daily_used': today + usage_units,
            'monthly_used': total + usage_units,
            'required_units': usage_units,
            'quota_unit': 'usage_unit',
            **limits,
        }

    return consume(transaction)


def _consume_paid_allowance(
    email: str,
    tier: str,
    signing_key: str,
    *,
    usage_units: int = 1,
    workflow_class: str = 'preview',
    workspace_id: str = '',
    reservation_id: str | None = None,
) -> tuple[bool, dict]:
    """Consume usage units locally on the hub or via its signed central meter.

    The subapp creates a fresh 128-bit reservation identifier for every logical
    central-meter call.  A caller that retries the same logical call may pass
    the original identifier so the hub can return the existing reservation
    without consuming the allowance twice.
    """
    if not signing_key:
        raise RuntimeError(
            'dedicated usage-meter signing key is unavailable'
        )
    identity = hmac.new(
        signing_key.encode('utf-8'),
        email.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    service = os.environ.get('K_SERVICE', '').strip()
    meter_url = os.environ.get('FRESHSKY_USAGE_METER_URL', '').strip()
    if not meter_url and service and service != 'freshskyai':
        meter_url = (
            'https://www.freshskyai.com/internal/paid-usage/consume'
        )
    if not meter_url:
        return consume_paid_identity(
            identity,
            tier,
            usage_units=usage_units,
        )

    import json
    import requests

    if reservation_id is None:
        reservation_id = secrets.token_hex(16)
    elif (
        not isinstance(reservation_id, str)
        or len(reservation_id) != 32
        or any(character not in '0123456789abcdef' for character in reservation_id)
    ):
        raise ValueError(
            'reservation_id must be 32 lowercase hexadecimal characters'
        )
    body = {
        'identity': identity,
        'reservation_id': reservation_id,
        'tier': tier,
        'usage_units': usage_units,
        'workflow_class': workflow_class,
    }
    if workspace_id:
        body['workspace_id'] = workspace_id
    encoded = json.dumps(
        body, separators=(',', ':'), sort_keys=True
    ).encode('utf-8')
    timestamp = str(int(time.time()))
    signature = hmac.new(
        signing_key.encode('utf-8'),
        timestamp.encode('ascii') + b'.' + encoded,
        hashlib.sha256,
    ).hexdigest()
    response = requests.post(
        meter_url,
        data=encoded,
        headers={
            'Content-Type': 'application/json',
            'X-FreshSky-Usage-Timestamp': timestamp,
            'X-FreshSky-Usage-Signature': f'v1={signature}',
        },
        timeout=5,
    )
    response.raise_for_status()
    result = response.json()
    usage = dict(result.get('usage') or {})
    required = {'daily', 'monthly', 'daily_used', 'monthly_used'}
    if not required.issubset(usage):
        raise RuntimeError('central usage meter returned an invalid response')
    if usage_units != 1 and (
        usage.get('required_units') != usage_units
        or usage.get('quota_unit') != 'usage_unit'
    ):
        raise RuntimeError(
            'central usage meter does not support weighted reservations'
        )
    usage.setdefault('required_units', usage_units)
    usage.setdefault('quota_unit', 'usage_unit')
    return bool(result.get('allowed')), usage


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
    gate_all_post: bool = False,
    workspace_id: str = '',
    focus_workspace: str = '',
    auth_broker_url: str = '',
) -> Callable[..., Optional[Response | tuple]]:
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
    dedicated_usage_hmac_key = os.environ.get(
        'FRESHSKY_USAGE_HMAC_KEY', ''
    ).strip()
    managed_runtime = bool(
        os.environ.get('K_SERVICE')
        or os.environ.get('FRESHSKY_ENV', '').strip().lower()
        in {'prod', 'production'}
    )
    usage_hmac_key = (
        dedicated_usage_hmac_key
        or (stripe_secret_key if not managed_runtime else '')
    )
    workspace_id = (
        workspace_id or os.environ.get('FRESHSKY_WORKSPACE_ID', '')
    ).strip().lower()
    focus_workspace = (
        focus_workspace or os.environ.get('FRESHSKY_FOCUS_WORKSPACE', '')
    ).strip().lower()
    auth_broker_url = (
        auth_broker_url
        or os.environ.get('FRESHSKY_AUTH_BROKER_URL', '')
        or os.environ.get('GOOGLE_REDIRECT_BASE_URL', '')
    ).strip().rstrip('/')
    if auth_broker_url:
        broker_parts = urlparse(auth_broker_url)
        broker_host = (broker_parts.hostname or '').lower()
        if (
            not broker_host
            or broker_parts.username
            or broker_parts.password
            or (
                broker_parts.scheme != 'https'
                and not (
                    broker_parts.scheme == 'http'
                    and broker_host in {'localhost', '127.0.0.1', '::1'}
                )
            )
        ):
            raise ValueError(
                'auth_broker_url must be HTTPS (or HTTP on localhost)'
            )
    if workspace_id:
        from .runtime_policy import parse_workspace
        workspace_id = parse_workspace(workspace_id).value
    if focus_workspace:
        from .runtime_policy import parse_workspace
        focus_workspace = parse_workspace(focus_workspace).value
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
    if workspace_id and subscription_ready and free_request_limit is None:
        # The consolidated workspace policy is opt-in via workspace_id.
        # Legacy apps that have not adopted it keep their existing behavior.
        free_request_limit = 3
    primary_url = (primary_url or '').rstrip('/')
    google_redirect_base = auth_broker_url or primary_url
    redirect_uri = (
        f'{google_redirect_base}/auth/google/callback'
        if google_redirect_base
        else ''
    )
    primary_host = (urlparse(primary_url).hostname or '').lower()
    google_redirect_host = (
        urlparse(google_redirect_base).hostname or ''
    ).lower()
    # Community mode is retained as a product-category signal for
    # civic-volunteer apps. It never bypasses a configured subscription.
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
    def _tier_allows(
        entitled_tier: str,
        selected_workspace_override: str | None = None,
    ) -> bool:
        if workspace_id:
            try:
                from .entitlements import resolve_entitlement
                selected = (
                    selected_workspace_override
                    or session.get('focus_workspace')
                    or focus_workspace
                    or None
                )
                entitlement = resolve_entitlement(
                    entitled_tier,
                    selected_workspace=selected,
                )
                return entitlement.can_access(workspace_id)
            except ValueError:
                return False
        return PLAN_RANK.get(entitled_tier, 0) >= PLAN_RANK.get(subscription_tier, 0)

    def _verified_owner() -> bool:
        from .entitlements import is_verified_owner
        return is_verified_owner(
            session.get('user_email'),
            session.get('user_email_verified') is True,
        )

    def _session_subscription_tier() -> str:
        tier = str(session.get('subscription_tier') or '').lower()
        if (
            subscription_ready
            and _tier_allows(tier)
            and float(session.get('subscription_checked_at') or 0) > time.time() - 300
        ):
            return tier
        return ''

    def _stripe_subscription_tier(email: str) -> str:
        """Verify an active subscription for a confirmed email.

        Stripe stays the source of truth. The five-minute session cache avoids
        an API lookup on every generation request while keeping cancellations
        reasonably prompt.
        """
        if not subscription_ready or not email:
            return ''
        try:
            import stripe
            stripe.api_key = stripe_secret_key
            customers = stripe.Customer.list(email=email, limit=10)
            best_tier = ''
            best_focus_workspace = ''
            best_period_end = 0
            for customer in customers.data:
                subscriptions = stripe.Subscription.list(
                    customer=customer.id,
                    status='all',
                    limit=100,
                    expand=['data.items.data.price'],
                )
                for item in subscriptions.data:
                    status = (
                        item.get('status', '')
                        if isinstance(item, dict)
                        else getattr(item, 'status', '')
                    )
                    if status not in {'active', 'trialing'}:
                        continue
                    item_metadata = getattr(item, 'metadata', {}) or {}
                    if isinstance(item, dict):
                        item_metadata = item.get('metadata', {}) or {}
                    candidate_focus_workspace = str(
                        item_metadata.get('freshsky_workspace')
                        or item_metadata.get('workspace_id')
                        or ''
                    ).strip().lower()
                    if isinstance(item, dict):
                        subscription_items = (
                            (item.get('items') or {}).get('data') or []
                        )
                    else:
                        subscription_items = getattr(
                            getattr(item, 'items', None),
                            'data',
                            [],
                        )
                    for sub_item in subscription_items:
                        tier = subscription_item_tier(
                            sub_item, subscription_price_id, subscription_tier
                        )
                        price = (
                            sub_item.get('price', {})
                            if isinstance(sub_item, dict)
                            else getattr(sub_item, 'price', None)
                        )
                        product_ref = (
                            price.get('product')
                            if isinstance(price, dict)
                            else getattr(price, 'product', None)
                        )
                        if not tier and isinstance(product_ref, str):
                            product = stripe.Product.retrieve(product_ref)
                            tier = subscription_item_tier(
                                sub_item,
                                subscription_price_id,
                                subscription_tier,
                                product_override=product,
                            )
                        price_id = (
                            price.get('id', '')
                            if isinstance(price, dict)
                            else getattr(price, 'id', '')
                        )
                        if (
                            tier == 'focus'
                            and not candidate_focus_workspace
                            and subscription_price_id
                        ):
                            if price_id == subscription_price_id and workspace_id:
                                candidate_focus_workspace = (
                                    focus_workspace or workspace_id
                                )
                        if (
                            _tier_allows(tier, candidate_focus_workspace or None)
                            and PLAN_RANK.get(tier, 0)
                            > PLAN_RANK.get(best_tier, 0)
                        ):
                            period_end = (
                                sub_item.get('current_period_end', 0)
                                if isinstance(sub_item, dict)
                                else getattr(sub_item, 'current_period_end', 0)
                            ) or (
                                item.get('current_period_end', 0)
                                if isinstance(item, dict)
                                else getattr(item, 'current_period_end', 0)
                            )
                            best_tier = tier
                            best_period_end = int(period_end or 0)
                            best_focus_workspace = (
                                candidate_focus_workspace
                                if tier == 'focus'
                                else ''
                            )
            if _tier_allows(best_tier, best_focus_workspace or None):
                session['subscription_tier'] = best_tier
                session['subscription_checked_at'] = time.time()
                if best_period_end > 0:
                    session['subscription_period_end'] = best_period_end
                else:
                    session.pop('subscription_period_end', None)
                if best_tier == 'focus' and best_focus_workspace:
                    session['focus_workspace'] = best_focus_workspace
                return best_tier
        except Exception as exc:
            logger.warning('Subscription verification unavailable: %s', exc)
        return ''

    def check(*, workflow_class: str = 'preview') -> Optional[tuple]:
        if not subscription_ready:
            return None
        from .runtime_policy import parse_workflow, workflow_budget
        try:
            workflow = parse_workflow(workflow_class)
            budget = workflow_budget(workflow)
        except ValueError as exc:
            return jsonify(
                error=str(exc),
                code='invalid_workflow_class',
            ), 400
        email = (session.get('user_email') or '').lower()
        entitled_tier = (
            'owner'
            if _verified_owner()
            else _session_subscription_tier() or _stripe_subscription_tier(email)
        )
        if entitled_tier:
            enforce_usage = (
                os.environ.get('FRESHSKY_ENFORCE_PAID_LIMITS', '').lower()
                in {'1', 'true', 'yes'}
                or bool(os.environ.get('K_SERVICE'))
            )
            if not enforce_usage:
                return None
            try:
                allowed, usage = _consume_paid_allowance(
                    email,
                    entitled_tier,
                    usage_hmac_key,
                    usage_units=budget.usage_units,
                    workflow_class=workflow.value,
                    workspace_id=workspace_id,
                )
            except Exception as exc:
                logger.error('Paid usage meter failed closed: %s', exc)
                return jsonify(
                    error='Usage verification is temporarily unavailable.',
                    code='usage_meter_unavailable',
                ), 503
            if allowed:
                return None
            return jsonify(
                error='Your plan usage allowance has been reached.',
                code='plan_usage_limit_reached',
                tier=entitled_tier,
                daily_limit=usage['daily'],
                monthly_limit=usage['monthly'],
                daily_used=usage['daily_used'],
                monthly_used=usage['monthly_used'],
                required_units=budget.usage_units,
                quota_unit='usage_unit',
                billing_url='/billing',
            ), 429
        if workflow.value != 'preview':
            return jsonify(
                error='This workflow requires workspace access.',
                code='workflow_requires_plan',
                tier=subscription_tier,
                price_cents=subscription_amount_cents,
                subscribe_url='/subscribe',
                login_url='/auth/google?next=/subscribe',
            ), 402
        if free_request_limit is None:
            return None
        preview_window_seconds = 30 * 24 * 60 * 60
        preview_window_started_at = float(
            session.get('free_preview_window_started_at') or 0
        )
        if (
            preview_window_started_at <= 0
            or preview_window_started_at <= time.time() - preview_window_seconds
        ):
            session['free_requests_used'] = 0
            session['free_preview_window_started_at'] = time.time()
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

    if gate_all_post:
        _ungated_post_paths = {
            '/stripe-webhook',
        }

        @app.before_request
        def freemium_global_post_gate():
            if request.method != 'POST' or request.path in _ungated_post_paths:
                return None
            return check()

    if expose_provider_metrics:
        from .llm import install_provider_metrics
        install_provider_metrics(app)

    # ─── GOOGLE OAUTH ────────────────────────────────────────────
    @app.route('/auth/google')
    def freemium_google_login():
        if not google_auth_enabled:
            return jsonify(error='Google login is not configured.'), 503
        if not redirect_uri or not google_redirect_host:
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
        session['user_email_verified'] = True
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
            return redirect(url_for('index'), code=302)
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
            if workspace_id:
                args['metadata']['workspace_id'] = workspace_id
                args['subscription_data']['metadata']['workspace_id'] = workspace_id
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
                and (
                    not workspace_id
                    or metadata.get('workspace_id') == workspace_id
                )
                and email
            )
            if not verified:
                raise ValueError('checkout did not match this application')
            session.permanent = True
            session['user_email'] = email
            session.setdefault('user_name', email.split('@')[0])
            # Checkout proves payment ownership, not Google identity.  The
            # exact owner entitlement is granted only after verified OAuth.
            session['user_email_verified'] = False
            session['subscription_tier'] = subscription_tier
            session['subscription_checked_at'] = time.time()
            if subscription_tier == 'focus' and workspace_id:
                session['focus_workspace'] = focus_workspace or workspace_id
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
                return redirect(url_for('index'), code=302)
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

    _access_bundle_path = '/freshsky-access-v060.js'

    def _freemium_js_response():
        try:
            content = (_ir.files('freshsky_common.static') / 'freemium.js').read_text(encoding='utf-8')
        except Exception:
            content = ''
        resp = Response(content, mimetype='application/javascript; charset=utf-8')
        resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return resp

    app.add_url_rule(_access_bundle_path, 'freshsky_access_bundle_v060', _freemium_js_response)

    @app.route('/freemium.js')
    def freemium_js():
        # Compatibility route for older templates. Do not cache this stable
        # path across deployments; HTML is rewritten to the versioned bundle.
        resp = _freemium_js_response()
        resp.headers['Cache-Control'] = 'no-store, max-age=0'
        return resp

    @app.after_request
    def version_freemium_bundle(response):
        """Move HTML off the historically cached stable JavaScript path."""
        if response.status_code != 200:
            return response
        if 'text/html' not in response.headers.get('Content-Type', ''):
            return response
        # Compression middleware may have already encoded the body. Rewriting
        # a compressed response as UTF-8 corrupts it and can raise at runtime.
        if response.headers.get('Content-Encoding'):
            return response
        body = response.get_data(as_text=True)
        if _access_bundle_path in body:
            return response
        for quote in ('"', "'"):
            body = body.replace(
                f'src={quote}/freemium.js{quote}',
                f'src={quote}{_access_bundle_path}{quote}',
            )
            body = body.replace(
                f'src={quote}/freemium.js?v=20260723{quote}',
                f'src={quote}{_access_bundle_path}{quote}',
            )
        if _access_bundle_path not in body and '</body>' in body:
            body = body.replace(
                '</body>',
                f'<script src="{_access_bundle_path}"></script></body>',
                1,
            )
        response.set_data(body)
        return response

    # ─── USER STATUS API ─────────────────────────────────────────
    @app.route('/api/user-status')
    def freemium_user_status():
        from .entitlements import resolve_entitlement, user_status_fields

        email = (session.get('user_email') or '').lower()
        community_request = _is_community_request()
        entitled_tier = ''
        owner_entitled = _verified_owner()
        if owner_entitled:
            entitled_tier = 'owner'
        elif email and subscription_ready:
            entitled_tier = (
                _session_subscription_tier() or _stripe_subscription_tier(email)
            )
        display_tier = entitled_tier or subscription_tier
        selected = (
            session.get('focus_workspace')
            or focus_workspace
            or None
        )
        entitlement = resolve_entitlement(
            entitled_tier or 'guest',
            selected_workspace=selected,
            email=email,
            email_verified=session.get('user_email_verified') is True,
        )
        workspace_access = (
            entitlement.can_access(workspace_id)
            if workspace_id
            else bool(entitled_tier)
        )
        workspace_full_access = bool(
            entitled_tier
            and entitlement.tier.value != 'guest'
            and workspace_access
        )
        base = {
            'logged_in': bool(email),
            'google_auth_enabled': google_auth_enabled,
            'auth_broker_enabled': bool(auth_broker_url),
            'free_access': not subscription_ready or free_request_limit is None,
            'full_access': not subscription_ready,
            'free_preview_limit': free_request_limit,
            'stripe_enabled': bool(stripe_enabled),
            'subscription_enabled': subscription_ready,
            'subscription_tier': display_tier or None,
            'required_subscription_tier': subscription_tier or None,
            'subscription_price_cents': subscription_amount_cents or None,
            'entitlement_expires_at': (
                datetime.fromtimestamp(
                    float(session.get('subscription_period_end') or 0),
                    tz=timezone.utc,
                ).isoformat()
                if entitled_tier not in {'', 'owner'}
                and float(session.get('subscription_period_end') or 0) > 0
                else None
            ),
            'paid_daily_limit': entitlement.daily_units,
            'paid_monthly_limit': entitlement.monthly_units,
            'community_mode': community_request,
        }
        if email:
            base['email'] = email
            base['name'] = session.get('user_name', '')
            if subscription_ready:
                base['full_access'] = workspace_full_access
        if subscription_ready:
            base['free_requests_used'] = int(session.get('free_requests_used') or 0)
            base['subscribe_url'] = '/subscribe'
        base.update(
            user_status_fields(
                entitlement,
                workspace=workspace_id or None,
            )
        )
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
