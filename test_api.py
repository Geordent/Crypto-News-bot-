import os
import json
import requests
from dotenv import load_dotenv
from pycoingecko import CoinGeckoAPI
import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Загрузка .env
load_dotenv("/storage/6465-3434/PythonBots/Cryptonewsbot/.env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")
NEWSAPI_API_KEY = os.getenv("NEWSAPI_API_KEY", "")

cg = CoinGeckoAPI()
analyzer = SentimentIntensityAnalyzer()

def test_cryptopanic():
    if not CRYPTOPANIC_API_KEY:
        print("CRYPTOPANIC_API_KEY не задан.")
        return
    base_url = "https://cryptopanic.com/api/v1/posts/"
    params = {
        "auth_token": CRYPTOPANIC_API_KEY,
        "filter": "rising",
        "kind": "news",
    }
    try:
        resp = requests.get(base_url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        print("CryptoPanic News:")
        print(json.dumps(data, indent=2))
    except Exception as e:
        print(f"Ошибка при запросе CryptoPanic: {e}")

def test_newsapi():
    if not NEWSAPI_API_KEY:
        print("NEWSAPI_API_KEY не задан.")
        return
    base_url = "https://newsapi.org/v2/everything"
    params = {
        "q": "cryptocurrency OR bitcoin OR ethereum",
        "apiKey": NEWSAPI_API_KEY,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 5  # Ограничим количество для теста
    }
    try:
        resp = requests.get(base_url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        print("NewsAPI Articles:")
        print(json.dumps(data, indent=2))
    except Exception as e:
        print(f"Ошибка при запросе NewsAPI: {e}")

def test_coindesk_rss():
    feed_url = "https://feeds.feedburner.com/CoinDesk"
    try:
        feed = feedparser.parse(feed_url)
        print("CoinDesk RSS Entries:")
        for entry in feed.entries[:5]:  # Ограничим количество для теста
            print(f"- {entry.title}: {entry.link}")
    except Exception as e:
        print(f"Ошибка при запросе CoinDesk RSS: {e}")

def test_cointelegraph_rss():
    feed_url = "https://cointelegraph.com/rss"
    try:
        feed = feedparser.parse(feed_url)
        print("CoinTelegraph RSS Entries:")
        for entry in feed.entries[:5]:  # Ограничим количество для теста
            print(f"- {entry.title}: {entry.link}")
    except Exception as e:
        print(f"Ошибка при запросе CoinTelegraph RSS: {e}")

def test_coin_gecko_prices():
    cryptos = ["bitcoin", "ethereum"]
    try:
        prices = cg.get_price(ids=cryptos, vs_currencies="usd")
        print("CoinGecko Prices:")
        print(json.dumps(prices, indent=2))
    except Exception as e:
        print(f"Ошибка при запросе цен через CoinGecko: {e}")

if __name__ == "__main__":
    print("=== Тестирование API Подключений ===\n")
    test_cryptopanic()
    print("\n-------------------------------\n")
    test_newsapi()
    print("\n-------------------------------\n")
    test_coindesk_rss()
    print("\n-------------------------------\n")
    test_cointelegraph_rss()
    print("\n-------------------------------\n")
    test_coin_gecko_prices()
