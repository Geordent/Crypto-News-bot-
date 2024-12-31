import os
import time
import logging
import requests
import json
import re
import feedparser
import threading

from dotenv import load_dotenv
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from pycoingecko import CoinGeckoAPI

from telegram import (
    Update,
    ParseMode,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Updater,
    CommandHandler,
    CallbackContext,
    MessageHandler,
    Filters,
)

# Настройка логирования
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("CryptoNewsBot")

# Загрузка переменных окружения из .env (Укажите свой путь)
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")
NEWSAPI_API_KEY = os.getenv("NEWSAPI_API_KEY", "")

if not TELEGRAM_BOT_TOKEN:
    logger.error("Не указан TELEGRAM_BOT_TOKEN. Выход.")
    exit(1)

NEWS_STORAGE_FILE = "last_news_id.dat"
PREVIOUS_PRICES_FILE = "previous_prices.json"
SUBSCRIPTIONS_FILE = "subscriptions.json"

# Через сколько секунд выполнять планировщик (например, раз в час)
POLL_INTERVAL = 3600

# ID вашего канала (должен быть админом в канале, если используете getChatMember)
CHANNEL_ID = -1002126621893
# Ссылка-приглашение в канал
CHANNEL_INVITE_LINK = "https://t.me/+M62co0BH-pIwN2Fi"

cg = CoinGeckoAPI()
analyzer = SentimentIntensityAnalyzer()

# ========== ГЛОБАЛЬНАЯ ПЕРЕМЕННАЯ для списка доступных монет ============
# Мы заранее загрузим все «id» монет с CoinGecko, чтобы проверять корректность.
SUPPORTED_COINS = set()

def load_supported_coins():
    """Загружаем список монет (id) с CoinGecko для проверки. Делается один раз при старте."""
    global SUPPORTED_COINS
    try:
        coin_list = cg.get_coins_list()  # [{'id': 'bitcoin', ...}, ...]
        SUPPORTED_COINS = {coin['id'].lower() for coin in coin_list}
        logger.info(f"Загружено {len(SUPPORTED_COINS)} монет из CoinGecko для проверки")
    except Exception as e:
        logger.error(f"Не удалось загрузить список монет из CoinGecko: {e}")
        SUPPORTED_COINS = set()  # пусть будет пустое множество в случае ошибки

# --------------------------- Проверка подписки на канал ------------------------
def is_user_in_channel(bot, user_id: int, channel_id: int) -> bool:
    """
    Возвращает True, если пользователь user_id состоит (или является админом) в канале channel_id.
    При ошибке (бот не админ, канал приватный и т.д.) возвращаем False.
    """
    try:
        member_info = bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        if member_info.status in ["member", "administrator", "creator"]:
            return True
        return False
    except Exception as e:
        logger.warning(f"Ошибка getChatMember для user_id={user_id}: {e}")
        return False

# ---------------------- Функции подписок --------------------------------------

def load_subscriptions() -> dict:
    if not os.path.exists(SUBSCRIPTIONS_FILE):
        return {}
    try:
        with open(SUBSCRIPTIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Не удалось прочитать {SUBSCRIPTIONS_FILE}: {e}")
        return {}

def save_subscriptions(subscriptions: dict):
    try:
        with open(SUBSCRIPTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(subscriptions, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.warning(f"Не удалось записать {SUBSCRIPTIONS_FILE}: {e}")

def add_subscription(user_id: str, subscription: str) -> bool:
    """
    Добавляет криптовалюту (subscription) к подпискам пользователя.
    Возвращает True, если добавили новую подписку, False — если такая уже была
    """
    subscriptions = load_subscriptions()
    user_subs = subscriptions.setdefault(user_id, [])

    if subscription in user_subs:
        return False

    user_subs.append(subscription)
    save_subscriptions(subscriptions)
    logger.info(f"Пользователь {user_id} подписался на '{subscription}'.")
    return True

def remove_subscription(user_id: str, subscription: str) -> bool:
    subscriptions = load_subscriptions()
    if user_id in subscriptions and subscription in subscriptions[user_id]:
        subscriptions[user_id].remove(subscription)
        save_subscriptions(subscriptions)
        logger.info(f"Пользователь {user_id} отписался от '{subscription}'.")
        return True
    return False

def get_user_subscriptions(user_id: str) -> list:
    subscriptions = load_subscriptions()
    return subscriptions.get(user_id, [])

# ---------------------- Получение новостей ------------------------------------

def fetch_cryptopanic_news() -> list:
    if not CRYPTOPANIC_API_KEY:
        logger.warning("CRYPTOPANIC_API_KEY не задан.")
        return []
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
        results = data.get("results", [])
        logger.info(f"Получено {len(results)} новостей из CryptoPanic.")
        return results
    except Exception as e:
        logger.warning(f"Ошибка при запросе CryptoPanic: {e}")
        return []

def fetch_newsapi_news() -> list:
    if not NEWSAPI_API_KEY:
        logger.warning("NEWSAPI_API_KEY не задан.")
        return []
    base_url = "https://newsapi.org/v2/everything"
    params = {
        "q": "cryptocurrency OR bitcoin OR ethereum",
        "apiKey": NEWSAPI_API_KEY,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 50
    }
    try:
        resp = requests.get(base_url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        articles = data.get("articles", [])
        logger.info(f"Получено {len(articles)} статей из NewsAPI.")
        return articles
    except Exception as e:
        logger.warning(f"Ошибка при запросе NewsAPI: {e}")
        return []

def fetch_coindesk_news() -> list:
    feed_url = "https://feeds.feedburner.com/CoinDesk"
    try:
        feed = feedparser.parse(feed_url)
        entries = feed.entries
        logger.info(f"Получено {len(entries)} новостей из CoinDesk RSS.")
        return entries
    except Exception as e:
        logger.warning(f"Ошибка при запросе CoinDesk RSS: {e}")
        return []

def fetch_cointelegraph_news() -> list:
    feed_url = "https://cointelegraph.com/rss"
    try:
        feed = feedparser.parse(feed_url)
        entries = feed.entries
        logger.info(f"Получено {len(entries)} новостей из CoinTelegraph RSS.")
        return entries
    except Exception as e:
        logger.warning(f"Ошибка при запросе CoinTelegraph RSS: {e}")
        return []

def fetch_all_news() -> list:
    news = []

    cryptopanic_news = fetch_cryptopanic_news()
    news += cryptopanic_news

    newsapi_news = fetch_newsapi_news()
    news += newsapi_news

    coindesk_news = fetch_coindesk_news()
    news += coindesk_news

    cointelegraph_news = fetch_cointelegraph_news()
    news += cointelegraph_news

    logger.info(f"Всего получено {len(news)} новостей.")
    return news

# ---------------------- Анализ тональности ------------------------------------

def get_sentiment_label(text: str) -> str:
    scores = analyzer.polarity_scores(text)
    compound = scores['compound']
    if compound >= 0.05:
        return "POSITIVE"
    elif compound <= -0.05:
        return "NEGATIVE"
    else:
        return "NEUTRAL"

# ---------------------- Отправка сообщений -------------------------------------

def send_telegram_message(chat_id: str, text: str, parse_mode: str = ParseMode.HTML):
    max_length = 4096
    try:
        if len(text) <= max_length:
            bot_context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                disable_web_page_preview=True
            )
        else:
            # Разбиваем на части, если сообщение слишком длинное
            for i in range(0, len(text), max_length):
                chunk = text[i:i + max_length]
                bot_context.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode=parse_mode,
                    disable_web_page_preview=True
                )
    except Exception as e:
        logger.warning(f"Ошибка при отправке сообщения пользователю {chat_id}: {e}")

# ---------------------- Клавиатура (ReplyKeyboard) -----------------------------
def show_main_keyboard(update_or_context):
    """
    ВАЖНО: убираем кнопку Start, а также меняем эмодзи кнопок
    Так что в первой строке: News, Price, Volatility, Telegram
    Во второй строке: 🆘 Help, 🔔 Subscribe, 🔕 Unsubscribe
    """
    custom_keyboard = [
        ["📰 News", "💰 Price", "📈 Volatility", "📨 Telegram"],
        ["🆘 Help", "🔔 Subscribe", "🔕 Unsubscribe"]
    ]
    reply_markup = ReplyKeyboardMarkup(
        custom_keyboard,
        resize_keyboard=True,
        one_time_keyboard=False
    )

    if isinstance(update_or_context, Update):
        update_or_context.message.reply_text(
            "Выберите действие:",
            reply_markup=reply_markup
        )
    else:
        update_or_context.bot.send_message(
            chat_id=update_or_context.effective_chat.id,
            text="Выберите действие:",
            reply_markup=reply_markup
        )

# ---------------------- Обработчики команд -------------------------------------

def start_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    logger.info(f"Получена команда /start от пользователя {user_id}")
    welcome_text = (
        "Привет! Я *CryptoNewsBot*.\n\n"
        "Я помогу вам получать новости и отслеживать курсы криптовалют.\n"
        "Ниже — меню для управления (кнопки)."
    )
    update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)
    show_main_keyboard(update)

def help_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    logger.info(f"Получена команда /help от пользователя {user_id}")
    help_text = (
        "Доступные команды:\n\n"
        "• /start — запустить или перезапустить бота (без кнопки)\n"
        "• /help — список команд\n"
        "• /unsubscribe <coin> — отписка от конкретной криптовалюты\n\n"
        "Все названия криптовалют указывайте **латиницей**, "
        "в соответствии с CoinGecko ID (bitcoin, ethereum, solana и т.д.).\n"
        "Чтобы найти точное имя, смотрите в URL на CoinGecko или обращайтесь к списку монет."
    )
    update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    show_main_keyboard(update)

def unsubscribe_command(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    logger.info(f"Получена команда /unsubscribe от пользователя {user_id}")

    if len(context.args) == 0:
        subs = get_user_subscriptions(user_id)
        if subs:
            lines = [f"- {s}" for s in subs]
            text_subs = "Ваши активные подписки:\n" + "\n".join(lines)
            update.message.reply_text(
                f"{text_subs}\n\nВведите криптовалюту, от которой хотите отписаться (латиницей)."
            )
            context.user_data["awaiting_unsubscribe"] = True
        else:
            update.message.reply_text("У вас нет активных подписок.")
            show_main_keyboard(update)
        return

    subscription = context.args[0].lower()
    success = remove_subscription(user_id, subscription)
    if success:
        update.message.reply_text(f"Вы успешно отписались от {subscription}.")
    else:
        update.message.reply_text(f"Вы не были подписаны на {subscription}.")
    show_main_keyboard(update)

# ---------------------- Обработчик обычных сообщений (ReplyKeyboard) ----------

def handle_message(update: Update, context: CallbackContext):
    user_id_str = str(update.message.from_user.id)
    user_id_int = update.message.from_user.id
    text = update.message.text.strip().lower()

    if text == "📰 news":
        handle_news(update, context)
    elif text == "💰 price":
        handle_price(update, context)
    elif text == "📈 volatility":
        handle_volatility(update, context)
    elif text == "📨 telegram":
        if is_user_in_channel(context.bot, user_id_int, CHANNEL_ID):
            update.message.reply_text(
                f"Вы уже подписаны на канал!\nСсылка: {CHANNEL_INVITE_LINK}"
            )
            show_main_keyboard(update)
        else:
            prompt = (
                "Похоже, вы ещё не подписаны на наш канал.\n"
                "Хотите подписаться? Напишите 'Да' или 'Нет'."
            )
            update.message.reply_text(prompt)
            context.user_data["awaiting_channel_subscribe"] = True

    elif text == "🆘 help":
        help_command(update, context)

    elif text == "🔔 subscribe":
        subs = get_user_subscriptions(user_id_str)
        if subs:
            lines = [f"- {s}" for s in subs]
            msg = ("Ваши текущие подписки:\n" + "\n".join(lines) +
                   "\n\nВведите новые криптовалюты (через запятую), латиницей.\n"
                   "Например: `bitcoin, ethereum, solana`")
        else:
            msg = ("У вас пока нет подписок.\n"
                   "Введите криптовалюты (через запятую), латиницей.\n"
                   "Например: `bitcoin, ethereum, solana`")
        update.message.reply_text(msg)
        context.user_data["awaiting_subscribe"] = True

    elif text == "🔕 unsubscribe":
        subs = get_user_subscriptions(user_id_str)
        if subs:
            lines = [f"- {s}" for s in subs]
            text_subs = "Ваши активные подписки:\n" + "\n".join(lines)
            update.message.reply_text(
                f"{text_subs}\n\nВведите криптовалюту, от которой хотите отписаться (латиницей)."
            )
            context.user_data["awaiting_unsubscribe"] = True
        else:
            update.message.reply_text("У вас нет активных подписок.")
            show_main_keyboard(update)

    elif text == "start":
        # Команда /start без кнопки
        start_command(update, context)

    elif text == "help":
        # Команда /help без кнопки
        help_command(update, context)

    elif text == "unsubscribe":
        # Команда /unsubscribe без кнопки
        subs = get_user_subscriptions(user_id_str)
        if subs:
            lines = [f"- {s}" for s in subs]
            text_subs = "Ваши активные подписки:\n" + "\n".join(lines)
            update.message.reply_text(
                f"{text_subs}\n\nВведите криптовалюту, от которой хотите отписаться (латиницей)."
            )
            context.user_data["awaiting_unsubscribe"] = True
        else:
            update.message.reply_text("У вас нет активных подписок.")
            show_main_keyboard(update)

    else:
        # Состояния
        if context.user_data.get("awaiting_subscribe"):
            cryptos = [c.strip().lower() for c in update.message.text.split(",")]
            subscribed = []
            already = []
            invalid = []

            for crypto in cryptos:
                if not crypto:
                    continue
                if crypto not in SUPPORTED_COINS:
                    invalid.append(crypto)
                    continue
                if crypto in get_user_subscriptions(user_id_str):
                    already.append(crypto)
                    continue
                add_subscription(user_id_str, crypto)
                subscribed.append(crypto)

            msg_list = []
            if subscribed:
                msg_list.append("Вы успешно подписались на: " + ", ".join(subscribed))
            if already:
                msg_list.append("У вас уже есть подписка на: " + ", ".join(already))
            if invalid:
                msg_list.append(
                    "Не найдены: " + ", ".join(invalid) +
                    "\nПроверьте правильность написания (латиницей)."
                )

            if not msg_list:
                update.message.reply_text("Не распознаны корректные криптовалюты, попробуйте ещё раз.")
            else:
                update.message.reply_text("\n".join(msg_list))

            context.user_data["awaiting_subscribe"] = False
            show_main_keyboard(update)

        elif context.user_data.get("awaiting_unsubscribe"):
            subscription = update.message.text.strip().lower()
            success = remove_subscription(user_id_str, subscription)
            if success:
                update.message.reply_text(f"Вы успешно отписались от {subscription}.")
            else:
                update.message.reply_text(
                    f"Либо вы не были подписаны на '{subscription}',\n"
                    "либо название криптовалюты некорректно. "
                    "Проверьте написание и попробуйте ещё раз."
                )
            context.user_data["awaiting_unsubscribe"] = False
            show_main_keyboard(update)

        elif context.user_data.get("awaiting_channel_subscribe"):
            if text in ["да", "yes", "lf", "д"]:
                update.message.reply_text(
                    f"Отлично! Вот ссылка на канал:\n{CHANNEL_INVITE_LINK}\n\n"
                    "После вступления повторно нажмите кнопку Telegram,\n"
                    "чтобы бот увидел, что вы подписались."
                )
            else:
                update.message.reply_text("Хорошо, будем ждать вашего решения позже.")
            context.user_data["awaiting_channel_subscribe"] = False
            show_main_keyboard(update)

        else:
            update.message.reply_text("Не понял команду. Попробуйте воспользоваться кнопками ниже.")
            show_main_keyboard(update)

# ---------------------- Новая функция handle_volatility -----------------------
def handle_volatility(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    logger.info(f"Пользователь {user_id} нажал 'Volatility'.")

    subs = get_user_subscriptions(user_id)
    if not subs:
        update.message.reply_text(
            "У вас нет подписок на криптовалюты. Сначала подпишитесь (🔔 Subscribe)."
        )
        return

    # Запрашиваем у CoinGecko изменение цены за 1h,24h,7d,14d,30d
    subs_str = ",".join(subs)
    try:
        markets_data = cg.get_coins_markets(
            vs_currency="usd",
            ids=subs_str,
            price_change_percentage="1h,24h,7d,14d,30d"
        )
    except Exception as e:
        logger.warning(f"Ошибка при запросе /coins/markets: {e}")
        update.message.reply_text("Не удалось получить данные для расчёта волатильности.")
        return

    market_map = {}
    for item in markets_data:
        cid = item.get("id", "").lower()
        market_map[cid] = item

    lines = []
    for coin in subs:
        c = coin.lower()
        if c not in market_map:
            lines.append(f"{coin.capitalize()}: не найдены данные")
            continue

        data_item = market_map[c]
        ch_1h  = data_item.get("price_change_percentage_1h_in_currency", None)
        ch_24h = data_item.get("price_change_percentage_24h_in_currency", None)
        ch_7d  = data_item.get("price_change_percentage_7d_in_currency", None)
        ch_14d = data_item.get("price_change_percentage_14d_in_currency", None)
        ch_30d = data_item.get("price_change_percentage_30d_in_currency", None)

        def fmt(p):
            if p is None:
                return "n/a"
            return f"{p:+.2f}%"

        line = (
            f"{coin.capitalize()}:\n"
            f"  1h:  {fmt(ch_1h)}\n"
            f"  24h: {fmt(ch_24h)}\n"
            f"  7d:  {fmt(ch_7d)}\n"
            f"  14d: {fmt(ch_14d)}\n"
            f"  30d: {fmt(ch_30d)}"
        )
        lines.append(line)

    final_msg = "Динамика курсов (Volatility):\n\n" + "\n\n".join(lines)
    update.message.reply_text(final_msg)

# ---------------------- Логика кнопок: News, Price -----------------------------

def handle_news(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    logger.info(f"Пользователь {user_id} запросил News (вручную).")

    subscriptions = get_user_subscriptions(user_id)
    if not subscriptions:
        update.message.reply_text("У вас нет подписок. Сначала подпишитесь (🔔 Subscribe).")
        return

    all_news = fetch_all_news()
    if not all_news:
        update.message.reply_text("Не удалось получить новости в данный момент.")
        return

    user_news = []
    for item in all_news:
        title = ""
        url = ""
        if isinstance(item, dict):
            title = item.get("title", "").lower()
            url = item.get("url", "") or item.get("link", "")
        elif hasattr(item, 'title'):
            title = item.title.lower()
            url = item.link
        else:
            continue

        if any(re.search(rf"\b{re.escape(sub)}\b", title) for sub in subscriptions):
            user_news.append((title, url))

    if not user_news:
        update.message.reply_text("Нет актуальных новостей по вашим подпискам.")
        return

    for title, url in user_news:
        sentiment = get_sentiment_label(title)
        if sentiment == "POSITIVE":
            sentiment_text = "🟢 Positive"
        elif sentiment == "NEGATIVE":
            sentiment_text = "🔴 Negative"
        else:
            sentiment_text = "<i>Neutral</i>"

        msg_text = (
            f"<b>НОВОСТЬ:</b>\n{title}\n\n"
            f"<b>Сентимент:</b> {sentiment_text}\n\n"
            f"<a href='{url}'>Ссылка на источник</a>"
        )
        try:
            update.message.reply_text(
                msg_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.warning(f"Ошибка отправки новости пользователю {user_id}: {e}")

def handle_price(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    logger.info(f"Пользователь {user_id} запросил Price.")

    subs = get_user_subscriptions(user_id)
    if not subs:
        update.message.reply_text("У вас нет подписок. Сначала подпишитесь (🔔 Subscribe).")
        return

    subs = list(set(subs))
    try:
        prices = cg.get_price(ids=",".join(subs), vs_currencies="usd")
    except Exception as e:
        logger.warning(f"Ошибка при получении цены: {e}")
        update.message.reply_text("Произошла ошибка при получении данных.")
        return

    if not prices:
        update.message.reply_text("Не удалось получить цены. Проверьте названия криптовалют.")
        return

    lines = []
    for coin in subs:
        coin_lower = coin.lower()
        if coin_lower in prices:
            val = prices[coin_lower]["usd"]
            lines.append(f"{coin.capitalize()}: {val} USD")
        else:
            lines.append(f"{coin.capitalize()}: не найдена")

    update.message.reply_text("\n".join(lines))

# ---------------------- Рассылка новостей в канал -----------------------------

def process_and_send_news_to_channel(context: CallbackContext, all_news: list):
    logger.info(f"Отправка {len(all_news)} новостей в канал {CHANNEL_ID}.")
    for item in all_news:
        title = ""
        url = ""
        if isinstance(item, dict):
            title = item.get("title", "").lower()
            url = item.get("url", "") or item.get("link", "")
        elif hasattr(item, 'title'):
            title = item.title.lower()
            url = item.link
        else:
            continue

        sentiment = get_sentiment_label(title)
        if sentiment == "POSITIVE":
            sentiment_text = "🟢 Positive"
        elif sentiment == "NEGATIVE":
            sentiment_text = "🔴 Negative"
        else:
            sentiment_text = "<i>Neutral</i>"

        msg_text = (
            f"<b>НОВОСТЬ:</b>\n{title}\n\n"
            f"<b>Сентимент:</b> {sentiment_text}\n\n"
            f"<a href='{url}'>Ссылка на источник</a>"
        )
        try:
            context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=msg_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            time.sleep(2)
        except Exception as e:
            logger.warning(f"Не удалось отправить новость в канал: {e}")

# ---------------------- (Опционально) Проверка изменения цен -------------------

def load_price_thresholds() -> dict:
    thresholds = {}
    for crypto in ["bitcoin", "ethereum"]:
        key = f"PRICE_THRESHOLD_{crypto.upper()}"
        threshold = os.getenv(key, "")
        try:
            thresholds[crypto] = float(threshold)
        except ValueError:
            thresholds[crypto] = 5.0
    return thresholds

def load_previous_prices() -> dict:
    if not os.path.exists(PREVIOUS_PRICES_FILE):
        return {}
    try:
        with open(PREVIOUS_PRICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Не удалось прочитать {PREVIOUS_PRICES_FILE}: {e}")
        return {}

def save_previous_prices(prices: dict):
    try:
        with open(PREVIOUS_PRICES_FILE, "w", encoding="utf-8") as f:
            json.dump(prices, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.warning(f"Не удалось записать {PREVIOUS_PRICES_FILE}: {e}")

def fetch_crypto_prices(cryptos: list = None, vs_currency: str = "usd") -> dict:
    if not cryptos:
        cryptos = ["bitcoin", "ethereum"]
    try:
        prices = cg.get_price(ids=cryptos, vs_currencies=vs_currency)
        logger.info(f"Получены цены: {prices}")
        return prices
    except Exception as e:
        logger.warning(f"Ошибка при запросе цен CoinGecko: {e}")
        return {}

def check_price_changes(context: CallbackContext):
    thresholds = load_price_thresholds()
    current_prices = fetch_crypto_prices(list(thresholds.keys()))
    if not current_prices:
        logger.warning("Не удалось получить текущие цены для проверки.")
        return

    previous_prices = load_previous_prices()
    alerts = []

    for crypto, threshold in thresholds.items():
        now_price = current_prices.get(crypto, {}).get("usd")
        old_price = previous_prices.get(crypto)
        if now_price and old_price:
            change = (now_price - old_price) / old_price * 100
            if abs(change) >= threshold:
                direction = "↑" if change > 0 else "↓"
                alert = (
                    f"⚠️ Изменение цены {crypto.capitalize()}: {now_price:.2f} USD\n"
                    f"Изменение: {direction}{abs(change):.2f}%"
                )
                alerts.append(alert)

        # Обновляем «старую» цену
        if now_price:
            previous_prices[crypto] = now_price

    if alerts:
        logger.info(f"Отправка {len(alerts)} оповещений об изменении цен.")
        for a in alerts:
            context.bot.send_message(chat_id=CHANNEL_ID, text=a)
        save_previous_prices(previous_prices)
    else:
        logger.info("Нет значительных изменений цен.")

# ---------------------- Планировщик --------------------------------------------

def scheduled_tasks(updater: Updater):
    while True:
        try:
            logger.info("Запуск периодической задачи: получение новостей и проверка цен.")
            all_news = fetch_all_news()
            if all_news:
                process_and_send_news_to_channel(updater.dispatcher, all_news)

            check_price_changes(updater.dispatcher)

            logger.info(f"Задачи выполнены. Ждем {POLL_INTERVAL} секунд.")
            time.sleep(POLL_INTERVAL)
        except Exception as e:
            logger.error(f"Ошибка в планировщике: {e}", exc_info=True)
            time.sleep(60)

# ---------------------- main() ------------------------------------------------

def main():
    global bot_context
    # Сначала загружаем список поддерживаемых монет (CoinGecko)
    load_supported_coins()

    updater = Updater(token=TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher
    bot_context = dispatcher  # Сохраняем глобальный контекст для send_telegram_message

    # Команды
    dispatcher.add_handler(CommandHandler("start", start_command))
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(CommandHandler("unsubscribe", unsubscribe_command))

    # Обработчик обычного текста (кнопки ReplyKeyboard и т.п.)
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    # Запуск бота
    updater.start_polling()
    logger.info("Бот запущен и готов к работе.")

    # Запуск планировщика в отдельном потоке
    task_thread = threading.Thread(target=scheduled_tasks, args=(updater,), daemon=True)
    task_thread.start()

    updater.idle()

if __name__ == "__main__":
    main()
