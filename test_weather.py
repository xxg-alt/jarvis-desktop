# -*- coding: utf-8 -*-
"""天气专用数据源测试：打桩 requests.get，不走真实网络。"""

import sys

sys.path.insert(0, "/data/workspace/jarvis-desktop")

import datetime

import weather_tools


class _Resp:
    def __init__(self, data=None, status_code=200, exc=None):
        self._data = data
        self.status_code = status_code
        self._exc = exc

    def raise_for_status(self):
        if self._exc:
            raise self._exc
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._data


def _tomorrow():
    return (datetime.date.today() + datetime.timedelta(days=1)).isoformat()


def _today():
    return datetime.date.today().isoformat()


def main():
    fails = []
    real_get = weather_tools.requests.get

    try:
        # 1. 主链：景德镇明天
        def fake_get(url, params=None, headers=None, timeout=None):
            if "nominatim.openstreetmap.org/search" in url:
                assert params.get("q") == "景德镇"
                return _Resp([{"lat": "29.3", "lon": "117.2"}])
            if "api.open-meteo.com/v1/forecast" in url:
                assert params.get("forecast_days") == 7
                return _Resp({
                    "current": {"temperature_2m": 18.4, "weather_code": 1},
                    "daily": {
                        "time": [_today(), _tomorrow()],
                        "weather_code": [1, 61],
                        "temperature_2m_max": [20.2, 23.6],
                        "temperature_2m_min": [11.1, 14.3],
                        "precipitation_probability_max": [10, 40],
                    },
                })
            raise AssertionError(f"unexpected url: {url}")

        weather_tools.requests.get = fake_get
        ans = weather_tools.answer_weather("景德镇明天的天气怎么样")
        for key in ("景德镇", "明天", "24", "14", "40%", "Open-Meteo"):
            if key not in ans:
                fails.append(f"明天查询缺少 {key}: {ans}")
        if _tomorrow()[5:7].lstrip("0") not in ans:
            # 日期格式 M月D日
            t = datetime.date.today() + datetime.timedelta(days=1)
            if f"{t.month}月{t.day}日" not in ans:
                fails.append(f"明天查询缺少日期: {ans}")

        # 2. 今天：北京今天天气
        def fake_get_today(url, params=None, headers=None, timeout=None):
            if "nominatim.openstreetmap.org/search" in url:
                return _Resp([{"lat": "39.9", "lon": "116.4"}])
            if "api.open-meteo.com/v1/forecast" in url:
                return _Resp({
                    "current": {"temperature_2m": 26.2, "weather_code": 0},
                    "daily": {
                        "time": [_today()],
                        "weather_code": [0],
                        "temperature_2m_max": [28.7],
                        "temperature_2m_min": [18.1],
                        "precipitation_probability_max": [0],
                    },
                })
            raise AssertionError(f"unexpected url: {url}")

        weather_tools.requests.get = fake_get_today
        ans = weather_tools.answer_weather("北京今天天气")
        for key in ("北京", "现在", "26", "29", "18", "Open-Meteo"):
            if key not in ans:
                fails.append(f"今天查询缺少 {key}: {ans}")

        # 3. 全部异常返回空串，不抛异常
        def boom(*args, **kwargs):
            raise RuntimeError("network down")

        weather_tools.requests.get = boom
        try:
            ans = weather_tools.answer_weather("上海明天天气")
        except Exception as exc:
            fails.append(f"异常时应返回空串而不是抛异常: {exc}")
            ans = ""
        if ans != "":
            fails.append(f"异常时应返回空串: {ans!r}")

        # 4. 无城市问句走 wttr，带地区名
        def fake_get_wttr(url, params=None, headers=None, timeout=None):
            if "wttr.in" in url:
                return _Resp({
                    "nearest_area": [{"areaName": [{"value": "Jingdezhen"}]}],
                    "current_condition": [{"temp_C": "22", "weatherCode": "116"}],
                    "weather": [
                        {
                            "date": _today(),
                            "maxtempC": "25",
                            "mintempC": "16",
                            "hourly": [{"time": "1200", "weatherCode": "116", "chanceofrain": "10"}],
                        },
                        {
                            "date": _tomorrow(),
                            "maxtempC": "27",
                            "mintempC": "17",
                            "hourly": [{"time": "1200", "weatherCode": "113", "chanceofrain": "5"}],
                        },
                    ],
                })
            raise AssertionError(f"unexpected url: {url}")

        weather_tools.requests.get = fake_get_wttr
        ans = weather_tools.answer_weather("明天天气怎么样")
        for key in ("Jingdezhen", "明天", "27", "17", "wttr.in"):
            if key not in ans:
                fails.append(f"无城市wttr缺少 {key}: {ans}")

    finally:
        weather_tools.requests.get = real_get

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        sys.exit(1)
    print("PASS: weather_tools 4 cases")


if __name__ == "__main__":
    main()
