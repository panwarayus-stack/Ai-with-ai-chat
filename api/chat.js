const GEMINI_KEY = "AIzaSyCpxDoltExFg4KfN8QsJoTXuZaOJx6Ylw8";

const chatBox = document.getElementById("chatBox");
const startBtn = document.getElementById("startBtn");

function addMsg(text, who) {
    const div = document.createElement("div");
    div.className = "msg " + who;
    div.innerText = text;
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
}

// PREXZY GPT-5 API
async function prexzyAI(msg) {
    try {
        let r = await fetch("https://apis.prexzyvilla.site/ai/gpt-5?text=" + encodeURIComponent(msg));
        let j = await r.json();
        return j.text || "Prexzy error";
    } catch {
        return "Prexzy API failed";
    }
}

// GEMINI AI API
async function geminiAI(msg) {
    try {
        let r = await fetch(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key=" + GEMINI_KEY,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    contents: [{ parts: [{ text: msg }] }]
                })
            }
        );

        let j = await r.json();
        return j?.candidates?.[0]?.content?.parts?.[0]?.text || "Gemini error";
    } catch {
        return "Gemini API failed";
    }
}

let running = false;

startBtn.onclick = async () => {
    if (running) return;
    running = true;

    let msg = "Hello Gemini, say something!";
    addMsg("GPT-5: " + msg, "ai1");

    while (running) {
        let gpt = await prexzyAI(msg);
        addMsg("GPT-5: " + gpt, "ai1");

        let gem = await geminiAI(gpt);
        addMsg("Gemini: " + gem, "ai2");

        msg = gem;

        await new Promise(res => setTimeout(res, 2000));
    }
};
