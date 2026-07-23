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

  function installVisualSystem() {
    if (document.getElementById('freshsky-visual-system')) return;
    var link = document.createElement('link');
    link.id = 'freshsky-visual-system';
    link.rel = 'stylesheet';
    link.href = '/freshsky.css';
    document.head.appendChild(link);
  }

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

  function communityLink() {
    return '<a href="https://www.freshskyai.com/#why" target="_blank" rel="noopener" ' +
      'class="fs-access-link">HULEC + halal standard</a>';
  }

  function planLink() {
    var dollars = ((STATE.subscription_price_cents || 0) / 100).toFixed(2);
    return '<a href="' + (STATE.subscribe_url || '/subscribe') + '" ' +
      'data-fs-event="subscription_clicked" class="fs-access-cta">' +
      'Unlock · $' + dollars + '/month</a>';
  }

  function renderBar() {
    var bar = document.getElementById('freemium-bar');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'freemium-bar';
      bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;' +
        'padding:6px 16px;background:rgba(7,20,38,0.95);' +
        '-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);' +
        'border-bottom:1px solid rgba(125,150,210,0.18);color:#cbd5e1;' +
        'font-family:Inter,system-ui,-apple-system,sans-serif;';
      document.body.prepend(bar);
      document.body.style.paddingTop = '54px';
    }

    var host = (window.location && window.location.host || '').toLowerCase();
    var mainCatalog = host === 'www.freshskyai.com' || host === 'freshskyai.com';
    var access = STATE.subscription_enabled
      ? (STATE.full_access
          ? '<span class="fs-access-state" data-active="true">Plan active</span>'
          : '<span class="fs-access-state">' +
              String(STATE.daily_limit || 3) + ' free AI runs</span>')
      : (mainCatalog
          ? '<span class="fs-access-state" data-active="true">Catalog · 3 free AI previews</span>'
          : '<span class="fs-access-state" data-active="true">' +
              (STATE.community_mode ? '3 free civic previews' : '3 free previews') + '</span>');
    var action = STATE.subscription_enabled ? planLink() : communityLink();
    var user = STATE.logged_in
      ? '<span class="fs-access-user">' +
          escapeHtml(STATE.name || STATE.email || '') + '</span>'
      : '';
    var account = STATE.logged_in
      ? '<a href="/logout" class="fs-access-link">Sign out</a>'
      : (STATE.google_auth_enabled
          ? '<a href="/auth/google" class="fs-access-link">Sign in</a>'
          : '');

    bar.innerHTML =
      '<div class="fs-access-shell">' +
        '<a class="fs-access-brand" href="https://www.freshskyai.com/" ' +
          'target="_blank" rel="noopener">Fresh Sky AI</a>' +
        '<div class="fs-access-actions">' + user + access + action + account + '</div>' +
      '</div>';
  }

  window.handleFreemiumResponse = function(response, outputElement) {
    if (response.status !== 402 && response.status !== 429) return false;
    track('rate_limit_hit', { logged_in: !!STATE.logged_in });
    if (outputElement) {
      if (response.status === 402 && STATE.subscription_enabled) {
        outputElement.innerHTML =
          '<div style="text-align:center;padding:24px;">' +
            '<p style="font-size:18px;font-weight:750;margin-bottom:8px;color:#1e293b;">' +
              'Your free preview is complete</p>' +
            '<p style="color:#475569;font-size:15px;line-height:1.5;">' +
              'Continue this focused workflow with the monthly plan. Cancel from your billing portal at any time.</p>' +
            '<p style="margin-top:14px;">' + planLink() + '</p>' +
          '</div>';
      } else {
        outputElement.innerHTML =
          '<div style="text-align:center;padding:24px;">' +
            '<p style="font-size:18px;font-weight:750;margin-bottom:8px;color:#1e293b;">Temporarily unavailable</p>' +
            '<p style="color:#475569;font-size:15px;line-height:1.5;">' +
              'A provider or safety limit is temporarily busy. Please try again shortly; this did not use another preview run.</p>' +
          '</div>';
      }
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
      mark.style.cssText = 'text-align:center;padding:22px 14px 26px;margin-top:32px;';
      mark.innerHTML =
        '<div class="fs-hub-mark-inner">' +
          '<strong>Fresh Sky AI</strong> · Human-centered · Unique · Legal · Efficient · Cheap<br>' +
          'HULEC + halal-conscious: transparent monthly pricing, no hidden fees, no interest-based financing, no gambling mechanics, no sale of prompt data, and no religious-certification claim. ' +
          (STATE.subscription_enabled
            ? '<a href="/subscribe" data-fs-event="subscription_clicked">View this app&rsquo;s monthly plan</a>'
            : '<a href="https://www.freshskyai.com/#why" target="_blank" rel="noopener">Read the HULEC standard</a>') +
        '</div>';
      document.body.appendChild(mark);
    } catch (e) {}
  }

  if (document.readyState === 'loading') {
    installVisualSystem();
    document.addEventListener('DOMContentLoaded', function() {
      refresh().then(mountHubMark).catch(mountHubMark);
    });
  } else {
    installVisualSystem();
    refresh().then(mountHubMark).catch(mountHubMark);
  }
})();
