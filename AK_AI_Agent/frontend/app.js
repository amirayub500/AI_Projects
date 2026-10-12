const button =
    document.getElementById(
        "researchButton"
    );

const topicInput =
    document.getElementById(
        "topic"
    );

const status =
    document.getElementById(
        "status"
    );

const result =
    document.getElementById(
        "result"
    );

const progressSection =
    document.getElementById(
        "progressSection"
    );

const resultSection =
    document.getElementById(
        "resultSection"
    );

const errorBox =
    document.getElementById(
        "error"
    );

const characterCount =
    document.getElementById(
        "characterCount"
    );


topicInput.addEventListener(
    "input",
    () => {

        const length =
            topicInput.value.length;

        characterCount.textContent =
            `${length} characters`;
    }
);


button.addEventListener(
    "click",
    async () => {

        const topic =
            topicInput.value.trim();


        if (!topic) {

            errorBox.textContent =
                "Please enter a research topic.";

            errorBox.classList.remove(
                "hidden"
            );

            return;
        }


        errorBox.classList.add(
            "hidden"
        );

        resultSection.classList.add(
            "hidden"
        );

        progressSection.classList.remove(
            "hidden"
        );


        button.disabled = true;

        button.textContent =
            "Researching...";


        status.textContent =
            "Your research agent is working...";


        try {

    await startResearch(
        topic
    );

} catch (error) {

    errorBox.textContent =
        error.message;

    errorBox.classList.remove(
        "hidden"
    );

}
 finally {

            button.disabled = false;

            button.innerHTML =
                "<span>✦</span> Start Research";
        }

    }
);


async function startResearch(topic) {


    document.getElementById(
    "activity"
).innerHTML = "";

    progressSection.classList.remove(
        "hidden"
    );

    resultSection.classList.add(
        "hidden"
    );

    result.textContent = "";

    clearCharts();

    showDownload([]);

    lastQuestion = topic;

    status.textContent =
        "Starting research...";


    const response = await fetch(
        `/research?topic=${encodeURIComponent(topic)}`
    );


    if (!response.ok) {

        throw new Error(
            "Unable to start research."
        );
    }


    const reader =
        response.body.getReader();


    const decoder =
        new TextDecoder();


    let buffer = "";


    while (true) {

        const {
            value,
            done
        } = await reader.read();


        if (done) {
            break;
        }


        buffer += decoder.decode(
            value,
            {
                stream: true
            }
        );


        const events =
            buffer.split("\n\n");


        buffer =
            events.pop();


        for (
            const event of events
        ) {

            if (!event.startsWith("data: ")) {
                continue;
            }


            const json =
                event.substring(6);


            const data =
                JSON.parse(json);


            handleResearchEvent(
                data
            );
        }
    }
}


function handleResearchEvent(event) {

    if (event.type === "plan") {

        status.textContent =
            "Research plan created.";

        addActivity(
            "✓",
            "Research plan created"
        );

        return;
    }


    if (event.type === "step") {

        addActivity(
            "●",
            event.message
        );

        return;
    }


    if (event.type === "tool") {

        addActivity(
            "●",
            event.message
        );

        status.textContent =
            event.message;

        return;
    }


    if (event.type === "tool_complete") {

        addActivity(
            "✓",
            event.message
        );

        return;
    }


    if (
        event.type === "verification"
    ) {

        addActivity(
            "✓",
            event.message
        );

        return;
    }


    if (event.type === "complete") {

        progressSection.classList.add(
            "hidden"
        );

        resultSection.classList.remove(
            "hidden"
        );

        result.innerHTML =
            renderReport(event.report || "");

        renderCharts(
            event.charts || []
        );

        showDownload(
            event.tables || []
        );

        status.textContent =
            "Research completed.";
    }
}


function addActivity(
    icon,
    message
) {

    const item =
        document.createElement(
            "div"
        );

    item.className =
        "activity-item";


    item.innerHTML = `
        <span class="activity-icon">
            ${icon}
        </span>

        <span>
            ${message}
        </span>
    `;


    document
        .getElementById("activity")
        .appendChild(item);
}


// Sample questions: clicking one puts it in the box
document.querySelectorAll(".sample-q").forEach((sample) => {

    sample.addEventListener("click", () => {

        topicInput.value = sample.dataset.q;

        characterCount.textContent =
            `${topicInput.value.length} characters`;

        sample.closest("details").open = false;

        topicInput.focus();
    });
});


// ---------------------------------------------------------------------
// Charts: drawn from the database rows the server sends (no made-up numbers)
// ---------------------------------------------------------------------

const chartsBox =
    document.getElementById("charts");

// Chart.js objects currently on the page (destroyed before a new research)
let activeCharts = [];

// Theme colors: green, blue, violet, then lighter shades for pie slices
const CHART_COLORS = [
    "#369379", "#2563eb", "#5c61de", "#7cc4ad",
    "#7aa5f5", "#a3a6ef", "#2c7a63", "#1d4fb8",
    "#c9a227", "#e07a5f"
];

function clearCharts() {

    activeCharts.forEach((chart) => chart.destroy());

    activeCharts = [];

    chartsBox.innerHTML = "";
}

function renderCharts(charts) {

    clearCharts();

    if (!charts.length || typeof Chart === "undefined") {
        return;
    }

    charts.forEach((spec) => {

        const figure = document.createElement("figure");
        figure.className = "chart-card";

        const heading = document.createElement("h4");
        heading.textContent = spec.title;

        const holder = document.createElement("div");
        holder.className = "chart-holder";

        const canvas = document.createElement("canvas");
        holder.appendChild(canvas);

        const caption = document.createElement("figcaption");
        caption.textContent =
            "Source: " + spec.source +
            (spec.truncated ? " (first 50 rows)" : "");
        caption.title = spec.sql;

        figure.append(heading, holder, caption);
        chartsBox.appendChild(figure);

        const isPie = spec.type === "pie";

        const datasets = spec.datasets.map((set, i) => ({
            label: set.label,
            data: set.data,
            backgroundColor: isPie
                ? spec.labels.map((_, j) => CHART_COLORS[j % CHART_COLORS.length])
                : CHART_COLORS[i % CHART_COLORS.length],
            borderColor: isPie ? "#ffffff" : CHART_COLORS[i % CHART_COLORS.length],
            borderWidth: 2,
            tension: 0.25,
            pointRadius: 4,
            fill: false
        }));

        activeCharts.push(new Chart(canvas, {
            type: spec.type,
            data: { labels: spec.labels, datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: isPie || datasets.length > 1 }
                },
                scales: isPie ? {} : {
                    y: { beginAtZero: true }
                }
            }
        }));
    });
}


// ---------------------------------------------------------------------
// Report formatting: headings (#, ##, ###), **bold** and --- lines.
// The text is HTML-escaped first, so nothing in it can run as code.
// ---------------------------------------------------------------------

function renderReport(text) {

    const escaped = text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");

    return escaped
        .split("\n")
        .map((line) => {

            const heading = line.match(/^\s*#{1,4}\s+(.*)$/);

            if (heading) {
                return `<span class="report-heading">${heading[1].replace(/\*\*/g, "")}</span>`;
            }

            if (/^\s*-{3,}\s*$/.test(line)) {
                return `<hr class="report-rule">`;
            }

            return line.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
        })
        .join("\n");
}


// ---------------------------------------------------------------------
// Version label backup: if the page came from an old cache and still shows
// the {{...}} placeholder, ask the server for its version (GET /health).
// ---------------------------------------------------------------------

(function fillVersion() {

    const label = document.getElementById("appVersion");
    const footer = document.getElementById("footerVersion");

    if (!label || !label.textContent.includes("{{")) {
        return;
    }

    fetch("/health", { cache: "no-store" })
        .then((response) => response.json())
        .then((data) => {
            const version = data.version || "";
            label.textContent = version ? "v" + version : "";
            if (footer) {
                footer.textContent = version;
            }
        })
        .catch(() => {
            label.textContent = "";
        });
})();


// ---------------------------------------------------------------------
// Download as Excel: the server sends the tables behind a "/" answer;
// the button posts them back to /export-excel and saves the .xlsx file.
// ---------------------------------------------------------------------

const downloadButton =
    document.getElementById("downloadExcel");

let lastQuestion = "";
let lastTables = [];

function showDownload(tables) {

    lastTables = tables;

    downloadButton.classList.toggle("hidden", tables.length === 0);
}

downloadButton.addEventListener("click", async () => {

    downloadButton.disabled = true;

    const label = downloadButton.innerHTML;
    downloadButton.textContent = "Preparing...";

    try {

        const response = await fetch("/export-excel", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question: lastQuestion, tables: lastTables })
        });

        if (!response.ok) {
            throw new Error("Download failed. Please try again.");
        }

        const blob = await response.blob();
        const match = (response.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/);

        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = match ? match[1] : "research_export.xlsx";
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);

    } catch (error) {

        errorBox.textContent = error.message;
        errorBox.classList.remove("hidden");

    } finally {

        downloadButton.disabled = false;
        downloadButton.innerHTML = label;
    }
});


// ---------------------------------------------------------------------
// Download as PDF: fill in the print header, then open the print window
// (the print styles in style.css show only the report). Choose "Save as PDF".
// ---------------------------------------------------------------------

document.getElementById("downloadPdf").addEventListener("click", () => {

    document.getElementById("printQuestion").textContent =
        lastQuestion || topicInput.value.trim();

    const version = (document.getElementById("appVersion") || {}).textContent || "";

    document.getElementById("printMeta").textContent =
        "AK Data Solutions Research AI Agent " + version +
        " · " + new Date().toLocaleString() +
        " · Demo with sample data";

    // Charts: stop animation and redraw at print size
    activeCharts.forEach((chart) => {
        chart.options.animation = false;
        chart.resize();
    });

    window.print();
});
