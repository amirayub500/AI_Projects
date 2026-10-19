/* ============================================================
   Welcome box: "Who's trying the demo?"  (v1.11.1)

   - Shown once per browser. A name is required to continue (no Skip).
   - What they type is saved in cookies (ak_name, ak_org) so every
     later visit and question in the visitor log shows their name.
   - A personal link like  /?ref=sarah  is saved as ak_ref.
   - POSTs to /hello so the log gets a "Said hello" line.

   To turn it off: remove the <script> line for this file in index.html.
   ============================================================ */

(function () {

    var YEAR = 60 * 60 * 24 * 365;

    function setCookie(name, value, maxAge) {
        var secure = location.protocol === "https:" ? "; Secure" : "";
        document.cookie = name + "=" + encodeURIComponent(value) +
            "; Max-Age=" + maxAge + "; Path=/; SameSite=Lax" + secure;
    }

    function getCookie(name) {
        var match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
        return match ? decodeURIComponent(match[1]) : "";
    }

    function say(body) {
        try {
            fetch("/hello", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
                keepalive: true
            });
        } catch (e) { /* the demo works without it */ }
    }

    // 1. Personal link: remember ?ref=... for 90 days
    var ref = new URLSearchParams(location.search).get("ref");
    if (ref) {
        setCookie("ak_ref", ref.trim().slice(0, 60), 60 * 60 * 24 * 90);
    }

    // 2. Already gave a name? Then stay out of the way.
    //    (People who used Skip in v1.11.0 are asked once more.)
    if (getCookie("ak_hello") === "yes") {
        return;
    }

    // 3. The box
    var css = document.createElement("style");
    css.textContent = [
        ".akh-back{position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;",
        "padding:16px;background:rgba(11,21,48,.55);backdrop-filter:blur(3px);animation:akhIn .25s ease-out}",
        ".akh-box{width:100%;max-width:420px;background:#fffaf2;color:#0b1530;border-radius:18px;",
        "padding:28px 26px 22px;box-shadow:0 24px 60px -20px rgba(11,21,48,.6);font-family:inherit}",
        ".akh-box h2{margin:0 0 6px;font-size:22px;line-height:1.25}",
        ".akh-box p{margin:0 0 18px;color:#5a6478;font-size:15px;line-height:1.5}",
        ".akh-box label{display:block;font-size:13px;font-weight:600;margin:0 0 6px}",
        ".akh-box label span{font-weight:400;color:#5a6478}",
        ".akh-box input{width:100%;box-sizing:border-box;margin:0 0 14px;padding:11px 13px;font:inherit;",
        "font-size:15px;color:#0b1530;background:#fff;border:1.5px solid #e0d1b6;border-radius:10px}",
        ".akh-box input:focus{outline:none;border-color:#E05A47;box-shadow:0 0 0 3px rgba(224,90,71,.18)}",
        ".akh-row{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:6px}",
        ".akh-go{flex:1;padding:12px 18px;font:inherit;font-size:15px;font-weight:700;color:#fff;",
        "background:#E05A47;border:0;border-radius:999px;cursor:pointer}",
        ".akh-go:hover{background:#c94a38}",
        ".akh-go:disabled{opacity:.45;cursor:default}",
        ".akh-box .akh-note{margin:14px 0 0;font-size:12px;color:#5a6478;text-align:center}",
        ".akh-box .akh-note a{color:#5a6478}",
        "@keyframes akhIn{from{opacity:0}to{opacity:1}}"
    ].join("");
    document.head.appendChild(css);

    var back = document.createElement("div");
    back.className = "akh-back";
    back.innerHTML =
        '<div class="akh-box" role="dialog" aria-modal="true" aria-labelledby="akh-title">' +
        '  <h2 id="akh-title">👋 Welcome! Who\'s trying the demo?</h2>' +
        '  <p>Please tell us who you are so we can follow up.</p>' +
        '  <form id="akh-form" autocomplete="on">' +
        '    <label for="akh-name">Your name</label>' +
        '    <input id="akh-name" name="name" maxlength="80" autocomplete="name">' +
        '    <label for="akh-org">Organization <span>(optional)</span></label>' +
        '    <input id="akh-org" name="organization" maxlength="120" autocomplete="organization">' +
        '    <div class="akh-row">' +
        '      <button type="submit" class="akh-go" id="akh-go" disabled>Continue</button>' +
        '    </div>' +
        '  </form>' +
        '  <p class="akh-note">Please don\'t enter anything confidential. ' +
        '<a href="/static/policy.html" target="_blank" rel="noopener">Demo policy</a></p>' +
        '</div>';

    function close() {
        back.remove();
    }

    function show() {
        document.body.appendChild(back);

        var name = document.getElementById("akh-name");
        var org = document.getElementById("akh-org");
        var go = document.getElementById("akh-go");

        name.addEventListener("input", function () {
            go.disabled = !name.value.trim();
        });

        document.getElementById("akh-form").addEventListener("submit", function (e) {
            e.preventDefault();
            var n = name.value.trim().slice(0, 80);
            var o = org.value.trim().slice(0, 120);
            if (!n) { return; }
            setCookie("ak_name", n, YEAR);
            if (o) { setCookie("ak_org", o, YEAR); }
            setCookie("ak_hello", "yes", YEAR);
            say({ name: n, org: o });
            close();
        });

        setTimeout(function () { name.focus(); }, 50);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", show);
    } else {
        show();
    }

})();
