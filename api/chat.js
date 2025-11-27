const GEMINI_KEY = "AIzaSyC5TlqbwR2V3GQLjLaxQ4PTQ1MJ70j_1Jc"; // put your new google key

async function askGemini(text) {
    try {
        const res = await fetch(
            "https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key=" + GEMINI_KEY,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    contents: [
                        {
                            role: "user",
                            parts: [{ text }]
                        }
                    ]
                })
            }
        );

        const data = await res.json();

        if (data.error) return "Gemini Error: " + data.error.message;
        return data.candidates?.[0]?.content?.parts?.[0]?.text || "No response";

    } catch (err) {
        return "Request Failed";
    }
}

const chatBox = document.getElementById("chatBox");
const startBtn = document.getElementById("startBtn");

function addMsg(text, cls) {
    const div = document.createElement("div");
    div.className = "msg " + cls;
    div.textContent = text;
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
}

let lastMessage = "Hello, how are you?";

async function loopAI() {
    // AI 1
    addMsg("AI 1: " + lastMessage, "ai1");
    const reply1 = await askGemini(lastMessage);

    // AI 2
    addMsg("AI 2: " + reply1, "ai2");
    const reply2 = await askGemini(reply1);

    lastMessage = reply2;

    setTimeout(loopAI, 1200); // speed (1.2 sec)
}

startBtn.onclick = () => {
    startBtn.disabled = true;
    startBtn.innerText = "Chat Running...";
    loopAI();
};
