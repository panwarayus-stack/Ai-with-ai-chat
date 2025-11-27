const GEMINI_KEY = "AIzaSyCpxDoltExFg4KfN8QsJoTXuZaOJx6Ylw8";

const chatBox = document.getElementById("chatBox");
const startBtn = document.getElementById("startBtn");

// Scroll always to bottom
function addMsg(text, who) {
    let div = document.createElement("div");
    div.className = "msg " + who;
    div.innerText = text;
    chatBox.appendChild(div);

    chatBox.scrollTop = chatBox.scrollHeight;
}

async function prexzyAI(input) {
    const r = await fetch("https://apis.prexzyvilla.site/ai/gpt-5?text=" + encodeURIComponent(input));
    const j = await r.json();
    return j.text;
}

async function geminiAI(input) {
    const r = await fetch(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key=" + GEMINI_KEY,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                contents: [{ parts: [{ text: input }] }]
            })
        }
    );

    const j = await r.json();
    return j?.candidates?.[0]?.content?.parts?.[0]?.text || "Gemini Error.";
}

let running = false;

startBtn.onclick = async () => {
    if (running) return;
    running = true;

    let msg = "Hello Gemini, introduce yourself!";
    addMsg(msg, "ai1");

    while (running) {
        // PREXZY reply
        let p = await prexzyAI(msg);
        addMsg(p, "ai1");

        // GEMINI reply
        let g = await geminiAI(p);
        addMsg(g, "ai2");

        msg = g;

        await new Promise(r => setTimeout(r, 1500));
    }
};
