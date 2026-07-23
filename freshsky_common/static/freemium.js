/* Shared Fresh Sky AI free-access UI. */
(function() {
  if (window.__freemiumLoaded) return;
  window.__freemiumLoaded = true;

  var STATE = {
    free_access: true,
    full_access: true,
    logged_in: false,
    google_auth_enabled: false,
    subscription_enabled: false,
    donate_url: 'https://www.freshskyai.com/donate'
  };
  var DONATE_URL = STATE.donate_url;

  function track(event, params) {
    try {
      if (typeof window.gtag === 'function') window.gtag('event', event, params || {});
    } catch (e) {}
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function(c) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
    });
  }

  function refresh() {
    return fetch('/api/user-status')
      .then(function(response) { return response.json(); })
      .then(function(state) {
        Object.assign(STATE, state);
        DONATE_URL = STATE.donate_url || STATE.sponsor_url || DONATE_URL;
        renderBar();
      })
      .catch(function() {});
  }

  function donateLink() {
    return '<a href="' + DONATE_URL + '" target="_blank" rel="noopener" ' +
      'data-fs-event="donate_clicked" ' +
      'style="display:inline-flex;align-items:center;background:linear-gradient(135deg,#0f9f6e,#1267d8);' +
      'color:#fff;padding:4px 12px;border-radius:6px;text-decoration:none;font-weight:600;font-size:12.5px;">' +
      'Donate</a>';
  }

  function planLink() {
    var dollars = ((STATE.subscription_price_cents || 0) / 100).toFixed(2);
    return '<a href="' + (STATE.subscribe_url || '/subscribe') + '" ' +
      'data-fs-event="subscription_clicked" ' +
      'style="display:inline-flex;align-items:center;background:linear-gradient(135deg,#5ee7f7,#7c8cff);' +
      'color:#06101f;padding:5px 12px;border-radius:7px;text-decoration:none;font-weight:800;font-size:12.5px;">' +
      'Unlock · $' + dollars + '/mo</a>';
  }

  function renderBar() {
    var bar = document.getElementById('freemium-bar');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'freemium-bar';
      bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;' +
        'display:flex;align-items:center;justify-content:flex-end;gap:10px;' +
        'padding:6px 16px;background:rgba(10,14,39,0.92);' +
        '-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);' +
        'border-bottom:1px solid rgba(99,102,241,0.15);font-size:13px;color:#cbd5e1;' +
        'font-family:Inter,system-ui,-apple-system,sans-serif;';
      document.body.prepend(bar);
      document.body.style.paddingTop = '38px';
    }

    var access = STATE.subscription_enabled
      ? (STATE.full_access
          ? '<span style="color:#67e8f9;font-weight:700;font-size:12.5px;">Plan active</span>'
          : '<span style="color:#cbd5e1;font-weight:600;font-size:12.5px;">Free preview</span>')
      : '<span style="color:#4ade80;font-weight:600;font-size:12.5px;">Full access is free</span>';
    var action = STATE.subscription_enabled ? planLink() : donateLink();
    if (STATE.logged_in) {
      bar.innerHTML =
        '<span style="color:#94a3b8;">' + escapeHtml(STATE.name || STATE.email || '') + '</span>' +
        access + action +
        '<a href="/logout" style="color:#64748b;text-decoration:none;font-size:12.5px;">Sign out</a>';
      return;
    }
    bar.innerHTML = access + action +
      (STATE.google_auth_enabled
        ? '<a href="/auth/google" style="color:#cbd5e1;text-decoration:none;font-size:12.5px;">Sign in</a>'
        : '');
  }

  window.handleFreemiumResponse = function(response, outputElement) {
    if (response.status !== 429) return false;
    track('rate_limit_hit', { logged_in: !!STATE.logged_in });
    if (outputElement) {
      outputElement.innerHTML =
        '<div style="text-align:center;padding:24px;">' +
          '<p style="font-size:18px;font-weight:600;margin-bottom:8px;color:#1e293b;">Temporarily unavailable</p>' +
          '<p style="color:#475569;font-size:15px;line-height:1.5;">' +
            'The AI provider or a security safeguard is temporarily limiting requests. Full Fresh Sky AI access is free; please try again shortly.' +
          '</p>' +
        '</div>';
    }
    return true;
  };

  document.addEventListener('click', function(ev) {
    var el = ev.target;
    while (el && el !== document.body && el.tagName !== 'A' && el.tagName !== 'BUTTON') {
      el = el.parentNode;
    }
    if (!el || el === document.body) return;
    var explicit = el.getAttribute && el.getAttribute('data-fs-event');
    var href = el.getAttribute && el.getAttribute('href');
    if (explicit) track(explicit, { source: window.location.host });
    else if (href && href.indexOf('/auth/google') === 0) track('signup_clicked', { source: 'auth_link' });
  }, true);

  function mountHubMark() {
    try {
      var host = (window.location && window.location.host || '').toLowerCase();
      if (host === 'www.freshskyai.com' || host === 'freshskyai.com') return;
      if (document.body && document.body.hasAttribute('data-fs-no-hub-mark')) return;
      if (document.getElementById('fs-hub-mark')) return;
      var mark = document.createElement('div');
      mark.id = 'fs-hub-mark';
      mark.setAttribute('role', 'contentinfo');
      mark.style.cssText = 'text-align:center;font:13px/1.5 system-ui,-apple-system,sans-serif;' +
        'color:#94a3b8;padding:18px 12px 22px;border-top:1px solid #e5e7eb;' +
        'margin-top:32px;background:#f8fafc;';
      mark.innerHTML =
        'Part of <a href="https://www.freshskyai.com/" target="_blank" rel="noopener" ' +
          'style="color:#6366f1;text-decoration:none;font-weight:600;">Fresh Sky AI</a> · ' +
        (STATE.subscription_enabled
          ? 'Monthly plan available · <a href="/subscribe" data-fs-event="subscription_clicked" style="color:#5ee7f7;text-decoration:underline;font-weight:700;">View plan</a>'
          : 'Full access is free · <a href="' + DONATE_URL + '" target="_blank" rel="noopener" data-fs-event="donate_clicked" style="color:#5ee7f7;text-decoration:underline;font-weight:600;">Donate</a>');
      document.body.appendChild(mark);
    } catch (e) {}
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() { refresh(); mountHubMark(); });
  } else {
    refresh();
    mountHubMark();
  }
})();
