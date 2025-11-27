const GEMINI_KEY = "AIzaSyCpxDoltExFg4KfN8QsJoTXuZaOJx6Ylw8";

const chatBox = document.getElementById("chatBox");
const startBtn = document.getElementById("startBtn");

// Auto-scroll
function addMsg(text, who) {
    const div = document.createElement("div");
    div.className = "msg " + who;
    div.innerText = text;
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
}

// PREXZY GPT-5 API
async function prexzyAI(txt) {
    try {
        const r = await fetch("https://apis.prexzyvilla.site/ai/gpt-5?text=" + encodeURIComponent(txt));
        const j = await r.json();
        return j.text || "Prexzy Error";
    } catch {
        return "Prexzy API Failed.";
    }
}

// GEMINI API
async function geminiAI(txt) {
    try {
        const r = await fetch(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key=" + GEMINI_KEY,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    contents: [{ parts: [{ text: txt }] }]
                })
            }
        );

        const j = await r.json();
        return j?.candidates?.[0]?.content?.parts?.[0]?.text || "Gemini Error";
    } catch {
        return "Gemini API Failed.";
    }
}

let running = false;

startBtn.onclick = async () => {
    if (running) return;
    running = true;

    let msg = "Hello Gemini, tell me something interesting!";
    addMsg(msg, "ai1");

    while (running) {
        let reply1 = await prexzyAI(msg);
        addMsg(reply1, "ai1");

        let reply2 = await geminiAI(reply1);
        addMsg(reply2, "ai2");

        msg = reply2;

        await new Promise(r => setTimeout(r, 1500));
    }
};
