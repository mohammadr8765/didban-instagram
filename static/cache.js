(function () {
  var SNAP_KEY = "didban_snap_v1";
  var THUMB_PREFIX = "didban_thumb_";

  function load() {
    try {
      var s = JSON.parse(localStorage.getItem(SNAP_KEY) || "null");
      if (s && typeof s === "object" && s.accounts) return s;
    } catch (e) {}
    return { accounts: {}, last_check: null };
  }
  function save(s) {
    try { localStorage.setItem(SNAP_KEY, JSON.stringify(s)); } catch (e) {}
  }
  function ts(l) {
    var t = l && l.date_utc ? Date.parse(l.date_utc) : NaN;
    return isNaN(t) ? 0 : t;
  }
  function getThumb(shortcode) {
    try { return localStorage.getItem(THUMB_PREFIX + shortcode); } catch (e) { return null; }
  }

  // Download the server thumbnail once, shrink it and keep it on the phone.
  function cacheThumb(latest) {
    if (!latest || !latest.thumb_url || !latest.shortcode) return;
    if (getThumb(latest.shortcode)) return;
    var img = new Image();
    img.onload = function () {
      try {
        var max = 320;
        var w = img.naturalWidth, h = img.naturalHeight;
        var k = Math.min(1, max / Math.max(w, h));
        var c = document.createElement("canvas");
        c.width = Math.max(1, Math.round(w * k));
        c.height = Math.max(1, Math.round(h * k));
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        localStorage.setItem(THUMB_PREFIX + latest.shortcode, c.toDataURL("image/jpeg", 0.7));
      } catch (e) {}
    };
    img.src = latest.thumb_url;
  }

  // Merge the server answer with what this phone remembers, so a wiped server
  // never makes the latest-post info disappear.
  function merge(data) {
    var snap = load();
    var next = { accounts: {}, last_check: snap.last_check };
    var keepThumbs = {};

    data.accounts.forEach(function (a) {
      var old = snap.accounts[a.username] || {};
      var cur = a.latest || null;
      var prev = old.latest || null;
      var best = cur && prev ? (ts(prev) > ts(cur) ? prev : cur) : (cur || prev);
      var initialized = !!(a.initialized || old.initialized);

      next.accounts[a.username] = { latest: best, initialized: initialized };

      if (best) {
        cacheThumb(best);
        keepThumbs[best.shortcode] = true;
        a.latest = Object.assign({}, best, { thumb_data: getThumb(best.shortcode) });
      }
      a.initialized = initialized;
    });

    if (data.last_check) next.last_check = data.last_check;
    else data.last_check = next.last_check;

    save(next);

    try {
      for (var i = localStorage.length - 1; i >= 0; i--) {
        var key = localStorage.key(i);
        if (key && key.indexOf(THUMB_PREFIX) === 0 && !keepThumbs[key.slice(THUMB_PREFIX.length)]) {
          localStorage.removeItem(key);
        }
      }
    } catch (e) {}
    return data;
  }

  window.Didban = {
    merge: merge,
    lastCheck: function () { return load().last_check; }
  };
})();
