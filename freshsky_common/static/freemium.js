/* freshsky-common freemium UI — free-everywhere edition.

   Drop into any Fresh Sky AI app's <head>:
     <script src="/freemium.js"></script>

   What this provides:
   - A slim top user bar with optional Google sign-in + a Support link
     (sign-in is purely optional now; it raises your daily rate-limit cap)
   - A friendly 429 rate-limit message (replaces the old paywall card)
   - A hub-mark footer linking back to freshskyai.com on every sub-app
   - Optional GA4 click tracking (sign-in, support, rate-limit hits)

   Pricing is gone (free-everywhere pivot 2026-05-09). All money flow is
   one-time donations on https://www.freshskyai.com/support. This file
   does not show prices anywhere. */
(function() {
  if (window.__freemiumLoaded) return;
  window.__freemiumLoaded = true;

  var STATE = {
    is_pro: true, free_everywhere: true, logged_in: false,
    google_auth_enabled: false,
    donation_url: 'https://www.freshskyai.com/support',
  };
  var SUPPORT_URL = 'https://www.freshskyai.com/support';

  function track(event, params) {
    try {
      if (typeof window.gtag === 'function') {
        window.gtag('event', event, params || {});
      }
    } catch (e) {}
  }

  function refresh() {
    return fetch('/api/user-status')
      .then(function(r) { return r.json(); })
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

    var support =
      '<a href="' + SUPPORT_URL + '" target="_blank" rel="noopener" ' +
      'data-fs-event="support_clicked" ' +
      'style="color:#6366f1;text-decoration:none;font-weight:500;">💛 Support</a>';

    if (STATE.logged_in) {
      bar.innerHTML =
        '<span style="color:#64748b;">' +
          escapeHtml(STATE.name || STATE.email || '') +
        '</span>' +
        support +
        '<a href="/logout" style="color:#94a3b8;text-decoration:none;">Sign out</a>';
      return;
    }

    var loginBtn = STATE.google_auth_enabled
      ? '<a href="/auth/google" ' +
          'style="display:inline-flex;align-items:center;gap:6px;' +
          'background:#4285f4;color:#fff;padding:5px 14px;border-radius:4px;' +
          'text-decoration:none;font-size:13px;font-weight:500;" ' +
          'title="Optional — sign in to get a higher daily rate limit">' +
          '🔒 Sign in</a>'
      : '';

    if (!loginBtn) {
      // No login + no Pro means there's nothing useful to show — hide the bar
      // entirely so we don't leave a 40px-tall empty strip on every page.
      bar.style.display = 'none';
      document.body.style.paddingTop = '';
      return;
    }
    bar.innerHTML = loginBtn + support;
  }

  // Public: pages call this when their fetch returns, to render a friendly
  // 429 message in the tool's output area. Replaces the old paywall card.
  window.handleFreemiumResponse = function(response, outputElement) {
    if (response.status !== 429) return false;

    track('rate_limit_hit', { logged_in: !!STATE.logged_in });

    var loginNudge = (!STATE.logged_in && STATE.google_auth_enabled)
      ? ' Or <a href="/auth/google" style="color:#1a6cf5;text-decoration:underline;font-weight:600;">sign in with Google</a> for a higher daily limit.'
      : '';
    var html =
      '<div style="text-align:center;padding:24px;">' +
        '<p style="font-size:18px;font-weight:600;margin-bottom:8px;">⏳ Slow down a bit</p>' +
        '<p style="color:#475569;margin-bottom:16px;font-size:15px;line-height:1.5;">' +
          "You hit the rate limit. Please wait a few minutes and try again." +
          loginNudge +
        '</p>' +
        '<a href="' + SUPPORT_URL + '" data-fs-event="support_clicked" target="_blank" rel="noopener" ' +
          'style="display:inline-block;background:#6366f1;color:#fff;padding:11px 26px;' +
          'border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;">' +
          '💛 Support to help raise the cap' +
        '</a>' +
        '<p style="margin-top:12px;color:#94a3b8;font-size:12px;">Every Fresh Sky AI tool is free. Donations cover the running cost.</p>' +
      '</div>';
    if (outputElement) outputElement.innerHTML = html;
    refresh();
    return true;
  };

  // Auto-refresh state when any /api/ call returns 429 (so the user bar
  // stays in sync if a sibling tool already hit the limit).
  var origFetch = window.fetch.bind(window);
  window.fetch = function(input, init) {
    return origFetch(input, init).then(function(r) {
      try {
        var url = (typeof input === 'string') ? input : (input && input.url) || '';
        if (r.status === 429 && url.indexOf('/api/') === 0) refresh();
      } catch (e) {}
      return r;
    });
  };

  // Light click tracking for funnel insight (sign-in + support).
  document.addEventListener('click', function(ev) {
    var el = ev.target;
    while (el && el !== document.body) {
      if (el.tagName === 'A' || el.tagName === 'BUTTON') break;
      el = el.parentNode;
    }
    if (!el || el === document.body) return;
    var explicit = el.getAttribute && el.getAttribute('data-fs-event');
    var href = el.getAttribute && el.getAttribute('href');
    if (explicit) {
      track(explicit, { source: window.location.host });
    } else if (href && href.indexOf('/auth/google') === 0) {
      track('signup_clicked', { source: 'auth_link' });
    }
  }, true);

  // Tiny hub-mark footer linking back to freshskyai.com.
  // Auto-skips on the hub itself + on opt-out pages (<body data-fs-no-hub-mark>).
  function mountHubMark() {
    try {
      var host = (window.location && window.location.host || '').toLowerCase();
      if (host === 'www.freshskyai.com' || host === 'freshskyai.com') return;
      if (document.body && document.body.hasAttribute('data-fs-no-hub-mark')) return;
      if (document.getElementById('fs-hub-mark')) return;
      var d = document.createElement('div');
      d.id = 'fs-hub-mark';
      d.setAttribute('role', 'contentinfo');
      d.style.cssText = 'text-align:center;font:13px/1.5 system-ui,-apple-system,sans-serif;' +
        'color:#94a3b8;padding:18px 12px 22px;border-top:1px solid #e5e7eb;' +
        'margin-top:32px;background:#f8fafc;';
      d.innerHTML =
        'Part of <a href="https://www.freshskyai.com/" target="_blank" rel="noopener" ' +
          'style="color:#1a6cf5;text-decoration:none;font-weight:600;">Fresh Sky AI</a> · ' +
        '<a href="https://www.freshskyai.com/support" target="_blank" rel="noopener" ' +
          'data-fs-event="support_clicked" ' +
          'style="color:#64748b;text-decoration:underline;">💛 Support</a>';
      document.body.appendChild(d);
    } catch (e) {}
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() { refresh(); mountHubMark(); });
  } else {
    refresh();
    mountHubMark();
  }
})();
