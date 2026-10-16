// Single bottom ad banner — only on whitelisted pages, OFF by default.
//
// ADS_ENABLED = false  → the slot stays hidden for everyone.
// ?ads=preview         → shows a grey placeholder in this one page load only,
//                        so the layout can be checked on a real phone.
//
// No ad network script is loaded from here. Wiring in real AdSense (plus a
// consent banner and an updated adatkezeles.html) is a separate, explicitly
// approved step.

const ADS_ENABLED = false;
const AD_ALLOWED_PATHS = ['/', '/join', '/create'];

function adSlotMode(pathname, search, enabled) {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname;
  if (!AD_ALLOWED_PATHS.includes(path)) return 'off';
  if (new URLSearchParams(search).get('ads') === 'preview') return 'preview';
  return enabled ? 'placeholder' : 'off';
}

function initAdSlot() {
  const slot = document.getElementById('adSlot');
  if (!slot) return;
  const mode = adSlotMode(location.pathname, location.search, ADS_ENABLED);
  if (mode === 'off') return;
  slot.querySelector('.ad-slot-box').textContent =
    mode === 'preview' ? 'Hirdetés helye (előnézet)' : '';
  slot.hidden = false;
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAdSlot);
  } else {
    initAdSlot();
  }
}
