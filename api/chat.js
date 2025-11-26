export default async function handler(req, res) {
  if (req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  const history = req.body.history || [];
  const lastMessage = history.length > 0 ? history[history.length - 1] : "Hello";

  try {
    // ----- 1) PREXZY GPT-5 API -----
    const prexzyResp = await fetch(`https://apis.prexzyvilla.site/ai/gpt-5?text=${encodeURIComponent(lastMessage)}`);
    const prexzyData = await prexzyResp.json();
    const gpt5Reply = prexzyData.text;

    // ----- 2) GOOGLE GEMINI API -----
    const geminiResp = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5:generateText?key=${process.env.GEMINI_API_KEY}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: gpt5Reply,
          maxOutputTokens: 200
        })
      }
    );

    const geminiData = await geminiResp.json();
    const geminiReply = geminiData.candidates?.[0]?.content?.[0]?.text || "Gemini did not respond.";

    res.status(200).json({
      from_gpt5: gpt5Reply,
      from_gemini: geminiReply
    });
  } catch (error) {
    console.error(error);
    res.status(500).json({ error: "Something went wrong" });
  }
}
