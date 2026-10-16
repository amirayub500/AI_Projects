/* ============================================================
   AK UI refresh — loaded AFTER app.js. Presentation only.
   Does not touch research, charts, downloads or the backend.
   Delete the <script> tag in index.html to revert.
   ============================================================ */

(function () {
    "use strict";

    var samples = document.querySelector("details.samples");
    if (!samples) { return; }

    /* The sample list is always open now. app.js closes it after a question is
       picked (sample.closest("details").open = false) — we simply reopen it,
       so app.js keeps working untouched. */
    samples.open = true;

    samples.addEventListener("toggle", function () {
        if (!samples.open) { samples.open = true; }
    });


    /* Category tabs: show one group of questions at a time. */
    var tabs = samples.querySelectorAll(".ak-tab");
    var groups = samples.querySelectorAll(".ak-group");

    tabs.forEach(function (tab) {

        tab.addEventListener("click", function () {

            tabs.forEach(function (t) { t.classList.remove("is-on"); });
            tab.classList.add("is-on");

            groups.forEach(function (g) {
                g.hidden = (g.id !== tab.dataset.group);
            });
        });
    });

}());
