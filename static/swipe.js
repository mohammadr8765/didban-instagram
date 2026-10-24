(function () {
  var body = document.body;
  var toRight = body.getAttribute("data-swipe-right");
  var toLeft = body.getAttribute("data-swipe-left");
  var shell = document.querySelector(".shell");

  try {
    var dir = sessionStorage.getItem("didban_dir");
    sessionStorage.removeItem("didban_dir");
    if (shell && dir === "right") shell.classList.add("enter-left");
    if (shell && dir === "left") shell.classList.add("enter-right");
  } catch (e) {}

  function go(url, direction) {
    try { sessionStorage.setItem("didban_dir", direction); } catch (e) {}
    if (shell) shell.classList.add(direction === "right" ? "leave-right" : "leave-left");
    setTimeout(function () { location.href = url; }, 150);
  }

  var sx = 0, sy = 0, st = 0, tracking = false;
  var EDGE = 24; // ignore swipes that start at the screen edge (system back gesture)

  document.addEventListener("touchstart", function (e) {
    if (e.touches.length !== 1) { tracking = false; return; }
    var t = e.touches[0];
    sx = t.clientX; sy = t.clientY; st = Date.now();
    tracking = sx > EDGE && sx < window.innerWidth - EDGE;
  }, { passive: true });

  document.addEventListener("touchend", function (e) {
    if (!tracking) return;
    tracking = false;
    var t = e.changedTouches[0];
    var dx = t.clientX - sx, dy = t.clientY - sy;
    if (Date.now() - st > 700) return;
    if (Math.abs(dx) < 70 || Math.abs(dx) < Math.abs(dy) * 1.6) return;
    if (dx > 0 && toRight) go(toRight, "right");
    else if (dx < 0 && toLeft) go(toLeft, "left");
  }, { passive: true });

  window.addEventListener("pageshow", function (e) {
    if (e.persisted && shell) shell.classList.remove("leave-left", "leave-right");
  });
})();
