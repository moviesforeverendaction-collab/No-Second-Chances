import random
import aiohttp
from secret import AI_PROVIDER, AI_API_KEY
from logger import logger

AI_ENABLED: bool = False
_provider: str = ""

_CONFIGS = {
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
        "key_param": "key",
        "request_builder": lambda prompt: {
            "contents": [{"parts": [{"text": prompt}]}]
        },
        "response_parser": lambda d: d["candidates"][0]["content"]["parts"][0]["text"],
    },
    "openai": {
        "url": "https://api.openai.com/v1/chat/completions",
        "key_param": None,
        "request_builder": lambda prompt: {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 300,
        },
        "response_parser": lambda d: d["choices"][0]["message"]["content"],
    },
    "grok": {
        "url": "https://api.x.ai/v1/chat/completions",
        "key_param": None,
        "request_builder": lambda prompt: {
            "model": "grok-3-mini",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 300,
        },
        "response_parser": lambda d: d["choices"][0]["message"]["content"],
    },
    "claude": {
        "url": "https://api.anthropic.com/v1/messages",
        "key_param": None,
        "request_builder": lambda prompt: {
            "model": "claude-haiku-20240307",
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}],
        },
        "response_parser": lambda d: d["content"][0]["text"],
    },
}

_BAN_JOKE_FALLBACKS = [
    "🚫 Once a leaver, forever a believer... in consequences.",
    "🚪 The door you left through? Permanently welded shut. 😈",
    "👋 Bye again! This door has a very good memory.",
    "📜 Your application to rejoin has been reviewed and... no.",
    "🔒 Access denied. Our bouncer has your photo memorized.",
    "🎭 Plot twist: you can't rejoin. Not even with a disguise.",
    "💌 Dear user, your request to return has been filed under 'Nope'.",
]


async def initialize_ai() -> None:
    global AI_ENABLED, _provider
    if not AI_PROVIDER or not AI_API_KEY:
        logger.info("AI: No AI_PROVIDER or AI_API_KEY configured. AI features disabled.")
        return
    if AI_PROVIDER not in _CONFIGS:
        logger.warning(
            f"AI: Unknown AI_PROVIDER '{AI_PROVIDER}'. "
            f"Supported: {list(_CONFIGS)}. AI disabled."
        )
        return
    try:
        response = await get_ai_response("Say 'OK' in one word.")
        if response:
            AI_ENABLED = True
            _provider = AI_PROVIDER
            logger.info(f"AI: {AI_PROVIDER} connected successfully. AI features enabled.")
    except Exception as e:
        logger.warning(f"AI: Connection test failed for {AI_PROVIDER}: {e}. AI disabled.")


async def get_ai_response(prompt: str, system: str = "") -> str | None:
    if not AI_API_KEY or AI_PROVIDER not in _CONFIGS:
        return None
    cfg = _CONFIGS[AI_PROVIDER]
    try:
        headers = {"Content-Type": "application/json"}
        params = {}
        if cfg["key_param"]:
            params[cfg["key_param"]] = AI_API_KEY
        else:
            headers["Authorization"] = f"Bearer {AI_API_KEY}"

        if AI_PROVIDER == "claude":
            headers["x-api-key"] = AI_API_KEY
            headers["anthropic-version"] = "2023-06-01"
            headers.pop("Authorization", None)

        full_prompt = f"{system}\n\n{prompt}".strip() if system else prompt
        payload = cfg["request_builder"](full_prompt)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                cfg["url"],
                json=payload,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return cfg["response_parser"](data).strip()
                else:
                    logger.warning(f"AI API returned status {resp.status}")
                    return None
    except Exception as e:
        logger.error(f"AI request failed: {e}")
        return None


async def generate_ban_joke(user_id: int) -> str:
    if not AI_ENABLED:
        return random.choice(_BAN_JOKE_FALLBACKS)

    prompt = (
        f"Write one short, funny, sassy rejection message for a Telegram user (ID: {user_id}) "
        f"who left a group chat and is now trying to rejoin but is permanently banned. "
        f"Max 2 sentences. Include 1-2 relevant emojis. Be playfully mean, not cruel."
    )
    result = await get_ai_response(prompt)
    return result if result else random.choice(_BAN_JOKE_FALLBACKS)


async def generate_ban_dm(user_id: int, first_name: str, chat_name: str) -> str:
    if not AI_ENABLED:
        return f"You've been permanently removed from {chat_name}. This action cannot be reversed."

    prompt = (
        f"Write a polite, brief Telegram DM to {first_name} letting them know they've been "
        f"permanently banned from {chat_name}. Be professional and firm. Max 2 sentences."
    )
    result = await get_ai_response(prompt)
    return result if result else f"You've been permanently removed from {chat_name}. This action cannot be reversed."


async def generate_plea_response(approved: bool, first_name: str) -> str:
    if not AI_ENABLED:
        return (
            "Great news — your request has been approved!"
            if approved
            else "Your request was carefully reviewed but could not be approved."
        )

    action = "approved" if approved else "denied"
    prompt = (
        f"Write a short message to {first_name} whose unban request was {action}. "
        f"Be kind but firm. 1 sentence."
    )
    result = await get_ai_response(prompt)
    return result if result else (
        "Great news — your request has been approved!"
        if approved
        else "Your request was carefully reviewed but could not be approved."
    )


async def answer_user_question(question: str, username: str = "user") -> str | None:
    if not AI_ENABLED:
        return None
    system = (
        "You are the assistant for 'No Second Chances' bot — a Telegram group anti-rejoin "
        "enforcement bot. Be helpful, concise, and friendly. Keep responses under 3 sentences."
    )
    return await get_ai_response(question, system=system)
