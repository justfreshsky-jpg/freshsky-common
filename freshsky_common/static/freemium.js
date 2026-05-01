/* freshsky-common freemium UI — drop into any batch app's <head>:
     <script src="/static/freemium.js"></script>
   Expects the page to follow the standard pattern: each generation
   button lives inside a tool card; outputs live in the same card; the
   global fetch() call passes through window.handleFreemiumResponse().

   Provides:
   - A persistent user bar at the top of the page showing free-tier
     usage and a Sign-in / Upgrade button
   - A 429 paywall card that replaces the tool's output area when the
     daily free limit is hit (with $price/mo + $price/yr CTAs)

   Designed to be additive: existing inline scripts keep working. The
   wrapper hooks into window.fetch only for /api/* POSTs to the host
   origin. */
(function() {
  if (window.__freemiumLoaded) return;
  window.__freemiumLoaded = true;

  var STATE = { is_pro: false, usage_today: 0, daily_limit: 10,
                stripe_enabled: false, logged_in: false,
                google_auth_enabled: false, pro_monthly_dollars: '$1.99',
                pro_yearly_dollars: '$19' };

  function refresh() {
    return fetch('/api/user-status').then(function(r) { return r.json(); })
      .then(function(s) { Object.assign(STATE, s); renderBar(); })
      .catch(function() {});
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
    });
  }

  function renderBar() {
    var bar = document.getElementById('freemium-bar');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'freemium-bar';
      bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;' +
        'display:flex;align-items:center;justify-content:flex-end;gap:10px;' +
        'padding:6px 16px;background:rgba(255,255,255,.95);' +
        'backdrop-filter:blur(6px);border-bottom:1px solid #e2e8f0;' +
        'font-size:13px;font-family:system-ui,-apple-system,sans-serif;';
      document.body.prepend(bar);
      document.body.style.paddingTop = '40px';
    }
    var info, actions = '';
    if (STATE.logged_in) {
      info = STATE.is_pro
        ? '⭐ ' + escapeHtml(STATE.pro_pricing_label || 'Pro') + ' — all Fresh Sky AI tools'
        : '✨ Free (' + STATE.usage_today + '/' + STATE.daily_limit + ' today)';
      actions = STATE.is_pro
        ? '<a href="/billing" style="color:#6366f1;text-decoration:none;font-weight:500">Manage</a>'
        : (STATE.stripe_enabled
            ? '<a href="/subscribe" style="background:#6366f1;color:#fff;padding:4px 12px;border-radius:4px;text-decoration:none;font-weight:500">Upgrade</a>'
            : '');
      bar.innerHTML = '<span style="color:#64748b">' +
        escapeHtml(STATE.name || STATE.email || '') + ' — ' + info + '</span>' +
        actions +
        '<a href="/logout" style="color:#94a3b8;text-decoration:none">Sign out</a>';
    } else {
      var anonInfo = (typeof STATE.daily_limit === 'number')
        ? '<span style="color:#64748b;margin-right:6px">✨ Free (' +
          STATE.usage_today + '/' + STATE.daily_limit + ' today)</span>'
        : '';
      var loginBtn = STATE.google_auth_enabled
        ? '<a href="/auth/google" style="display:inline-flex;align-items:center;gap:6px;background:#4285f4;color:#fff;padding:5px 14px;border-radius:4px;text-decoration:none;font-size:13px;font-weight:500">🔒 Sign in with Google</a>'
        : '';
      bar.innerHTML = anonInfo + loginBtn;
    }
  }

  // Public: pages call this when their fetch returns to handle 429 paywalls.
  window.handleFreemiumResponse = function(response, outputElement) {
    if (response.status === 429) {
      var html = '<div style="text-align:center;padding:24px">' +
        '<p style="font-size:18px;font-weight:600;margin-bottom:8px">⚡ Daily free limit reached</p>' +
        '<p style="color:#475569;margin-bottom:6px;font-size:15px">' +
          '<strong>One Pro subscription = unlimited access on every Fresh Sky AI tool.</strong>' +
        '</p>' +
        '<p style="color:#64748b;margin-bottom:16px;font-size:13px">' +
          '32+ apps for civic, legal, healthcare, immigration, and benefits — covered by the same plan.' +
        '</p>';
      if (STATE.stripe_enabled) {
        html += '<div style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap">' +
          '<a href="/subscribe" style="display:inline-block;background:#6366f1;color:#fff;padding:10px 24px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:500">' +
            (STATE.pro_pricing_label || 'Pro') + ' — ' + STATE.pro_monthly_dollars + '/mo</a>' +
          '<a href="/subscribe/yearly" style="display:inline-block;background:#059669;color:#fff;padding:10px 24px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:500">' +
            'Yearly — ' + STATE.pro_yearly_dollars + '/yr <span style="opacity:.85;font-size:12px">(save ~20%)</span></a>' +
          '</div>' +
          '<p style="margin-top:14px;color:#94a3b8;font-size:12px">Cancel anytime. Free Google sign-in first.</p>';
      } else {
        html += '<p style="color:#94a3b8;font-size:12px">Try again tomorrow — paid plan not yet enabled.</p>';
      }
      html += '</div>';
      if (outputElement) outputElement.innerHTML = html;
      // Refresh user bar to update counter
      refresh();
      return true; // handled
    }
    return false; // not handled
  };

  // Best-effort: also auto-wrap fetch so legacy inline scripts don't have
  // to opt in. We only intervene on 429 from same-origin /api/ POSTs.
  var origFetch = window.fetch.bind(window);
  window.fetch = function(input, init) {
    return origFetch(input, init).then(function(r) {
      try {
        var url = (typeof input === 'string') ? input : (input && input.url) || '';
        if (r.status === 429 && url.indexOf('/api/') === 0) {
          // Increment-counter side effect: just refresh the bar
          refresh();
        }
      } catch (e) {}
      return r;
    });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', refresh);
  } else {
    refresh();
  }
})();
