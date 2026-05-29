/* freshsky-common freemium UI — Pro tier edition.

   Drop into any Fresh Sky AI app's <head>:
     <script src="/freemium.js"></script>

   What this provides:
   - A slim top user bar with Google sign-in + Pro pill + daily usage
   - "Sponsor $5+" CTA that links to https://www.freshskyai.com/sponsor
   - A friendly 429 rate-limit message that pitches the upgrade
   - Optional GA4 click tracking (sign-in, upgrade, rate-limit hits)

   Pro tier (restored 2026-05-11):
   - $1.99/mo or $19.99/yr unlocks unlimited use across Fresh Sky AI tools
   - Free anonymous: ~10 queries/day per IP
   - Free signed-in: ~20 queries/day per user
   - Pro: unlimited (2000/day soft cap) */
(function() {
  if (window.__freemiumLoaded) return;
  window.__freemiumLoaded = true;

  var STATE = {
    is_pro: false, logged_in: false,
    google_auth_enabled: false,
    usage_today: 0, daily_limit: 10,
    community_mode: false,
    pricing_url: 'https://www.freshskyai.com/pricing',
    sponsor_url: 'https://www.freshskyai.com/sponsor',
  };
  var PRICING_URL = 'https://www.freshskyai.com/pricing';
  var SPONSOR_URL = 'https://www.freshskyai.com/sponsor';

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
      .then(function(s) {
        Object.assign(STATE, s);
        if (STATE.sponsor_url) SPONSOR_URL = STATE.sponsor_url;
        renderBar();
      })
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
        'padding:6px 16px;background:rgba(10,14,39,0.92);' +
        '-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);' +
        'border-bottom:1px solid rgba(99,102,241,0.15);' +
        'font-size:13px;color:#cbd5e1;' +
        'font-family:Inter,system-ui,-apple-system,sans-serif;';
      document.body.prepend(bar);
      document.body.style.paddingTop = '38px';
    }
    var sponsorPill = STATE.community_mode
      ? ''  // civic apps: no upsell, just sign-in
      : '<a href="' + SPONSOR_URL + '" target="_blank" rel="noopener" ' +
        'data-fs-event="sponsor_clicked" ' +
        'style="display:inline-flex;align-items:center;gap:6px;' +
        'background:linear-gradient(135deg,#0f9f6e,#1267d8);color:#fff;' +
        'padding:4px 12px;border-radius:6px;text-decoration:none;font-weight:600;' +
        'font-size:12.5px;box-shadow:0 0 12px rgba(15,159,110,0.35);">' +
        'Sponsor $5+</a>';
    if (STATE.logged_in && STATE.is_pro) {
      bar.innerHTML =
        '<span style="color:#94a3b8;">' + escapeHtml(STATE.name || STATE.email || '') + '</span>' +
        '<span style="display:inline-flex;align-items:center;gap:4px;' +
          'background:rgba(34,197,94,0.15);color:#4ade80;' +
          'border:1px solid rgba(34,197,94,0.3);padding:3px 10px;border-radius:6px;' +
          'font-weight:600;font-size:12px;">✓ Pro</span>' +
        '<a href="/billing" style="color:#94a3b8;text-decoration:none;font-size:12.5px;">Manage</a>' +
        '<a href="/logout" style="color:#64748b;text-decoration:none;font-size:12.5px;">Sign out</a>';
      return;
    }
    if (STATE.logged_in) {
      var usage = (STATE.usage_today != null && STATE.daily_limit)
        ? '<span style="color:#94a3b8;font-size:12px;">' + STATE.usage_today + '/' + STATE.daily_limit + ' today</span>'
        : '';
      bar.innerHTML =
        '<span style="color:#94a3b8;">' + escapeHtml(STATE.name || STATE.email || '') + '</span>' +
        usage + sponsorPill +
        '<a href="/logout" style="color:#64748b;text-decoration:none;font-size:12.5px;">Sign out</a>';
      return;
    }
    if (STATE.google_auth_enabled) {
      bar.innerHTML = sponsorPill +
        '<a href="/auth/google" style="display:inline-flex;align-items:center;gap:6px;' +
          'background:rgba(255,255,255,0.06);color:#cbd5e1;border:1px solid rgba(255,255,255,0.12);' +
          'padding:4px 12px;border-radius:6px;text-decoration:none;font-size:12.5px;font-weight:500;">' +
          '🔒 Sign in</a>';
      return;
    }
    bar.innerHTML = sponsorPill;
  }

  // Public: pages call this when their fetch returns, to render a friendly
  // 429 message in the tool's output area. Replaces the old paywall card.
  window.handleFreemiumResponse = function(response, outputElement) {
    if (response.status !== 429) return false;

    track('rate_limit_hit', { logged_in: !!STATE.logged_in });

    var signInNudge = (!STATE.logged_in && STATE.google_auth_enabled)
      ? ' Or <a href="/auth/google" style="color:#6366f1;text-decoration:underline;font-weight:600;">sign in</a> for a higher daily cap.'
      : '';
    var html;
    if (STATE.community_mode) {
      html =
        '<div style="text-align:center;padding:24px;">' +
          '<p style="font-size:18px;font-weight:600;margin-bottom:8px;color:#1e293b;">⏳ Daily limit reached</p>' +
          '<p style="color:#475569;margin-bottom:18px;font-size:15px;line-height:1.5;">' +
            "Free for fire/EMS, CAP, CERT, CASA — always. Try again tomorrow." +
            signInNudge +
          '</p>' +
          '<p style="margin-top:12px;color:#94a3b8;font-size:12px;">These civic tools stay 100% free for the people who show up.</p>' +
        '</div>';
    } else {
      html =
        '<div style="text-align:center;padding:24px;">' +
          '<p style="font-size:18px;font-weight:600;margin-bottom:8px;color:#1e293b;">⚡ Daily limit reached</p>' +
          '<p style="color:#475569;margin-bottom:18px;font-size:15px;line-height:1.5;">' +
            "You've hit today's free cap. Pro unlocks unlimited use across every Fresh Sky AI tool." +
            signInNudge +
          '</p>' +
          '<a href="' + PRICING_URL + '" data-fs-event="upgrade_clicked" target="_blank" rel="noopener" ' +
            'style="display:inline-flex;align-items:center;gap:6px;background:linear-gradient(135deg,#6366f1,#8b5cf6);' +
            'color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14.5px;font-weight:600;' +
            'box-shadow:0 4px 20px rgba(99,102,241,0.35);">' +
            '⚡ Get Pro — $19.99/yr or $1.99/mo' +
          '</a>' +
          '<p style="margin-top:12px;color:#94a3b8;font-size:12px;">One subscription unlocks every Fresh Sky AI tool.</p>' +
          '<p style="margin-top:8px;color:#94a3b8;font-size:12px;">' +
            'Want to support free access instead? <a href="' + SPONSOR_URL + '" target="_blank" rel="noopener" ' +
            'data-fs-event="sponsor_clicked" style="color:#6366f1;text-decoration:underline;font-weight:600;">Sponsor Fresh Sky AI</a>.' +
          '</p>' +
        '</div>';
    }
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
          'style="color:#6366f1;text-decoration:none;font-weight:600;">Fresh Sky AI</a> · ' +
        '<a href="' + SPONSOR_URL + '" target="_blank" rel="noopener" ' +
          'data-fs-event="sponsor_clicked" ' +
          'style="color:#0f766e;text-decoration:underline;font-weight:600;">Sponsor $5+</a>';
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
