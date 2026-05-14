"""
Multimodal input helpers — voice + image for the AI-decade portfolio.

Voice: NFIRSAssistant proved the pattern — browser-side Web Speech API,
zero server cost, works on Chrome / Edge / Safari mobile. This module
packages it as a one-liner any app can install.

Image: a small drag-and-drop / file-picker widget that lets the user
attach a photo of the document they're asking about. The bytes are
posted to `/api/multimodal/image` which the app overrides to do
whatever LLM-vision call it wants (Cloudflare Workers AI llava, Gemini
Flash Vision via OpenRouter, etc.). The default handler echoes a
"vision not configured" message — keeps the install non-breaking.

Wiring:

    from freshsky_common.multimodal import install_voice, install_image
    install_voice(app, target_selector='#narrative', label='Tap to speak')
    install_image(app, target_selector='#narrative')

Both injectors auto-append script + style + button HTML right before
`</body>` on text/html responses. They're idempotent (marker check)
and gated to text/html only.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Callable, Optional

from flask import Flask, jsonify, request

logger = logging.getLogger(__name__)


_VOICE_MARKER = 'fs-multimodal-voice'
_IMAGE_MARKER = 'fs-multimodal-image'


def _voice_html(target_selector: str, label: str) -> str:
    sel_js = json.dumps(target_selector)
    label_js = json.dumps(label)
    return f"""
<button id="{_VOICE_MARKER}" type="button" aria-label="Voice input"
  style="position:fixed;bottom:14px;right:80px;z-index:99995;
  padding:10px 14px;border-radius:999px;border:1px solid rgba(99,102,241,.35);
  background:rgba(6,9,26,0.78);-webkit-backdrop-filter:blur(14px);
  backdrop-filter:blur(14px);color:#cbd5e1;font-size:12px;font-weight:600;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.25);display:none;">
  🎙 <span id="{_VOICE_MARKER}-label">{label}</span>
</button>
<script>
(function() {{
  if (window.__FS_VOICE_LOADED__) return;
  window.__FS_VOICE_LOADED__ = true;
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const btn = document.getElementById({json.dumps(_VOICE_MARKER)});
  const lbl = document.getElementById({json.dumps(_VOICE_MARKER + '-label')});
  if (!SR || !btn) return;
  function findTarget() {{
    const sel = {sel_js};
    if (sel) {{
      const el = document.querySelector(sel);
      if (el) return el;
    }}
    // Fall back to the largest visible textarea on the page.
    const tas = Array.from(document.querySelectorAll('textarea, input[type=text]'))
      .filter(t => t.offsetParent !== null);
    tas.sort((a, b) => (b.offsetWidth * b.offsetHeight) - (a.offsetWidth * a.offsetHeight));
    return tas[0] || null;
  }}
  const target = findTarget();
  if (!target) return;
  btn.style.display = 'inline-flex';
  let rec = null, recording = false;
  btn.onclick = function() {{
    if (recording) {{ rec && rec.stop(); return; }}
    rec = new SR();
    rec.lang = (document.documentElement.lang || 'en') + '-' + (navigator.language || 'en-US').split('-')[1] || 'US';
    try {{ rec.lang = navigator.language || 'en-US'; }} catch (e) {{}}
    rec.continuous = true;
    rec.interimResults = true;
    let finalT = target.value ? target.value.trimEnd() + ' ' : '';
    rec.onresult = function(e) {{
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {{
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalT += t + ' ';
        else interim += t;
      }}
      target.value = (finalT + interim).trimStart();
      target.dispatchEvent(new Event('input', {{bubbles: true}}));
    }};
    rec.onend = function() {{ recording = false; btn.style.background = 'rgba(6,9,26,0.78)'; lbl.textContent = {label_js}; }};
    rec.onerror = function(e) {{ recording = false; btn.style.background = 'rgba(6,9,26,0.78)'; lbl.textContent = {label_js}; }};
    recording = true;
    btn.style.background = 'rgba(239,68,68,0.42)';
    lbl.textContent = 'Listening…';
    rec.start();
  }};
}})();
</script>
"""


def install_voice(
    app: Flask,
    *,
    target_selector: str = '',
    label: str = 'Tap to speak',
) -> None:
    """Inject a voice-input mic button into every HTML response.

    `target_selector` is a CSS selector for the textarea/input to fill.
    Empty string → the largest visible textarea on the page (heuristic
    works for the 32 Fresh Sky apps; override per app when needed).
    """
    html = _voice_html(target_selector, label)

    @app.after_request
    def _inject_voice(resp):
        ct = (resp.content_type or '').lower()
        if 'text/html' not in ct:
            return resp
        if getattr(resp, 'direct_passthrough', False):
            return resp
        try:
            body = resp.get_data(as_text=True)
        except Exception:
            return resp
        if _VOICE_MARKER in body or '</body>' not in body:
            return resp
        resp.set_data(body.replace('</body>', html + '</body>', 1))
        return resp


def _image_html(target_selector: str, max_mb: int) -> str:
    sel_js = json.dumps(target_selector)
    return f"""
<button id="{_IMAGE_MARKER}-btn" type="button" aria-label="Attach image"
  style="position:fixed;bottom:14px;right:160px;z-index:99995;
  padding:10px 14px;border-radius:999px;border:1px solid rgba(99,102,241,.35);
  background:rgba(6,9,26,0.78);-webkit-backdrop-filter:blur(14px);
  backdrop-filter:blur(14px);color:#cbd5e1;font-size:12px;font-weight:600;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.25);">
  📎 <span id="{_IMAGE_MARKER}-label">Photo of doc</span>
</button>
<input id="{_IMAGE_MARKER}-file" type="file" accept="image/*,application/pdf" style="display:none">
<script>
(function() {{
  if (window.__FS_IMAGE_LOADED__) return;
  window.__FS_IMAGE_LOADED__ = true;
  const btn = document.getElementById({json.dumps(_IMAGE_MARKER + '-btn')});
  const lbl = document.getElementById({json.dumps(_IMAGE_MARKER + '-label')});
  const inp = document.getElementById({json.dumps(_IMAGE_MARKER + '-file')});
  if (!btn || !inp) return;
  function findTarget() {{
    const sel = {sel_js};
    if (sel) {{ const el = document.querySelector(sel); if (el) return el; }}
    const tas = Array.from(document.querySelectorAll('textarea, input[type=text]'))
      .filter(t => t.offsetParent !== null);
    tas.sort((a, b) => (b.offsetWidth * b.offsetHeight) - (a.offsetWidth * a.offsetHeight));
    return tas[0] || null;
  }}
  btn.onclick = () => inp.click();
  inp.onchange = async () => {{
    const f = inp.files && inp.files[0];
    if (!f) return;
    if (f.size > {max_mb} * 1024 * 1024) {{ lbl.textContent = 'Too big ({max_mb}MB max)'; setTimeout(() => lbl.textContent = 'Photo of doc', 2500); return; }}
    const target = findTarget();
    if (!target) return;
    lbl.textContent = 'Reading…';
    const fd = new FormData();
    fd.append('file', f);
    try {{
      const r = await fetch('/api/multimodal/image', {{method:'POST', body:fd}});
      const j = await r.json();
      if (j.text) {{
        target.value = (target.value ? target.value.trimEnd() + '\\n\\n' : '') + j.text;
        target.dispatchEvent(new Event('input', {{bubbles: true}}));
        lbl.textContent = 'Added';
      }} else {{
        lbl.textContent = j.error || 'Failed';
      }}
    }} catch (e) {{
      lbl.textContent = 'Failed';
    }}
    setTimeout(() => lbl.textContent = 'Photo of doc', 2500);
    inp.value = '';
  }};
}})();
</script>
"""


def install_image(
    app: Flask,
    *,
    target_selector: str = '',
    max_mb: int = 2,
    handler: Optional[Callable[[bytes, str], dict]] = None,
) -> None:
    """Inject an image-attach button + register POST /api/multimodal/image.

    `handler(bytes_data, mimetype) -> {"text": str}` is the override the
    app provides to do the actual vision call. If omitted, the endpoint
    returns a 501 explaining vision isn't configured — the UI shows the
    error to the user without crashing.
    """
    html = _image_html(target_selector, max_mb)

    @app.after_request
    def _inject_image(resp):
        ct = (resp.content_type or '').lower()
        if 'text/html' not in ct:
            return resp
        if getattr(resp, 'direct_passthrough', False):
            return resp
        try:
            body = resp.get_data(as_text=True)
        except Exception:
            return resp
        if _IMAGE_MARKER in body or '</body>' not in body:
            return resp
        resp.set_data(body.replace('</body>', html + '</body>', 1))
        return resp

    @app.route('/api/multimodal/image', methods=['POST'])
    def _image_endpoint():
        f = request.files.get('file')
        if not f:
            return jsonify(error='No file uploaded.'), 400
        data = f.read()
        if len(data) > max_mb * 1024 * 1024:
            return jsonify(error=f'File too large (>{max_mb}MB).'), 413
        if handler is None:
            return jsonify(
                error='Image OCR not configured for this app yet — type or paste the text instead.',
            ), 501
        try:
            out = handler(data, f.mimetype or 'application/octet-stream')
            if not isinstance(out, dict) or 'text' not in out:
                return jsonify(error='Image handler returned bad shape.'), 500
            return jsonify(out), 200
        except Exception as e:
            logger.exception('multimodal: image handler raised')
            return jsonify(error=f'Image read failed: {e}'), 500
