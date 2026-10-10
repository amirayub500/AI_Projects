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

        result.textContent =
            event.report;

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
