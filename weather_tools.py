# -*- coding: utf-8 -*-
"""天气专用数据源：Nominatim + Open-Meteo 主链，wttr.in 备链。"""

from __future__ import annotations

import datetime
import re

import requests

_UA = {"User-Agent": "jarvis-weather/1.0"}
_TIMEOUT = 8

_WMO = {
    0: "晴",
    1: "晴间多云",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "毛毛雨",
    53: "毛毛雨",
    55: "毛毛雨",
    56: "冻毛毛雨",
    57: "冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "阵雨",
    82: "暴雨",
    85: "小阵雪",
    86: "阵雪",
    95: "雷阵雨",
    96: "雷阵雨伴冰雹",
    99: "雷阵雨伴冰雹",
}

_WTTR = {
    "113": "晴",
    "116": "多云",
    "119": "多云",
    "122": "阴",
    "143": "雾",
    "176": "小阵雨",
    "200": "雷阵雨",
    "263": "小阵雨",
    "266": "小雨",
    "293": "小雨",
    "296": "小雨",
    "299": "阵雨",
    "302": "中雨",
    "305": "阵雨",
    "308": "大雨",
    "323": "小雪",
    "326": "小雪",
    "329": "中雪",
    "332": "中雪",
    "335": "大雪",
    "338": "大雪",
    "353": "小阵雨",
    "356": "阵雨",
    "359": "暴雨",
    "368": "小阵雪",
    "371": "阵雪",
    "386": "雷阵雨",
    "389": "雷雨",
}

_FILLERS = [
    "帮我查一下",
    "帮我查",
    "查一下",
    "天气预报",
    "查询",
    "搜索",
    "天气",
    "未来",
    "明天",
    "后天",
    "今天",
    "怎么样",
    "如何",
    "的",
    "吗",
    "？",
    "?",
    " ",
]


def _safe_int(value):
    try:
        if value is None:
            return None
        return int(round(float(value)))
    except Exception:
        return None


def _parse_offset(query: str) -> int:
    if "后天" in query:
        return 2
    if "明天" in query:
        return 1
    return 0


def _extract_city(query: str) -> str:
    city = str(query or "")
    for word in _FILLERS:
        city = city.replace(word, "")
    return city.strip()


def _day_label(offset: int) -> str:
    return {0: "今天", 1: "明天", 2: "后天"}.get(offset, "今天")


def _target_date(offset: int):
    return datetime.date.today() + datetime.timedelta(days=offset)


def _fmt_date(d) -> str:
    return f"{d.month}月{d.day}日"


def _format_answer(city, offset, desc, current_temp, high, low, precip, source):
    city = str(city or "").strip() or "当地"
    parts = []
    if offset == 0:
        first = f"{city}现在"
        if desc:
            first += f"{desc}"
        if current_temp is not None:
            first += f"，{current_temp}度"
        first += "。"
        parts.append(first)
        second = f"今天最高{high}度，最低{low}度。"
        parts.append(second)
    else:
        label = _day_label(offset)
        d = _target_date(offset)
        first = f"{city}{label}（{_fmt_date(d)}）"
        if desc:
            first += f"{desc}"
        first += f"，最高{high}度，最低{low}度。"
        parts.append(first)
    if precip is not None and precip >= 30:
        parts[-1] = parts[-1] + f"降雨概率{precip}%。"
    parts.append(f"(来源: {source})")
    return "".join(parts)


def _geocode(city: str):
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": city, "format": "json", "limit": 1},
        headers=_UA,
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    item = data[0]
    lat = item.get("lat")
    lon = item.get("lon")
    if lat is None or lon is None:
        return None
    return float(lat), float(lon)


def _open_meteo(lat: float, lon: float, offset: int):
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "Asia/Shanghai",
            "forecast_days": 7,
        },
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    daily = data.get("daily") or {}
    times = daily.get("time") or []
    target = _target_date(offset).isoformat()
    if target not in times:
        return None
    idx = times.index(target)
    code = (daily.get("weather_code") or [None])[idx]
    high = _safe_int((daily.get("temperature_2m_max") or [None])[idx])
    low = _safe_int((daily.get("temperature_2m_min") or [None])[idx])
    precip = _safe_int((daily.get("precipitation_probability_max") or [None])[idx])
    if high is None or low is None:
        return None
    desc = _WMO.get(_safe_int(code), "天气变化")
    current_temp = None
    if offset == 0:
        current = data.get("current") or {}
        current_temp = _safe_int(current.get("temperature_2m"))
        cur_code = _safe_int(current.get("weather_code"))
        if cur_code is not None:
            desc = _WMO.get(cur_code, "天气变化")
    return {
        "desc": desc,
        "current_temp": current_temp,
        "high": high,
        "low": low,
        "precip": precip,
    }


def _wttr_area(data):
    try:
        return data["nearest_area"][0]["areaName"][0]["value"].strip()
    except Exception:
        return ""


def _wttr_day(data, offset: int):
    weather = (data or {}).get("weather") or []
    target = _target_date(offset).isoformat()
    for day in weather:
        if day.get("date") == target:
            return day
    if 0 <= offset < len(weather):
        return weather[offset]
    return None


def _wttr(city: str, offset: int):
    loc = str(city or "").strip()
    url = f"https://wttr.in/{requests.utils.quote(loc)}" if loc else "https://wttr.in/"
    r = requests.get(url, params={"format": "j1"}, headers=_UA, timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    day = _wttr_day(data, offset)
    if not day:
        return None
    high = _safe_int(day.get("maxtempC"))
    low = _safe_int(day.get("mintempC"))
    if high is None or low is None:
        return None
    desc = ""
    precip = None
    hourly = day.get("hourly") or []
    # pick representative hourly slot: midday if exists else middle
    pick = None
    for h in hourly:
        if str(h.get("time", "")).zfill(4) == "1200":
            pick = h
            break
    if pick is None and hourly:
        pick = hourly[len(hourly) // 2]
    if pick:
        code = str(pick.get("weatherCode", "")).strip()
        desc = _WTTR.get(code, "")
        precip = _safe_int(pick.get("chanceofrain"))
    current_temp = None
    if offset == 0:
        try:
            cur = (data.get("current_condition") or [])[0]
            current_temp = _safe_int(cur.get("temp_C"))
            cur_code = str(cur.get("weatherCode", "")).strip()
            cur_desc = _WTTR.get(cur_code, "")
            if cur_desc:
                desc = cur_desc
        except Exception:
            pass
    area = _wttr_area(data)
    return {
        "area": area,
        "desc": desc,
        "current_temp": current_temp,
        "high": high,
        "low": low,
        "precip": precip,
    }


def answer_weather(query: str) -> str:
    try:
        q = str(query or "").strip()
        if not q:
            return ""
        offset = _parse_offset(q)
        city = _extract_city(q)

        if city:
            try:
                geo = _geocode(city)
                if geo:
                    lat, lon = geo
                    om = _open_meteo(lat, lon, offset)
                    if om:
                        return _format_answer(
                            city,
                            offset,
                            om["desc"],
                            om["current_temp"],
                            om["high"],
                            om["low"],
                            om["precip"],
                            "Open-Meteo",
                        )
            except Exception:
                pass
            try:
                wt = _wttr(city, offset)
                if wt:
                    return _format_answer(
                        city,
                        offset,
                        wt["desc"],
                        wt["current_temp"],
                        wt["high"],
                        wt["low"],
                        wt["precip"],
                        "wttr.in",
                    )
            except Exception:
                pass
            return ""

        try:
            wt = _wttr("", offset)
            if wt:
                area = wt.get("area") or "当地"
                return _format_answer(
                    area,
                    offset,
                    wt["desc"],
                    wt["current_temp"],
                    wt["high"],
                    wt["low"],
                    wt["precip"],
                    "wttr.in",
                )
        except Exception:
            pass
        return ""
    except Exception:
        return ""
