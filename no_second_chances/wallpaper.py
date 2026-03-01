import random
import aiohttp
from logger import logger

WAIFU_IM_URL = "https://api.waifu.im/search"
NEKOS_BEST_URL = "https://nekos.best/api/v2/neko"
PICSUM_BASE = "https://picsum.photos/1280/720"
_WALLPAPER_TAGS = ["waifu", "maid", "uniform", "raiden-shogun"]


async def _fetch_waifu_im(session: aiohttp.ClientSession, tag: str = "waifu") -> str | None:
    params = {
        "included_tags": tag,
        "is_nsfw": "false",
        "orientation": "landscape",
    }
    try:
        async with session.get(
            WAIFU_IM_URL, params=params, timeout=aiohttp.ClientTimeout(total=8)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                images = data.get("images", [])
                if images:
                    img = images[0]
                    w, h = img.get("width", 0), img.get("height", 0)
                    if w > h:
                        return img["url"]
    except Exception as e:
        logger.warning(f"waifu.im failed with tag '{tag}': {e}")
    return None


async def _fetch_nekos_best(session: aiohttp.ClientSession) -> str | None:
    try:
        async with session.get(
            NEKOS_BEST_URL,
            params={"amount": "1"},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = data.get("results", [])
                if results:
                    return results[0]["url"]
    except Exception as e:
        logger.warning(f"nekos.best failed: {e}")
    return None


def _picsum_url() -> str:
    return f"{PICSUM_BASE}?random={random.randint(1, 99999)}"


async def get_anime_wallpaper(tag: str | None = None) -> str:
    from no_second_chances.cache import wallpaper_cache

    if tag is None:
        tag = random.choice(_WALLPAPER_TAGS)

    cache_key = f"wallpaper:{tag}"
    cached = wallpaper_cache.get(cache_key)
    if cached:
        return cached

    async with aiohttp.ClientSession() as session:
        url = await _fetch_waifu_im(session, tag)
        if url:
            logger.info(f"Wallpaper from waifu.im (tag: {tag}): {url}")
            wallpaper_cache.set(cache_key, url, ttl=30)
            return url

        url = await _fetch_nekos_best(session)
        if url:
            logger.info(f"Wallpaper from nekos.best: {url}")
            wallpaper_cache.set(cache_key, url, ttl=30)
            return url

    url = _picsum_url()
    logger.info(f"Wallpaper from picsum (fallback): {url}")
    return url
